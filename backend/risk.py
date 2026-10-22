"""Explainable supplier risk radar.

Six dimensions, each 0 (low risk) … 1 (high risk) with the reasons that produced the number:

  evidence     how many critical claims the vendor's own page confirmed vs contradicted
  spec         must-have and preference compliance after page corrections
  delivery     stock, order quantity, and stated lead time vs the buyer's deadline
  warranty     coverage length/service (clause model) plus conflicts and small print
  price        price transparency: caps, unit-basis tricks, extra charges, prices not on the page
  integrity    agent-vs-page contradictions and pages that try to instruct AI agents

A dimension with no evidence either way is `None` (unknown), shown grey, and left out of the overall risk; it raises
`uncertainty` instead. Missing information is never treated as a sign of fraud.
"""
from .evidence import coverage
from .models import Offer, Requirements

DIMENSIONS = {"evidence": "Evidence strength", "spec": "Spec compliance", "delivery": "Delivery feasibility",
              "warranty": "Warranty clarity", "price": "Price transparency", "integrity": "Consistency & integrity"}
WEIGHTS = {"evidence": 0.20, "spec": 0.20, "delivery": 0.20, "warranty": 0.15, "price": 0.15, "integrity": 0.10}


def level(r: float | None) -> str:
    if r is None:
        return "unknown"
    return "low" if r < 0.25 else "moderate" if r < 0.5 else "elevated" if r < 0.75 else "high"


def _dim(key: str, risk: float | None, reasons: list[str], codes: list[str] | None = None) -> dict:
    r = None if risk is None else round(min(1.0, max(0.0, risk)), 3)
    return {"key": key, "label": DIMENSIONS[key], "risk": r, "level": level(r), "reasons": reasons, "flags": codes or []}


def supplier_risk(o: Offer, req: Requirements, flags: list[dict]) -> dict:
    codes = [f["code"] for f in flags]
    has = lambda c: c in codes
    dims = []

    # evidence
    cov = coverage(o.claims)
    known = cov["verified"] + cov["contradicted"] + cov["inferred"]
    contradicted = [c["label"] for c in o.claims if c["status"] == "contradicted"]
    unknown = [c["label"] for c in o.claims if c["status"] == "unknown"]
    reasons = [f"{cov['verified']} of {cov['total']} critical claims confirmed on the vendor's page"]
    if contradicted:
        reasons.append("Contradicted by the page: " + ", ".join(contradicted))
    if cov["inferred"]:
        reasons.append(f"{cov['inferred']} inferred from related listings")
    if unknown:
        reasons.append("Unknown (not evidence of a problem): " + ", ".join(unknown))
    dims.append(_dim("evidence", (cov["contradicted"] + 0.3 * cov["inferred"]) / known if known else None, reasons))

    # spec compliance
    hard_fail = [f for f in o.failed_constraints if not f.startswith("unverified")]
    hard_unknown = [f.split(": ", 1)[1] for f in o.failed_constraints if f.startswith("unverified")]
    spec_codes = [c for c in codes if c in ("SPEC_CONTRADICTED", "UNIT_BASIS_MISMATCH")]
    reasons = [f"Fails must-have: {f}" for f in hard_fail]
    reasons += [f"Must-have not confirmed: {a}" for a in hard_unknown]
    if spec_codes:
        reasons.append("Agent's specs were corrected from the page (" + ", ".join(sorted(set(spec_codes))).lower() + ")")
    if not reasons:
        reasons.append(f"Meets all must-haves; preference fit {o.fit_score:.0%}")
    risk = 1.0 if hard_fail else min(1.0, (1 - o.fit_score) + 0.2 * len(spec_codes))
    dims.append(_dim("spec", risk, reasons, spec_codes))

    # delivery
    lt = (o.terms or {}).get("lead_time")
    sev = {"OUT_OF_STOCK": 1.0, "DELIVERY_INFEASIBLE": 1.0, "STOCK_BELOW_QUANTITY": 0.9, "DELIVERY_AT_RISK": 0.6,
           "MOQ_ABOVE_QUANTITY": 0.3, "STOCK_UNCERTAIN": 0.4}
    hits = [(c, sev[c]) for c in codes if c in sev]
    reasons = [f["title"] + (f": {f['detail']}" if f["code"] != "OUT_OF_STOCK" else "") for f in flags if f["code"] in sev]
    if hits:
        risk = max(v for _, v in hits)
    elif lt and (req.deadline_days is None or lt["max_days"] <= req.deadline_days):
        risk = 0.1
        reasons.append(f"Page states {lt['kind']} in {lt['min_days']}–{lt['max_days']} days"
                       + (f", within your {req.deadline_days}-day deadline" if req.deadline_days is not None else ""))
    elif o.stock and any(c["field"] == "stock" and c["status"] == "verified" for c in o.claims):
        risk = 0.2
        reasons.append(f"In stock per the page (“{o.stock}”); delivery time not stated")
    else:
        risk = None
        reasons.append("No verified stock or delivery information")
    dims.append(_dim("delivery", risk, reasons, [c for c, _ in hits]))

    # warranty
    w_claim = next((c for c in o.claims if c["field"] == "warranty"), None)
    reasons = list(o.warranty_risk_reasons)
    w_codes = [c for c in codes if c in ("WARRANTY_CONFLICT", "WARRANTY_FINE_PRINT")]
    if w_claim and w_claim["status"] == "unknown" and not w_claim["claimed"]:
        risk = None
        reasons = ["No warranty terms stated by the agent or the page"]
    else:
        risk = o.warranty_risk
        if has("WARRANTY_CONFLICT"):
            risk = max(risk, 0.85)
            reasons.insert(0, "Page contradicts the listed warranty")
        if has("WARRANTY_FINE_PRINT"):
            risk += 0.15
            reasons.append("Small print limits the cover")
    dims.append(_dim("warranty", risk, reasons or ["Warranty meets the target"], w_codes))

    # price transparency
    price_claim = next((c for c in o.claims if c["field"] == "price"), None)
    weights = {"UNIT_BASIS_MISMATCH": 0.8, "BULK_CAP": 0.5, "PRICE_NOT_ON_PAGE": 0.5, "HIDDEN_CHARGE": 0.3,
               "TAX_CONFLICT": 0.3}
    risk, reasons, p_codes = 0.0, [], []
    for f in flags:
        if f["code"] in weights:
            risk += weights[f["code"]]
            reasons.append(f["title"])
            p_codes.append(f["code"])
    if o.price_flag in ("suspicious", "overpriced"):
        risk += 0.5 if o.price_flag == "suspicious" else 0.25
        reasons.append(o.anomaly or f"Price flagged {o.price_flag}")
    unknown_lines = (o.landed or {}).get("unknowns", [])
    if unknown_lines:
        reasons.append("Not stated (uncertainty, not a penalty): " + ", ".join(unknown_lines))
    if not p_codes and o.price_flag not in ("suspicious", "overpriced"):
        if price_claim and price_claim["status"] == "verified":
            reasons.insert(0, "Advertised price confirmed on the page, no extra charges found")
        elif not unknown_lines:
            reasons.insert(0, "No pricing problems found")
        else:
            risk = None if not price_claim or price_claim["status"] == "unknown" else risk
            reasons.insert(0, "Price not confirmed on the page")
    dims.append(_dim("price", risk, reasons, p_codes))

    # integrity
    page_read = bool((o.verification or {}).get("retrieved_at"))
    reasons, risk, i_codes = [], 0.0, []
    if has("PAGE_INJECTION"):
        risk, i_codes = 1.0, ["PAGE_INJECTION"]
        reasons.append("Page contains text instructing AI agents (ignored)")
    n_contra = len(contradicted)
    if n_contra:
        risk = max(risk, min(1.0, 0.25 * n_contra))
        reasons.append(f"{n_contra} claim(s) from the agent disagreed with the page")
    if o.condition in ("refurbished", "used"):
        risk = max(risk, 0.3)
        reasons.append(f"Condition: {o.condition}")
    if not reasons:  # no signal either way
        reasons.append("Page and agent agree" if page_read else "Product page not read")
        risk = 0.0 if page_read else None
    dims.append(_dim("integrity", risk, reasons, i_codes))

    known = [d for d in dims if d["risk"] is not None]
    wsum = sum(WEIGHTS[d["key"]] for d in known)
    overall = round(sum(WEIGHTS[d["key"]] * d["risk"] for d in known) / wsum, 3) if wsum else None
    unknown_w = round(1 - wsum, 3)
    claims_unknown = cov["unknown"] / cov["total"] if cov["total"] else 1.0
    return {"overall": overall, "level": level(overall), "dimensions": dims,
            "uncertainty": round(0.5 * unknown_w + 0.5 * claims_unknown, 3),
            "explain": _headline(dims, overall)}


def _headline(dims: list[dict], overall: float | None) -> str:
    if overall is None:
        return "Not enough evidence to assess this supplier."
    worst = sorted((d for d in dims if d["risk"] is not None), key=lambda d: -d["risk"])[:2]
    worst = [d for d in worst if d["risk"] >= 0.5]
    unk = [d["label"].lower() for d in dims if d["risk"] is None]
    s = f"Overall {level(overall)} risk"
    if worst:
        s += ", driven by " + " and ".join(f"{d['label'].lower()} ({d['reasons'][0]})" for d in worst)
    if unk:
        s += ". Unknown: " + ", ".join(unk)
    return s + "."
