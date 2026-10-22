"""Landed cost: what the order will actually cost to receive, line by line.

  goods      quantity × unit price, split into promo-capped and list-price lines when a page caps the promo
  discount   volume discounts stated on the page, applied only when the quantity qualifies
  delivery   stated charge, stated free, or unknown
  fees       handling / installation / convenience fees stated on the page
  tax        GST (India) or sales tax (US). If the page says the price includes tax, the included amount is shown
             but never added again. If inclusion isn't stated: India assumes GST is extra (upper bound, labelled);
             US sales tax stays unknown unless the buyer gives a rate (it depends on ship-to state and exemptions).

Every line has a status: confirmed (stated on the vendor's page), estimated (a stated assumption or an agent
reading the page didn't confirm), or unknown (no figure, excluded from the total and listed). Decimal arithmetic,
rounded to the cent/paisa per line.
"""
from decimal import ROUND_HALF_UP, Decimal

from .models import Offer, Requirements

CENT = Decimal("0.01")


def _d(x) -> Decimal:
    return Decimal(str(x))


def _r(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def _price_status(o: Offer) -> tuple[str, str]:
    c = next((c for c in o.claims if c["field"] == "price"), None)
    if c and c["status"] == "verified":
        return "confirmed", "price shown on the vendor's product page"
    if c and c["status"] == "contradicted":
        return "estimated", "agent-reported price; the product page shows a different figure"
    return "estimated", "agent-reported price; not confirmed on the product page"


def landed_cost(o: Offer, req: Requirements, tax_cfg: dict | None = None) -> dict:
    q = req.quantity
    cur = req.currency
    tax_cfg = tax_cfg or {}
    lines: list[dict] = []

    def add(key, label, amount, status, basis, evidence="", included=False):
        lines.append({"key": key, "label": label, "amount": float(_r(amount)) if amount is not None else None,
                      "status": status, "basis": basis, "evidence": evidence, "included_in_price": included})

    if o.unit_price is None:
        return {"currency": cur, "quantity": q, "lines": [], "total": None, "total_confirmed": None, "per_unit": None,
                "unknowns": ["unit price"], "confidence": "incomplete", "goods_subtotal": None}

    unit = _d(o.unit_price)
    pstatus, pbasis = _price_status(o)
    lim = (o.verification or {}).get("limit")
    if lim and lim["max_units"] < q:
        cap = lim["max_units"]
        add("goods_promo", f"{cap} × promo price {unit:,.2f}", unit * cap, pstatus, pbasis + f"; promo capped at {cap}/order",
            lim.get("snippet", lim.get("text", "")))
        rest = q - cap
        if lim.get("list_price") and not lim.get("inferred_from"):
            add("goods_list", f"{rest} × list price {_d(lim['list_price']):,.2f}", _d(lim["list_price"]) * rest, "confirmed",
                "list (struck-through) price on the product page", lim.get("snippet", ""))
        elif lim.get("est_list_price"):
            add("goods_list", f"{rest} × estimated list price {_d(lim['est_list_price']):,.2f}",
                _d(lim["est_list_price"]) * rest, "estimated",
                "list price estimated from a sibling listing's promo/list ratio", lim.get("snippet", ""))
        else:
            add("goods_list", f"{rest} units beyond the promo cap", None, "unknown",
                "the page doesn't show the regular price for units beyond the cap; request a quote")
    else:
        add("goods", f"{q} × {unit:,.2f}", unit * q, pstatus, pbasis)
    goods = sum((_d(l["amount"]) for l in lines if l["amount"] is not None), Decimal(0))

    t = o.terms or {}
    disc = t.get("discount")
    if disc and q >= disc.get("min_units", 0):
        add("discount", f"Volume discount {disc['pct']:g}%", -goods * _d(disc["pct"]) / 100, "confirmed",
            f"stated on page for {disc.get('min_units', 0)}+ units", disc.get("snippet", ""))
    elif disc:
        add("discount", f"Volume discount {disc['pct']:g}% (needs {disc['min_units']}+ units)", Decimal(0), "confirmed",
            "your quantity doesn't qualify", disc.get("snippet", ""))

    sh = t.get("shipping")
    if sh and sh["kind"] == "free_over":
        if goods >= _d(sh["threshold"]):
            add("delivery", "Delivery", Decimal(0), "confirmed",
                f"page states free delivery on orders over {sh['threshold']:g}; this order qualifies", sh["snippet"])
        else:
            add("delivery", "Delivery", None, "unknown",
                f"free only on orders over {sh['threshold']:g}; the charge below that isn't stated", sh["snippet"])
    elif sh and sh["kind"] == "free":
        add("delivery", "Delivery", Decimal(0), "confirmed", "page states free delivery", sh["snippet"])
    elif sh:
        amt = _d(sh["amount"]) * (q if sh["per"] == "unit" else 1)
        add("delivery", f"Delivery ({sh['amount']:g} per {sh['per']})", amt, "confirmed", "delivery charge stated on page",
            sh["snippet"])
    else:
        add("delivery", "Delivery", None, "unknown", "no delivery charge stated on the page")

    for f in t.get("fees", []):
        amt = _d(f["amount"]) * (q if f["per"] == "unit" else 1)
        add("fee", f"{f['label'].capitalize()} ({f['amount']:g} per {f['per']})", amt, "confirmed", "stated on page",
            f["snippet"])

    pre_tax = sum((_d(l["amount"]) for l in lines if l["amount"] is not None and not l["included_in_price"]), Decimal(0))
    tx = t.get("tax") or {}
    inclusion = tx.get("inclusion", "unknown")
    rate, rate_basis = None, ""
    if req.tax_rate_pct is not None:
        rate, rate_basis = _d(req.tax_rate_pct), f"{req.tax_rate_pct:g}% rate supplied by the buyer"
    elif tax_cfg.get("rate_pct") is not None:
        rate, rate_basis = _d(tax_cfg["rate_pct"]), tax_cfg.get("source", f"{tax_cfg['rate_pct']}% configured rate")
    name = "GST" if tax_cfg.get("kind") == "gst" or cur == "INR" else "Sales tax"
    split = None
    if name == "GST":
        split = ("CGST + SGST (same-state supply) or IGST (inter-state): depends on the supplier's GSTIN state"
                 + (f" vs ship-to {req.ship_to_state}" if req.ship_to_state else "; set the ship-to state"))

    if inclusion == "inclusive":
        if rate is not None:
            included = pre_tax - pre_tax / (1 + rate / 100)
            add("tax", f"{name} {rate:g}% (included in price)", included, "confirmed",
                f"page states prices include tax; {rate_basis}. Shown for the invoice, not added again", tx.get("snippet", ""),
                included=True)
        else:
            add("tax", f"{name} (included in price)", Decimal(0), "confirmed", "page states prices include tax",
                tx.get("snippet", ""), included=True)
    elif rate is None:
        add("tax", name, None, "unknown",
            "sales tax depends on the ship-to state and exemption status; enter a rate to estimate it"
            if name == "Sales tax" else "no GST rate configured for this item; enter the rate from the HSN schedule")
    else:
        base_note = "page states tax is extra" if inclusion == "exclusive" else (
            "page is inconsistent about tax; assumed extra (upper bound)" if inclusion == "conflicting" else
            "page doesn't say whether tax is included; assumed extra (upper bound)")
        add("tax", f"{name} {rate:g}%", pre_tax * rate / 100, "estimated", f"{base_note}; {rate_basis}", tx.get("snippet", ""))

    counted = [l for l in lines if l["amount"] is not None and not l["included_in_price"]]
    total = sum((_d(l["amount"]) for l in counted), Decimal(0))
    confirmed = sum((_d(l["amount"]) for l in counted if l["status"] == "confirmed"), Decimal(0))
    unknowns = [l["label"] for l in lines if l["amount"] is None]
    statuses = {l["status"] for l in counted}
    confidence = "incomplete" if unknowns else ("confirmed" if statuses <= {"confirmed"} else "estimated")
    return {"currency": cur, "quantity": q, "lines": lines, "goods_subtotal": float(_r(goods)),
            "pre_tax": float(_r(pre_tax)), "total": float(_r(total)), "total_confirmed": float(_r(confirmed)),
            "per_unit": float(_r(total / q)), "unknowns": unknowns, "confidence": confidence,
            "tax": {"name": name, "rate_pct": float(rate) if rate is not None else None, "inclusion": inclusion,
                    "split": split}}
