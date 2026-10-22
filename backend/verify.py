"""Second-source verification with the TinyFish Fetch API.

A browsing agent can misread a configurator or mix up two listings. Before anything is ranked, every product
page the agents cited is fetched independently and the extracted claims are checked against the page text:

  price      the claimed price must appear on the page (else: price changed / wrong listing)
  specs      RAM / storage / thickness stated next to that price must match the claim; the page wins
  warranty   the page's own warranty line is captured as evidence (and fills gaps)
  stock      "Sold Out" / "Backordered" on the page overrides the agent
  limits     "Max N units per order" promos are detected; units beyond the limit are costed at list price

Every check keeps a short snippet of the page text it relied on. evidence.py turns the checks into the claims ledger
(verified / contradicted / inferred / unknown) and adversarial.py reads commercial terms and red flags off the page.
"""
import re

from . import adversarial, evidence, tinyfish
from .ml.normalize import parse_warranty
from .models import Offer

# $1,299.00 · ₹1,23,456 · Rs. 54,990 · INR 54990
CUR = r"(?:\$|₹|\bRs\.?|\bINR)"
PRICE = re.compile(CUR + r"\s?(\d{1,3}(?:,\d{2,3})+(?:\.\d{2})?|\d+(?:\.\d{2})?)")
STRIKE = re.compile(r"~~\s*" + CUR + r"\s?([\d,]+(?:\.\d{2})?)\s*~~")
LIMIT = re.compile(r"(?i)(?:max(?:imum)?\.?|limit(?:ed to)?)\s*(\d+)\s*(?:units?|items?|per)\b[^\n]{0,20}?(?:per\s+(?:order|customer))?")
WARRANTY = re.compile(r"(?im)^[*\s]*(?:base\s+)?warranty\s*\**\s*\n+\s*([^\n]{3,140})|\|\s*warranty\s*\|\s*([^|\n]{3,140}?)\s*\||"
                      r"^\s*(\d+\s*years?\b[^\n]{0,80}warranty[^\n]{0,40})\n+\s*included\b|"
                      r"^[*\s]*(?:base\s+)?warranty\s*:\s*([^\n]{3,140})")
MEMORY = re.compile(r"(?i)(?:\bmemory\s*\**\s*\|?\s*(?:\n\s*)?(\d+)\s*GB|(\d+)\s*GB[, ]+(?:\dx\d+GB, )?(?:LP)?DDR\d)")
STORAGE = re.compile(r"(?i)\bstorage(?: capacity)?\s*\**\s*\|?\s*(?:\n\s*)?(\d+)\s*(GB|TB)")
THICK = re.compile(r"(?i)(?:finger|thickness)\s*:?\s*([\d.]+)\s*mil|([\d.]+)\s*mil\s+(?:industrial-strength\s+)?thickness")
IN_STOCK = re.compile(r"(?i)\b(in stock|ships today|available to ship|available for delivery)\b")
SOLD_OUT = re.compile(r"(?i)\b(sold out|out of stock|backordered item|backordered|currently unavailable)\b")


def _amounts(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in PRICE.findall(text)]


def snippet(text: str, start: int, end: int | None = None, pad: int = 70) -> str:
    """The page text around a match, whitespace-collapsed: what a reviewer needs to see to trust the check."""
    end = start if end is None else end
    s = text[max(0, start - pad): end + pad]
    return ("…" if start > pad else "") + re.sub(r"\s+", " ", s).strip() + ("…" if end + pad < len(text) else "")


def _find(text: str, price: float):
    for m in PRICE.finditer(text):
        if abs(float(m.group(1).replace(",", "")) - price) <= max(0.01, price * 0.005):
            return m
    return None


def _near_price(text: str, price: float, span: int = 700) -> str:
    """Text following each occurrence of the claimed price — the configuration block that price belongs to."""
    chunks = []
    for m in PRICE.finditer(text):
        if abs(float(m.group(1).replace(",", "")) - price) <= max(0.01, price * 0.002):
            chunks.append(text[m.end(): m.end() + span])
    return "\n".join(chunks)


def _configurable(rx: re.Pattern, text: str) -> bool:
    """True when a spec label is followed by a list of options (a configurator), e.g. 'Internal storage / 1 TB / 256 GB / 512 GB'."""
    for m in rx.finditer(text):
        window = text[m.start(): m.start() + 320]
        opts = {float(n) * (1024 if u.upper() == "TB" else 1) for n, u in re.findall(r"(\d+)\s*(GB|TB)\b", window, re.I)}
        if len(opts) > 1:
            return True
    return False


def _values(rx: re.Pattern, text: str) -> list[float]:
    """All values a spec regex finds, in document order, normalised (TB → GB)."""
    out = []
    for m in rx.finditer(text):
        groups = [g for g in m.groups() if g]
        val = float(groups[0])
        if len(groups) > 1 and groups[1].upper() == "TB":
            val *= 1024
        out.append(val)
    return out


def verify_offer(o: Offer, page: dict | None, quantity: int) -> dict:
    v = {"status": "unverified", "source_url": o.url, "checks": [], "corrections": []}
    if not page or not page.get("text"):
        v["note"] = "Product page could not be fetched"
        return v
    text = page["text"]
    checks = v["checks"]

    # 1. price
    # Only amounts in a plausible band count as "the page's price" — not $0.00 placeholders, accessories or promos.
    amounts = [a for a in _amounts(text) if o.unit_price and 0.4 * o.unit_price <= a <= 2.5 * o.unit_price] \
        if o.unit_price else []
    if o.unit_price and amounts:
        hit = any(abs(a - o.unit_price) <= max(0.01, o.unit_price * 0.005) for a in amounts)
        if hit:
            m = _find(text, o.unit_price)
            checks.append({"field": "price", "ok": True, "claimed": o.unit_price, "page": o.unit_price,
                           "snippet": snippet(text, m.start(), m.end()) if m else ""})
        else:
            near = sorted({a for a in amounts if a >= o.unit_price * 0.05}, key=lambda a: abs(a - o.unit_price))[:3]
            m = _find(text, near[0]) if near else None
            checks.append({"field": "price", "ok": False, "claimed": o.unit_price, "page": near,
                           "note": "claimed price not on page (price changed, different config, or different pack size)",
                           "snippet": snippet(text, m.start(), m.end()) if m else ""})
    elif o.unit_price:
        checks.append({"field": "price", "ok": None, "claimed": o.unit_price, "note": "page shows no prices (rendered client-side)"})

    # 2. specs at that price point (page is authoritative). Without a price anchor, only trust a spec the
    #    whole page agrees on (configurator pages list several configs).
    ctx = _near_price(text, o.unit_price) if o.unit_price else ""
    for field, rx in (("ram_gb", MEMORY), ("storage_gb", STORAGE), ("thickness_mil", THICK)):
        claimed = o.attributes.get(field)
        if claimed is None:
            continue
        near = _values(rx, ctx)
        page_val = near[0] if near else None
        if page_val is None and not _configurable(rx, text):
            allv = set(_values(rx, text))
            page_val = allv.pop() if len(allv) == 1 else None
        if page_val is None:
            continue
        ok = abs(page_val - claimed) < 1e-6
        sm = next((m for m in rx.finditer(ctx or text)), None)
        checks.append({"field": field, "ok": ok, "claimed": claimed, "page": page_val,
                       "snippet": snippet(ctx or text, sm.start(), sm.end()) if sm else ""})
        if not ok:
            o.attributes[field] = page_val
            v["corrections"].append(f"{field}: {claimed:g} → {page_val:g} (from product page at this price)")

    # 3. warranty line on the page
    m = WARRANTY.search(text)
    if m:
        page_w = next(g for g in m.groups() if g).strip().replace("\xa0", " ")
        pw = parse_warranty(page_w)
        years_conflict = (o.warranty.years is not None and pw.years is not None and abs(o.warranty.years - pw.years) > 0.01)
        svc_conflict = (o.warranty.service not in ("unknown",) and pw.service != "unknown" and pw.service != o.warranty.service)
        checks.append({"field": "warranty", "ok": not (years_conflict or svc_conflict),
                       "claimed": o.warranty.raw, "page": page_w, "snippet": snippet(text, m.start(), m.end())})
        if (years_conflict or svc_conflict) and (pw.years is not None or pw.service != "unknown"):
            # The page wins: cost and risk use the warranty the vendor's own page states.
            before = o.warranty.raw
            o.warranty = parse_warranty(page_w)
            o.warranty.raw = f"{page_w} (from product page)"
            o.attributes["warranty_years"] = o.warranty.years or 0.0
            o.attributes["warranty_onsite"] = 1.0 if o.warranty.service == "onsite" else 0.0
            v["corrections"].append(f"warranty: '{before}' → '{page_w}' (product page)")
        elif o.warranty.service == "unknown" and pw.service != "unknown":
            o.warranty.service = pw.service
            o.attributes["warranty_onsite"] = 1.0 if pw.service == "onsite" else 0.0
            v["corrections"].append(f"warranty service: {pw.service} (from product page)")
        if o.warranty.years is None and pw.years is not None:
            o.warranty.years = pw.years
            o.warranty.raw = f"{page_w} (from product page)"
            o.attributes["warranty_years"] = pw.years
            v["corrections"].append(f"warranty: {pw.years:g} yr included per product page (agent reported '{o.evidence.get('warranty', '')}')")
        o.evidence["warranty_on_page"] = page_w

    # 4. stock
    src = ctx[:250] if ctx else text
    m = SOLD_OUT.search(src)
    if m and "backorder" not in o.stock.lower() and "sold out" not in o.stock.lower():
        checks.append({"field": "stock", "ok": False, "claimed": o.stock or "n/a", "page": m.group(1),
                       "snippet": snippet(src, m.start(), m.end())})
        o.stock = m.group(1).title()
    elif m:
        checks.append({"field": "stock", "ok": True, "claimed": o.stock, "page": m.group(1),
                       "snippet": snippet(src, m.start(), m.end())})
    elif (m := IN_STOCK.search(src)) and not re.search(r"(?i)backorder|sold out|out of stock|unavailable", o.stock or ""):
        checks.append({"field": "stock", "ok": True, "claimed": o.stock or "n/a", "page": m.group(1),
                       "snippet": snippet(src, m.start(), m.end())})

    # 5. purchase limits
    m = LIMIT.search(text)
    if m:
        limit = int(m.group(1))
        list_price = None
        strikes = [float(x.replace(",", "")) for x in STRIKE.findall(text)]
        if o.unit_price and strikes:
            above = [s for s in strikes if s > o.unit_price]
            list_price = min(above) if above else None
        v["limit"] = {"max_units": limit, "text": m.group(0).strip(), "list_price": list_price,
                      "snippet": snippet(text, m.start(), m.end())}
        if limit < quantity:
            checks.append({"field": "quantity", "ok": False, "claimed": quantity, "page": f"max {limit} per order",
                           "note": "promo price only covers part of the order", "snippet": v["limit"]["snippet"]})
            if list_price:
                eff = (limit * o.unit_price + (quantity - limit) * list_price) / quantity
                o.effective_unit_price = round(eff, 2)
                v["corrections"].append(f"effective unit price ${eff:,.0f}: {limit} at promo ${o.unit_price:,.0f}, "
                                        f"{quantity - limit} at list ${list_price:,.0f}")

    oks = [c["ok"] for c in checks if c["ok"] is not None]
    price_unknown = any(c["field"] == "price" and c["ok"] is None for c in checks)
    if any(c is False for c in oks):
        v["status"] = "mismatch"
    elif oks:
        v["status"] = "partial" if price_unknown else "verified"
    return v


def propagate_limits(offers: list[Offer], quantity: int) -> int:
    """An unverifiable listing whose same-model sibling at the same vendor is promo-capped is assumed capped too
    (vendors run promos per model line). The effective price is estimated with the sibling's list/promo ratio."""
    from .ml.matching import _family
    capped = [o for o in offers if o.verification.get("limit") and o.verification["limit"]["max_units"] < quantity]
    n = 0
    for o in offers:
        if o.verification.get("status") != "unverified" or not o.unit_price:
            continue
        sib = next((c for c in capped if c.vendor == o.vendor and _family(c.name) == _family(o.name)), None)
        if not sib:
            continue
        lim = sib.verification["limit"]
        o.verification["limit"] = {**lim, "inferred_from": sib.url}
        o.verification["checks"].append({"field": "quantity", "ok": False, "claimed": quantity,
                                         "page": f"max {lim['max_units']} per order (inferred from sibling listing)"})
        if lim.get("list_price"):
            est_list = o.unit_price * lim["list_price"] / sib.unit_price
            o.verification["limit"]["est_list_price"] = round(est_list, 2)
            o.effective_unit_price = round((lim["max_units"] * o.unit_price + (quantity - lim["max_units"]) * est_list) / quantity, 2)
            o.verification["corrections"].append(
                f"effective unit price ≈ ${o.effective_unit_price:,.0f}: sibling listing {sib.name.split(' (')[0]} is capped at "
                f"{lim['max_units']} units/order; list price estimated at ${est_list:,.0f}")
        o.verification["status"] = "mismatch"
        n += 1
    return n


async def verify_offers(offers: list[Offer], domain: str, mode: str, quantity: int) -> dict:
    pages = await tinyfish.fetch_pages([o.url for o in offers if o.url], domain, mode)
    return verify_with_pages(offers, pages, quantity)


def verify_with_pages(offers: list[Offer], pages: dict, quantity: int) -> dict:
    """Verification is pure given the fetched pages, so tests and evals can call it with fixture pages directly."""
    summary = {"pages_fetched": len(pages), "verified": 0, "partial": 0, "mismatch": 0, "unverified": 0, "corrections": 0}
    for o in offers:
        page = pages.get(o.url)
        o.verification = verify_offer(o, page, quantity)
        if page:
            o.verification["retrieved_at"] = page.get("fetched_at")
            o.verification["page_provenance"] = page.get("provenance", {})
            o.terms = adversarial.extract_terms(page.get("text", ""), o)
            adversarial.check_unit_basis(o, page.get("text", ""))
    summary["inferred_limits"] = propagate_limits(offers, quantity)
    for o in offers:
        o.claims = evidence.build_claims(o)
        o.flags = adversarial.static_flags(o)
        summary["corrections"] += len(o.verification["corrections"])
    summary.update({k: sum(o.verification["status"] == k for o in offers)
                    for k in ("verified", "partial", "mismatch", "unverified")})
    summary["claims"] = {k: sum(c["status"] == k for o in offers for c in o.claims)
                         for k in ("verified", "contradicted", "inferred", "unknown")}
    summary["flags"] = sum(1 for o in offers for f in o.flags if f["severity"] in ("high", "medium"))
    return summary
