"""End-to-end smoke test against a running server: plan -> mission -> approve -> PO.

Usage: python -m scripts.smoke_test "objective text" [base_url]
"""
import sys
import time

import httpx

objective = sys.argv[1] if len(sys.argv) > 1 else \
    "I need to purchase 50 laptops for my new office. 16GB RAM, Windows 11 Pro, 3-year onsite warranty preferred. Total budget $75k."
base = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8077"
http = httpx.Client(base_url=base, trust_env=False, timeout=180)

p = http.post("/api/plan", json={"objective": objective}).json()
r = p["requirements"]
print("planner:", p["planner"], "| domain:", r["domain"], "| qty:", r["quantity"], "| budget:", r.get("budget_total"), r.get("currency"))
found = [v for v in p.get("discovery", {}).get("vendors", []) if v.get("recorded")]
print("search:", p.get("discovery", {}).get("query"), "| discovered:", [v["key"] for v in p.get("discovery", {}).get("vendors", [])],
      "| dispatching:", [v["key"] for v in found])
mid = http.post("/api/missions", json={"objective": objective, "requirements": r,
                                       "vendor_keys": [v["key"] for v in p["vendors"]],
                                       "extra_vendors": [{k: v[k] for k in ("key", "name", "url", "hint")} for v in found]}).json()["id"]
deadline = time.time() + 900
while time.time() < deadline:
    m = http.get(f"/api/missions/{mid}").json()
    if m["status"] in ("awaiting_approval", "failed"):
        break
    time.sleep(2)
print("status:", m["status"], {k: (a["status"], a["n_offers"]) for k, a in m["agents"].items()})
if not m["report"]:
    sys.exit(1)
print("summary:", m["report"]["summary"])
print("verification:", m["report"]["insights"].get("verification"))
for o in m["report"]["offers"][:6]:
    print(f"  #{o['rank']} {o['score']:5} {o['vendor']:12} ${o['unit_price']:>8} eff ${o['effective_unit_price'] or o['unit_price']:>8} landed {(o.get('landed') or {}).get('total')} "
          f"{o['verification'].get('status', '-'):10} {o['name'][:44]}")
best = next(o for o in m["report"]["offers"] if o["id"] == m["report"]["recommended_offer_id"]) \
    if m["report"]["recommended_offer_id"] else None
if best:
    print(f"best: {best['vendor']} {best['name']} ${best['unit_price']} score {best['score']}")
    d = http.post(f"/api/missions/{mid}/decision", json={"decision": "approve", "approver": "Test Buyer"})
    print("decision:", d.status_code, d.json()["status"])
    po = http.get(f"/missions/{mid}/po").text
    print("PO has approval stamp:", "APPROVED by Test Buyer" in po)
print("mission:", mid)
