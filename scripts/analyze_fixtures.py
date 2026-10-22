"""Run verification + the analysis engine over the recorded fixtures offline (no TinyFish / Claude calls).
Usage: python -m scripts.analyze_fixtures [objective]"""
import json
import sys

from backend import config, planner, verify
from backend.engine import analyze
from backend.ml import matching, normalize, pricing
from backend.tinyfish import provenance, recorded_date

objective = sys.argv[1] if len(sys.argv) > 1 else "50 business laptops, 16GB RAM, Windows 11 Pro, 3 year warranty, budget $75k"
domains = config.load_domains()
req = planner.plan_heuristic(objective, domains)
cfg = domains[req.domain]
offers = []
for p in sorted((config.REPLAY_DIR / req.domain).glob("[!_]*.json")):
    offers += normalize.normalize_offers(p.stem, json.loads(p.read_text())["result"], cfg)
data = json.loads((config.REPLAY_DIR / req.domain / "_fetch.json").read_text())
pages = {u: {**pg, "fetched_at": recorded_date(data["_source"]), "provenance": provenance(data["_source"])}
         for u, pg in data["pages"].items()}
print("verification:", verify.verify_with_pages(offers, pages, req.quantity))
matching.group_offers(offers)
print("price model:", pricing.price_offers(offers, cfg["price_features"])["method"])
res = analyze(offers, req, cfg)
print("req:", req.domain, req.currency, req.quantity, req.budget_total, req.deadline_days)
for o in res["ranked"]:
    lc = o.landed
    print(f"{o.rank!s:>4} {o.score:5} {o.vendor[:8]:8} {o.unit_price or 0:>10,.0f} landed {lc.get('total') or 0:>12,.0f} "
          f"{lc.get('confidence', '-'):10} risk {o.risk.get('level', '-'):9} | {o.name[:40]} | "
          f"{[f['code'] for f in o.flags if f['severity'] in ('high', 'medium')]} {o.failed_constraints}")
print("decision:", json.dumps(res["decision"]["why"], ensure_ascii=False, indent=1))
