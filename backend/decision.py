"""Decision agent: a fixed, evidence-based policy over the ranked offers. No LLM and no voting.

Walk the ranking from the top. The first offer that clears every rule is recommended:

  1. meets every must-have, including the landed-cost budget and the delivery deadline (it is ranked)
  2. its price is confirmed on the vendor's own product page
  3. every must-have spec is confirmed (none is merely 'unknown')
  4. no blocking finding: unavailable, too little stock, promo capped below the quantity, unit-basis mismatch,
     warranty contradicted by the page, or a page that tries to instruct AI agents

The highest-scoring offer that failed a rule becomes the 'value pick': worth a written quote, not a purchase.
If nothing clears the rules, the top-ranked offer is returned as a provisional recommendation that needs a quote.
Every skipped offer carries the reasons, so the brief can say exactly why the cheapest-looking offer lost.
"""
from .models import Offer

BLOCKING = {"OUT_OF_STOCK": "listed as unavailable", "STOCK_BELOW_QUANTITY": "not enough stock for the quantity",
            "BULK_CAP": "promo price capped below your quantity", "UNIT_BASIS_MISMATCH": "priced on a different unit basis",
            "WARRANTY_CONFLICT": "warranty contradicted by the product page",
            "PAGE_INJECTION": "page contains instructions aimed at AI agents"}
POLICY = ["Meets every must-have, the landed-cost budget and the deadline",
          "Price confirmed on the vendor's own product page",
          "Every must-have spec confirmed (none unknown)",
          "No blocking finding: " + ", ".join(BLOCKING.values())]
PART_LABELS = {"fit": "requirement fit", "value": "lifetime cost", "risk": "supplier risk", "anomaly": "price sanity",
               "trust": "source trust"}


def blockers(o: Offer) -> list[str]:
    reasons = []
    price = next((c for c in o.claims if c["field"] == "price"), None)
    if not price or price["status"] != "verified":
        reasons.append("price not confirmed on the product page"
                       + (f" (page shows {price['observed']})" if price and price["status"] == "contradicted" else ""))
    unconfirmed = [f.split(": ", 1)[1] for f in o.failed_constraints if f.startswith("unverified")]
    if unconfirmed:
        reasons.append("must-have not confirmed: " + ", ".join(unconfirmed))
    seen = set()
    for f in o.flags:
        if f["code"] in BLOCKING and f["code"] not in seen:
            seen.add(f["code"])
            reasons.append(BLOCKING[f["code"]])
    return reasons


def why_beats(a: Offer, b: Offer) -> str:
    """'leads on lifetime cost (+12.0) and supplier risk (+3.1)': the score terms where a beats b."""
    deltas = sorted(((k, a.score_parts.get(k, 0) - b.score_parts.get(k, 0)) for k in PART_LABELS), key=lambda kv: -kv[1])
    lead = [f"{PART_LABELS[k]} (+{d:.1f})" for k, d in deltas if d > 0.05][:2]
    lag = [f"{PART_LABELS[k]} ({d:.1f})" for k, d in deltas if d < -0.05][-1:]
    s = "leads on " + " and ".join(lead) if lead else "scores the same on every term"
    return s + (f", trails on {lag[0]}" if lag else "")


def decide(ranked: list[Offer]) -> dict:
    eligible = [o for o in ranked if o.rank]
    trace, best = [], None
    for o in eligible:
        reasons = blockers(o)
        if not reasons:
            best = o
            trace.append({"offer_id": o.id, "name": o.name, "vendor": o.vendor, "rank": o.rank, "score": o.score,
                          "outcome": "recommended", "reasons": ["Clears every rule"]})
            break
        trace.append({"offer_id": o.id, "name": o.name, "vendor": o.vendor, "rank": o.rank, "score": o.score,
                      "outcome": "skipped", "reasons": reasons})
    provisional = False
    if best is None and eligible:
        best, provisional = eligible[0], True
        trace[0]["outcome"] = "provisional"
    value = eligible[0] if eligible and eligible[0] is not best else None
    why = []
    if best:
        if provisional:
            why.append("No offer cleared every rule; this is the top-ranked offer and needs a written quote before approval")
        else:
            why.append("Highest-ranked offer that clears every rule")
        lc = best.landed or {}
        if lc.get("confidence"):
            why.append(f"Landed cost is {lc['confidence']}" + (f" (unknown: {', '.join(lc['unknowns'])})" if lc.get("unknowns") else ""))
        if best.risk.get("overall") is not None:
            why.append(f"Supplier risk {best.risk['level']}")
        runner = next((o for o in eligible if o is not best and o is not value), None)
        if runner:
            why.append(f"Over runner-up {runner.vendor} {runner.name[:50]}: {why_beats(best, runner)}")
        if value:
            why.append(f"{value.vendor} {value.name[:50]} scores higher ({value.score:.0f} vs {best.score:.0f}) but "
                       + "; ".join(blockers(value)))
    return {"recommended_offer_id": best.id if best else None, "value_pick_id": value.id if value else None,
            "provisional": provisional, "policy": POLICY, "trace": trace, "why": why}
