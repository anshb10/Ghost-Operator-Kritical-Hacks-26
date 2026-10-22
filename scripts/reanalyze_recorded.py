"""Re-run the full analysis on agent runs recorded in live mode (data/recorded/), without paying for new agent runs.

Agent results are replayed from data/recorded/<domain>/<vendor>.json; product pages are fetched live with the
TinyFish Fetch API (and recorded) unless --no-fetch is given.

Usage: python -m scripts.reanalyze_recorded laptops lenovo,cdw,hp "50 laptops 16GB RAM Windows 11 Pro budget $75k"
"""
import asyncio
import sys

from backend import config, planner, tinyfish
from backend.pipeline import Mission, run_mission

domain, keys, objective = sys.argv[1], sys.argv[2].split(","), sys.argv[3]
live_fetch = "--no-fetch" not in sys.argv

domains = config.load_domains()
req = planner.plan_heuristic(objective, domains)
known = {v["key"]: v for v in domains[domain]["vendors"]}
vendors = [known.get(k) or {"key": k, "name": f"{k}.com", "url": f"https://www.{k}.com", "hint": ""} for k in keys]

config.REPLAY_DIR = config.RECORD_DIR  # replay agents from the live recordings
config.REPLAY_SPEED = 1000
_fetch = tinyfish.fetch_pages
if live_fetch:
    tinyfish.fetch_pages = lambda urls, d, mode: _fetch(urls, d, "live")

m = Mission(req, vendors, objective, "rule-based (re-analysis)")
m.mode = "replay"
asyncio.run(run_mission(m))
rep = m.report
print("mission:", m.id)
print(rep["insights"]["verification"])
print(rep["summary"], "\n")
for o in m.offers:
    v = o.verification
    bad = [f"{c['field']}: claimed {c.get('claimed')} / page {c.get('page')}" for c in v.get("checks", []) if c["ok"] is False]
    print(f"#{o.rank!s:>4} {o.score:5} {o.vendor:8} ${o.unit_price:>8} eff ${o.effective_unit_price or o.unit_price:>8} "
          f"{v.get('status', '-'):10} {o.warranty.service:8} {o.stock[:12]:12} {o.name[:44]}" + (f"  ✕ {bad}" if bad else ""))
