"""Decision policy, counterfactual simulator, audit chain, procurement memory, memo grounding and the planner."""
import asyncio

import pytest

from backend import audit, config, grounding, memory, planner
from backend.pipeline import Mission, run_mission
from backend.simulate import simulate

INDIA = ("Buy 25 laptops for our Bengaluru office, 16GB RAM, Windows 11 Pro, 3 year onsite warranty, "
         "budget ₹20 lakh, delivery within 10 days")


def run(objective: str, vendor_keys: list[str] | None = None) -> Mission:
    d = config.load_domains()
    req = planner.plan_heuristic(objective, d)
    vendors = [v for v in d[req.domain]["vendors"]
               if (config.REPLAY_DIR / req.domain / f"{v['key']}.json").exists() and (not vendor_keys or v["key"] in vendor_keys)]
    m = Mission(req, vendors, objective, "rule-based", "replay")
    asyncio.run(run_mission(m))
    assert m.status == "awaiting_approval", m.audit[-1]
    return m


@pytest.fixture(scope="module")
def india():
    return run(INDIA)


def by_vendor(m, word):
    return next(o for o in m.report["offers"] if word in o["vendor"])


def test_india_decision_and_trace(india):
    rep = india.report
    assert by_vendor(india, "Sahyadri")["id"] == rep["recommended_offer_id"]
    assert rep["decision"]["provisional"] is False
    assert by_vendor(india, "Nimbus")["rank"] is None  # 8 GB at the advertised price fails the 16 GB must-have
    assert by_vendor(india, "Ganga")["rank"] is None  # ships in 3-4 weeks vs a 10-day deadline
    kaveri = by_vendor(india, "Kaveri")
    assert {"WARRANTY_CONFLICT", "STOCK_BELOW_QUANTITY"} <= {f["code"] for f in kaveri["flags"]}
    assert rep["provenance"] == ["synthetic"]


def test_simulated_deadline_changes_the_pick(india):
    cfg = config.load_domains()["laptops_in"]
    r = simulate(india.report["offers"], india.req, cfg, {"deadline_days": 30})
    assert r["recommendation_changed"] and "Ganga" in r["after"]["vendor"]
    assert r["after"]["landed_total"] == 1_563_795
    assert any("became eligible" in e and "delivery" in e for e in r["explanation"])


def test_simulator_baseline_matches_the_mission(india):
    cfg = config.load_domains()["laptops_in"]
    r = simulate(india.report["offers"], india.req, cfg, {})
    assert r["before"]["offer_id"] == r["after"]["offer_id"] == india.report["recommended_offer_id"]
    assert not r["recommendation_changed"] and r["changes"] == []


def test_simulated_budget_cut_explains_exclusion(india):
    cfg = config.load_domains()["laptops_in"]
    r = simulate(india.report["offers"], india.req, cfg, {"budget_total": 1_500_000})
    assert r["after"] is None
    assert any("₹15,97,500 > budget ₹15,00,000" in e for e in r["explanation"])


def test_simulated_quantity_lifts_the_promo_cap(india):
    cfg = config.load_domains()["laptops_in"]
    r = simulate(india.report["offers"], india.req, cfg, {"quantity": 2})
    deccan = next(o for o in r["offers"] if o["id"] == by_vendor(india, "Deccan")["id"])
    assert deccan["landed"]["total"] == 99_998  # 2 × 49,999, both inside the cap
    assert "Deccan" in r["after"]["vendor"]


def test_real_laptops_recommendation_is_page_verified_and_in_stock():
    m = run("I need 50 laptops, 16GB RAM, Windows 11 Pro, 3-year onsite warranty preferred. Total budget $75k.")
    best = next(o for o in m.report["offers"] if o["id"] == m.report["recommended_offer_id"])
    assert next(c for c in best["claims"] if c["field"] == "price")["status"] == "verified"
    assert not any(f["code"] in ("OUT_OF_STOCK", "BULK_CAP") for f in best["flags"])
    skipped = [t for t in m.report["decision"]["trace"] if t["outcome"] == "skipped"]
    assert any("promo price capped below your quantity" in r for t in skipped for r in t["reasons"])


def test_audit_chain_detects_tampering(india):
    assert audit.verify_chain(india.audit)["valid"]
    forged = [dict(e) for e in india.audit]
    forged[3] = {**forged[3], "data": {**forged[3]["data"], "tampered": True}}
    res = audit.verify_chain(forged)
    assert not res["valid"] and res["broken_at"] == 3


def test_memory_does_not_inflate_on_replay():
    before = memory.stats()["observations"]
    run(INDIA)
    after_first = memory.stats()["observations"]
    run(INDIA)
    assert memory.stats()["observations"] == after_first  # same recording replayed = same observations
    assert after_first >= before


def test_memory_history_excludes_own_observation(india):
    snap = india.snapshot()
    hist = memory.history_for(snap, snap["report"]["offers"])
    own = memory.observation_ids(snap)
    for oid, h in hist.items():
        assert all(r["id"] != own[oid] for r in h["observations"])


def test_memo_grounding():
    from backend.models import Requirements
    req = Requirements(domain="laptops_in", item="laptops", quantity=25, currency="INR", budget_total=2_000_000, constraints=[])
    offers = [{"unit_price": 63900, "landed": {"total": 1_597_500, "per_unit": 63900, "lines": []}, "tco": 1_612_620}]
    allowed = grounding.allowed_figures(offers, req)
    assert grounding.check_memo("Buy 25 at ₹63,900 each, ₹15,97,500 landed, ₹4,02,500 under budget.", allowed)["ok"]
    assert grounding.check_memo("About ₹16 lakh landed.", allowed)["ok"]  # rounding within 1.5%
    bad = grounding.check_memo("Total ₹14,20,000 after negotiation.", allowed)
    assert not bad["ok"] and bad["unsupported"] == [1_420_000]


@pytest.mark.parametrize("objective,expect", [
    (INDIA, {"domain": "laptops_in", "currency": "INR", "quantity": 25, "budget_total": 2_000_000, "deadline_days": 10,
             "ship_to_state": "Karnataka"}),
    ("I need 50 laptops, 16GB RAM, Windows 11 Pro. Budget $75k.",
     {"domain": "laptops", "currency": "USD", "quantity": 50, "budget_total": 75_000, "deadline_days": None}),
    ("40 cases of nitrile gloves, budget around $7,000, needed in 2 weeks",
     {"domain": "lab_supplies", "currency": "USD", "quantity": 40, "budget_total": 7_000, "deadline_days": 14}),
    ("Need 12 laptops for Pune, 32GB RAM, budget Rs 18,00,000",
     {"domain": "laptops_in", "currency": "INR", "quantity": 12, "budget_total": 1_800_000, "ship_to_state": "Maharashtra"}),
])
def test_planner_fallback(objective, expect):
    r = planner.plan_heuristic(objective, config.load_domains())
    for k, v in expect.items():
        assert getattr(r, k) == v, (k, getattr(r, k))
