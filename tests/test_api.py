"""End to end through the HTTP API: plan → mission → brief → scenario → approval → exports → outcome."""
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import app

INDIA = ("Buy 25 laptops for our Bengaluru office, 16GB RAM, Windows 11 Pro, 3 year onsite warranty, "
         "budget ₹20 lakh, delivery within 10 days")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def start(c, objective):
    p = c.post("/api/plan", json={"objective": objective, "mode": "replay"}).json()
    keys = [v["key"] for v in p["vendors"] if v["recorded"]]
    mid = c.post("/api/missions", json={"objective": objective, "requirements": p["requirements"], "vendor_keys": keys,
                                        "planner": p["planner"], "mode": "replay"}).json()["id"]
    for _ in range(300):
        m = c.get(f"/api/missions/{mid}").json()
        if m["status"] in ("awaiting_approval", "failed"):
            return m
        time.sleep(0.05)
    raise AssertionError("mission did not finish")


def test_live_mode_refused_without_key(client):
    r = client.post("/api/plan", json={"objective": "50 laptops", "mode": "live"})
    assert r.status_code == 400


def test_full_flow_with_scenario_approval(client):
    m = start(client, INDIA)
    assert m["status"] == "awaiting_approval"
    mid, rep = m["id"], m["report"]
    ganga = next(o for o in rep["offers"] if "Ganga" in o["vendor"])

    # excluded at the mission's 10-day deadline: can't be approved without the scenario
    r = client.post(f"/api/missions/{mid}/decision", json={"decision": "approve", "offer_id": ganga["id"], "note": "cheaper"})
    assert r.status_code == 400

    sim = client.post(f"/api/missions/{mid}/simulate", json={"deadline_days": 30}).json()
    assert sim["after"]["offer_id"] == ganga["id"]

    r = client.post(f"/api/missions/{mid}/decision", json={"decision": "approve", "offer_id": ganga["id"],
                                                          "approver": "Priya", "scenario": {"deadline_days": 30}})
    assert r.status_code == 200, r.text
    d = r.json()["decision"]
    assert d["override"] is False and d["scenario"]["changes"] == ["Deadline (days): 10 → 30"]

    assert client.post(f"/api/missions/{mid}/decision", json={"decision": "reject"}).status_code == 409

    po = client.get(f"/missions/{mid}/po").text
    assert "APPROVED by Priya" in po and "revised requirements" in po and "Buyer override" not in po
    assert "Test fixture" in po  # synthetic supplier is labelled on the PO
    report = client.get(f"/missions/{mid}/report").text
    assert "Comparative statement" in report and "chain intact" in report
    a = client.get(f"/api/missions/{mid}/audit").json()
    assert a["chain"]["valid"] and a["entries"][-1]["action"] == "decision.approve"
    csv = client.get(f"/api/missions/{mid}/offers.csv").text
    assert "landed_total" in csv.splitlines()[0]

    out = client.post(f"/api/missions/{mid}/outcome", json={"delivered_on": "2026-11-12", "on_time": True,
                                                            "actual_unit_price": 55800}).json()
    assert len(out["outcomes"]) == 1
    assert client.get(f"/api/missions/{mid}/audit").json()["entries"][-1]["action"] == "outcome.recorded"


def test_override_needs_a_reason(client):
    m = start(client, INDIA)
    other = next(o for o in m["report"]["offers"] if o["rank"] and o["id"] != m["report"]["recommended_offer_id"])
    r = client.post(f"/api/missions/{m['id']}/decision", json={"decision": "approve", "offer_id": other["id"]})
    assert r.status_code == 422
    r = client.post(f"/api/missions/{m['id']}/decision", json={"decision": "approve", "offer_id": other["id"],
                                                               "note": "existing supplier relationship"})
    assert r.status_code == 200 and r.json()["decision"]["override"] is True


def test_quote_request_is_india_aware(client):
    m = start(client, INDIA)
    kaveri = next(o for o in m["report"]["offers"] if "Kaveri" in o["vendor"])
    q = client.get(f"/api/missions/{m['id']}/offers/{kaveri['id']}/quote-request").json()
    assert "GSTIN" in q["body"] and "warranty" in q["body"].lower() and "₹54,500.00" in q["body"]


def test_legacy_budget_field_still_accepted(client):
    p = client.post("/api/plan", json={"objective": INDIA, "mode": "replay"}).json()
    req = {**p["requirements"]}
    req["budget_total_usd"] = req.pop("budget_total")
    r = client.post("/api/missions", json={"objective": INDIA, "requirements": req, "vendor_keys": ["sahyadri"], "mode": "replay"})
    assert r.status_code == 200
