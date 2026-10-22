"""Evidence-first claims ledger.

Every important claim about an offer gets exactly one status:

  verified      the vendor's own product page states it (snippet + URL + retrieval time attached)
  contradicted  the page states something different; the page value is what the rest of the system uses
  inferred      not on this page, but derived from evidence elsewhere (e.g. a sibling listing's promo cap)
  unknown       only the browsing agent reported it, or nobody did. Never shown as fact.

Built from the Fetch checks (verify.py) and page terms (adversarial.py). No LLM output is ever marked verified.
"""
from .models import Offer

LABELS = {"price": "Unit price", "ram_gb": "RAM (GB)", "storage_gb": "Storage (GB)", "thickness_mil": "Thickness (mil)",
          "count_per_case": "Pack size", "warranty": "Warranty", "stock": "Availability", "quantity": "Order limit",
          "shipping": "Delivery charge", "tax": "Tax treatment", "lead_time": "Delivery time"}
CRITICAL = ("price", "ram_gb", "storage_gb", "thickness_mil", "count_per_case", "warranty", "stock", "quantity",
            "shipping", "tax", "lead_time")


def _claim(o: Offer, field: str, status: str, claimed, observed, snippet: str = "", method: str = "",
           note: str = "") -> dict:
    v = o.verification or {}
    source = (v.get("limit") or {}).get("inferred_from") if status == "inferred" else None
    return {"field": field, "label": LABELS.get(field, field.replace("_", " ")), "claimed": claimed, "observed": observed,
            "status": status, "source_url": source or o.url, "snippet": snippet, "retrieved_at": v.get("retrieved_at") if status != "unknown" else None,
            "method": method, "uncertainty": note}


def build_claims(o: Offer) -> list[dict]:
    v = o.verification or {}
    t = o.terms or {}
    page_read = bool(v.get("retrieved_at") or v.get("checks")) and v.get("note") != "Product page could not be fetched"
    agent_only = "Reported by the browsing agent only; the product page did not confirm it."
    no_page = "The product page could not be fetched, so this is the agent's reading only."
    why_unknown = agent_only if page_read else no_page
    checks: dict[str, dict] = {}
    for c in v.get("checks", []):
        checks.setdefault(c["field"], c)  # first check per field is the direct one
    out = []

    # price
    c = checks.get("price")
    if c and c["ok"] is True:
        out.append(_claim(o, "price", "verified", o.unit_price, c["page"], c.get("snippet", ""), "price found on product page"))
    elif c and c["ok"] is False:
        out.append(_claim(o, "price", "contradicted", o.unit_price, c["page"], c.get("snippet", ""), "price not found on page",
                          c.get("note", "")))
    elif o.unit_price is not None:
        out.append(_claim(o, "price", "unknown", o.unit_price, None, "", "agent extraction",
                          "The page shows no readable prices (rendered client-side)." if c else why_unknown))

    # specs the buyer cares about
    for field in ("ram_gb", "storage_gb", "thickness_mil", "count_per_case"):
        c = checks.get(field)
        claimed = c["claimed"] if c else o.attributes.get(field)
        if c and c["ok"] is True:
            out.append(_claim(o, field, "verified", claimed, c["page"], c.get("snippet", ""), "spec read next to the price"))
        elif c and c["ok"] is False:
            out.append(_claim(o, field, "contradicted", claimed, c["page"], c.get("snippet", ""), "spec read next to the price",
                              "The page value replaced the agent's."))
        elif field == "count_per_case" and t.get("pack"):
            out.append(_claim(o, field, "verified", claimed, t["pack"]["count"], t["pack"]["snippet"], t["pack"]["method"]))
        elif claimed is not None:
            out.append(_claim(o, field, "unknown", claimed, None, "", "agent extraction",
                              why_unknown if page_read else no_page))

    # warranty
    c = checks.get("warranty")
    if c:
        out.append(_claim(o, "warranty", "verified" if c["ok"] else "contradicted", c["claimed"] or None, c["page"],
                          c.get("snippet", ""), "warranty line on page"))
    elif o.warranty.raw:
        out.append(_claim(o, "warranty", "unknown", o.warranty.raw, None, "", "agent extraction", why_unknown))
    else:
        out.append(_claim(o, "warranty", "unknown", None, None, "", "", "Neither the agent nor the page stated a warranty."))

    # stock
    c = checks.get("stock")
    if c:
        out.append(_claim(o, "stock", "contradicted" if c["ok"] is False else "verified", c["claimed"], c["page"],
                          c.get("snippet", ""), "availability text on page"))
    elif o.stock:
        out.append(_claim(o, "stock", "unknown", o.stock, None, "", "agent extraction", why_unknown))

    # order limit (promo cap)
    lim = v.get("limit")
    if lim and lim.get("inferred_from"):
        out.append(_claim(o, "quantity", "inferred", None, f"max {lim['max_units']} per order", lim.get("snippet", lim["text"]),
                          "same-model sibling listing at the same vendor",
                          "This page didn't render; the cap is assumed from a sibling listing of the same model."))
    elif lim:
        out.append(_claim(o, "quantity", "verified", None, f"max {lim['max_units']} per order", lim.get("snippet", lim["text"]),
                          "purchase-limit text on page"))

    # commercial terms: observed on the page, or unknown
    sh = t.get("shipping")
    out.append(_claim(o, "shipping", "verified", None, "free" if sh["kind"] == "free" else
                      f"free on orders over {sh['threshold']:g}" if sh["kind"] == "free_over" else f"{sh['amount']:g} per {sh['per']}",
                      sh["snippet"], "delivery text on page") if sh else
               _claim(o, "shipping", "unknown", None, None, "", "", "No delivery charge stated on the page."))
    tx = t.get("tax")
    out.append(_claim(o, "tax", "contradicted" if tx["inclusion"] == "conflicting" else "verified", None,
                      tx["inclusion"], tx["snippet"], "tax wording on page") if tx else
               _claim(o, "tax", "unknown", None, None, "", "", "The page doesn't say whether tax is included."))
    lt = t.get("lead_time")
    out.append(_claim(o, "lead_time", "verified", None, f"{lt['kind']} in {lt['min_days']}–{lt['max_days']} days",
                      lt["snippet"], "lead-time text on page") if lt else
               _claim(o, "lead_time", "unknown", None, None, "", "", "No delivery time stated on the page."))
    return out


def coverage(claims: list[dict]) -> dict:
    """Share of critical claims in each state, for the risk radar and the UI."""
    crit = [c for c in claims if c["field"] in CRITICAL]
    n = len(crit) or 1
    counts = {k: sum(c["status"] == k for c in crit) for k in ("verified", "contradicted", "inferred", "unknown")}
    return {**counts, "total": len(crit), "verified_share": round(counts["verified"] / n, 3)}
