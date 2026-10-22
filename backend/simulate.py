"""Counterfactual procurement simulator.

Change the budget, deadline, quantity, tax rate, must-haves/preferences or ranking weights, and re-run the exact same
analysis (engine.analyze) on the exact same verified offers. The baseline is recomputed with the same engine from
the mission's own requirements, so every difference is caused by the scenario and can be explained:

  * eligibility changes name the constraint that started or stopped failing
  * recommendation changes name the rule that blocked or unblocked an offer
  * score changes are decomposed into the five weighted terms
"""
from .decision import PART_LABELS, blockers, why_beats
from .engine import analyze
from .exports import money
from .models import Constraint, Offer, Requirements
from .scoring import WEIGHTS, normalize_weights

SCALARS = ("budget_total", "deadline_days", "quantity", "tax_rate_pct")


def apply_scenario(req: Requirements, scenario: dict) -> Requirements:
    data = req.model_dump()
    for k in SCALARS:
        if k in scenario:
            data[k] = scenario[k]
    if scenario.get("quantity") is not None:
        data["quantity"] = max(1, int(scenario["quantity"]))
    if "constraints" in scenario and scenario["constraints"] is not None:
        data["constraints"] = [Constraint(**c).model_dump() for c in scenario["constraints"]]
    if data.get("budget_total") and data["quantity"]:
        data["max_unit_price"] = round(data["budget_total"] / data["quantity"], 2)
    elif "budget_total" in scenario and not data.get("budget_total"):
        data["max_unit_price"] = None
    return Requirements(**data)


def _fmt_c(c: Constraint) -> str:
    v = f"{c.value:g}" if isinstance(c.value, float) else str(c.value)
    return f"{c.attribute} {c.op} {v} ({'must-have' if c.hard else f'preference ×{c.weight:g}'})"


def requirement_changes(a: Requirements, b: Requirements, wa: dict | None, wb: dict | None) -> list[str]:
    out = []
    labels = {"budget_total": "Budget", "deadline_days": "Deadline (days)", "quantity": "Quantity", "tax_rate_pct": "Tax rate %"}
    fmt = lambda k, v: "none" if v is None else money(v, b.currency, 0) if k == "budget_total" else f"{v:g}"
    for k, label in labels.items():
        va, vb = getattr(a, k), getattr(b, k)
        if va != vb:
            out.append(f"{label}: {fmt(k, va)} → {fmt(k, vb)}")
    ca, cb = {c.attribute: c for c in a.constraints}, {c.attribute: c for c in b.constraints}
    for attr in sorted(set(ca) | set(cb)):
        if attr not in cb:
            out.append(f"Removed {_fmt_c(ca[attr])}")
        elif attr not in ca:
            out.append(f"Added {_fmt_c(cb[attr])}")
        elif ca[attr].model_dump() != cb[attr].model_dump():
            out.append(f"{_fmt_c(ca[attr])} → {_fmt_c(cb[attr])}")
    if wb and wa != wb:
        out.append("Ranking weights: " + ", ".join(f"{PART_LABELS[k]} {round(100 * wb[k])}%" for k in PART_LABELS))
    return out


def _state(o: Offer, decision: dict) -> dict:
    t = next((t for t in decision["trace"] if t["offer_id"] == o.id), None)
    return {"rank": o.rank, "score": o.score, "eligible": bool(o.rank), "landed_total": (o.landed or {}).get("total"),
            "failed": [f for f in o.failed_constraints if not f.startswith("unverified")],
            "outcome": t["outcome"] if t else ("excluded" if not o.rank else "not reached"),
            "blockers": blockers(o) if o.rank else []}


def _offer_reasons(b: Offer, a: Offer, sb: dict, sa: dict, cur: str, weights_changed: bool) -> list[str]:
    out = []
    if sb["eligible"] and not sa["eligible"]:
        new = [f for f in sa["failed"] if f not in sb["failed"]] or sa["failed"]
        out.append("Now excluded: " + "; ".join(new))
    elif sa["eligible"] and not sb["eligible"]:
        gone = [f for f in sb["failed"] if f not in sa["failed"]] or sb["failed"]
        out.append("Now eligible: no longer fails " + "; ".join(gone))
    if sa["landed_total"] != sb["landed_total"] and sa["landed_total"] is not None and sb["landed_total"] is not None:
        out.append(f"Landed total {money(sb['landed_total'], cur, 0)} → {money(sa['landed_total'], cur, 0)}")
    if sa["eligible"] and sb["eligible"]:
        deltas = sorted(((k, a.score_parts.get(k, 0) - b.score_parts.get(k, 0)) for k in PART_LABELS),
                        key=lambda kv: -abs(kv[1]))
        moved = [f"{PART_LABELS[k]} {d:+.1f}" for k, d in deltas if abs(d) >= 0.1][:3]
        if moved:
            out.append("Score terms: " + ", ".join(moved))
            if deltas[0][0] == "value" and sa["landed_total"] == sb["landed_total"] and not weights_changed:
                out.append("Its cost didn't change; its position relative to the other eligible offers did")
    nb, na = set(sb["blockers"]), set(sa["blockers"])
    if not (sa["eligible"] and sb["eligible"]):  # blockers only matter between two eligible states
        return out
    if na - nb:
        out.append("New blocker: " + "; ".join(sorted(na - nb)))
    if nb - na:
        out.append("Blocker cleared: " + "; ".join(sorted(nb - na)))
    return out


def simulate(offer_dicts: list[dict], base_req: Requirements, domain_cfg: dict, scenario: dict) -> dict:
    alt_req = apply_scenario(base_req, scenario)
    weights = scenario.get("weights")
    base_offers = [Offer(**o) for o in offer_dicts]
    alt_offers = [Offer(**o) for o in offer_dicts]
    base = analyze(base_offers, base_req, domain_cfg)
    alt = analyze(alt_offers, alt_req, domain_cfg, weights)
    by_b = {o.id: o for o in base["ranked"]}
    rows = []
    for a in alt["ranked"]:
        b = by_b[a.id]
        sb, sa = _state(b, base["decision"]), _state(a, alt["decision"])
        reasons = _offer_reasons(b, a, sb, sa, alt_req.currency, bool(weights))
        if sb["rank"] != sa["rank"] or reasons:
            rows.append({"id": a.id, "name": a.name, "vendor": a.vendor, "before": sb, "after": sa,
                         "score_delta": round(a.score - b.score, 1), "reasons": reasons})
    rows.sort(key=lambda r: (r["after"]["rank"] or 999, r["before"]["rank"] or 999))

    rb, ra = base["decision"]["recommended_offer_id"], alt["decision"]["recommended_offer_id"]
    explain = []
    if rb != ra:
        ob, oa = by_b.get(rb), next((o for o in alt["ranked"] if o.id == ra), None)
        ob_alt = next((o for o in alt["ranked"] if o.id == rb), None)
        if ob and ob_alt is not None:
            s_after = _state(ob_alt, alt["decision"])
            if not s_after["eligible"]:
                explain.append(f"{ob.vendor} {ob.name[:50]} is no longer eligible: " + "; ".join(s_after["failed"]))
            elif s_after["blockers"]:
                explain.append(f"{ob.vendor} {ob.name[:50]} is now blocked: " + "; ".join(s_after["blockers"]))
        if oa:
            oa_before = by_b[oa.id]
            s_before = _state(oa_before, base["decision"])
            if not s_before["eligible"]:
                explain.append(f"{oa.vendor} {oa.name[:50]} became eligible: it no longer fails "
                               + "; ".join(s_before["failed"]))
            elif s_before["blockers"] and not blockers(oa):
                explain.append(f"{oa.vendor} {oa.name[:50]} is no longer blocked ("
                               + "; ".join(s_before["blockers"]) + ")")
            if ob_alt is not None and ob_alt.rank and oa.rank and oa.rank < ob_alt.rank:
                explain.append(f"Under this scenario {oa.vendor} {why_beats(oa, ob_alt)} over {ob.vendor}")
        if not oa:
            explain.append("No offer is eligible under this scenario")
        elif alt["decision"]["provisional"]:
            explain.append("No offer clears every rule; the pick is provisional and needs a quote")
    else:
        explain.append("Recommendation unchanged" + (f": {by_b[rb].vendor} {by_b[rb].name[:50]} still clears every rule"
                                                     if rb else ""))

    def brief(d, ranked):
        o = next((x for x in ranked if x.id == d["recommended_offer_id"]), None)
        return {"offer_id": o.id, "name": o.name, "vendor": o.vendor, "landed_total": o.landed.get("total"),
                "landed_confidence": o.landed.get("confidence"), "score": o.score,
                "provisional": d["provisional"]} if o else None

    return {"requirements": alt_req.model_dump(),
            "changes": requirement_changes(base_req, alt_req, normalize_weights(None) if weights else None,
                                           normalize_weights(weights) if weights else None),
            "default_weights": WEIGHTS,
            "before": brief(base["decision"], base["ranked"]), "after": brief(alt["decision"], alt["ranked"]),
            "recommendation_changed": rb != ra, "explanation": explain,
            "value_pick_id": alt["decision"]["value_pick_id"], "decision": alt["decision"],
            "offers": [{"id": o.id, "rank": o.rank, "score": o.score, "score_parts": o.score_parts,
                        "landed": o.landed, "failed_constraints": o.failed_constraints, "flags": o.flags,
                        "risk": o.risk, "effective_unit_price": o.effective_unit_price, "tco": o.tco}
                       for o in alt["ranked"]],
            "changed_offers": rows}
