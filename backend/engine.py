"""The requirement-dependent half of a mission, shared by the live pipeline and the counterfactual simulator.

Sourcing (agents), verification (Fetch) and the requirement-independent ML (matching, fair price) run once per
mission. Everything that depends on what the buyer asked for runs here, on the stored offers, every time:

  cost analyst    landed cost per offer for this quantity, tax rate and currency
  risk analyst    requirement findings (caps, MOQ, stock, deadline), constraint fit, supplier risk radar
  ranking         TCO + weighted score with per-term contributions
  decision agent  evidence policy → recommendation, value pick, trace (decision.py)

Because the simulator calls exactly this function on exactly the same offers, a scenario can only differ from the
mission because the requirements or weights differ.
"""
from . import adversarial, evidence
from .decision import decide
from .landed_cost import landed_cost
from .ml import warranty_risk
from .models import Offer, Requirements
from .risk import supplier_risk
from .scoring import apply_constraints, compute_tco, rank


def target_warranty_years(req: Requirements, domain_cfg: dict) -> float:
    return next((float(c.value) for c in req.constraints if c.attribute == "warranty_years"),
                domain_cfg["risk_model"]["lifetime_years"])


def analyze(offers: list[Offer], req: Requirements, domain_cfg: dict, weights: dict | None = None) -> dict:
    target = target_warranty_years(req, domain_cfg)
    for o in offers:
        if not o.claims and o.verification:  # missions saved before the claims ledger existed
            o.claims = evidence.build_claims(o)
            o.flags = adversarial.static_flags(o)
        o.currency = req.currency
        warranty_risk.score_offer(o, target)
        o.landed = landed_cost(o, req, domain_cfg.get("tax"))
        split = [l for l in o.landed.get("lines", []) if l["key"] in ("goods_promo", "goods_list")]
        o.effective_unit_price = (round(o.landed["goods_subtotal"] / req.quantity, 2)
                                  if split and all(l["amount"] is not None for l in split) else None)
        o.flags = [f for f in o.flags if f.get("scope") != "requirement"] + \
            [{**f, "scope": "requirement"} for f in adversarial.requirement_flags(o, req)]
    apply_constraints(offers, req)
    compute_tco(offers, req, domain_cfg["risk_model"])
    for o in offers:
        o.risk = supplier_risk(o, req, o.flags)
    ranked = rank(offers, weights)
    return {"ranked": ranked, "decision": decide(ranked)}


def roles_summary(agents: dict, vsum: dict, ranked: list[Offer], decision: dict) -> list[dict]:
    """What each stage was responsible for and what it produced, for the brief's 'how this was decided' panel."""
    lc = [o.landed.get("confidence") for o in ranked if o.landed]
    lv = [o.risk.get("level") for o in ranked if o.risk]
    flags = [f for o in ranked for f in o.flags if f["severity"] in ("high", "medium")]
    rec = next((t for t in decision["trace"] if t["outcome"] in ("recommended", "provisional")), None)
    return [
        {"role": "Sourcing agents", "responsibility": "Browse each vendor's site and extract structured offers",
         "output": f"{sum(a.get('n_offers', 0) for a in agents.values())} offers from "
                   f"{sum(1 for a in agents.values() if a.get('status') == 'done')} of {len(agents)} vendors"},
        {"role": "Verification agent", "responsibility": "Re-read every cited product page; build the claims ledger",
         "output": f"{vsum.get('pages_fetched', 0)} pages read; claims " + ", ".join(
             f"{v} {k}" for k, v in (vsum.get("claims") or {}).items())},
        {"role": "Adversarial checks", "responsibility": "Find misleading prices, hidden charges, conflicts and injected text",
         "output": f"{len(flags)} medium/high findings across {len({o.id for o in ranked if any(f['severity'] in ('high', 'medium') for f in o.flags)})} offers"},
        {"role": "Cost analyst", "responsibility": "Landed cost: goods, discounts, delivery, fees, tax",
         "output": ", ".join(f"{lc.count(k)} {k}" for k in ("confirmed", "estimated", "incomplete") if lc.count(k))},
        {"role": "Risk analyst", "responsibility": "Six-dimension supplier risk radar with reasons",
         "output": ", ".join(f"{lv.count(k)} {k}" for k in ("low", "moderate", "elevated", "high", "unknown") if lv.count(k))},
        {"role": "Decision agent", "responsibility": "Apply the evidence policy to the ranking (no LLM, no vote)",
         "output": (f"{rec['vendor']} {rec['name'][:48]} ({rec['outcome']})" if rec else "no eligible offer")
                   + f" after skipping {sum(1 for t in decision['trace'] if t['outcome'] == 'skipped')}"},
    ]
