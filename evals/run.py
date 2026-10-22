"""Reproducible evaluation suite. Offline: replay recordings and labelled synthetic pages only, no network, no LLM.

    .venv/bin/python -m evals.run            # writes evals/results/latest.json and latest.md

Categories: requirement matching, critical-claim verification, misleading-offer detection, landed-cost accuracy,
ranking changes under constraints, end-to-end completion. Cases live in evals/cases.json. They were written by the
same people who wrote the detectors, so treat the numbers as a regression baseline, not an independent benchmark.
"""
import asyncio
import json
import os
import platform
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ["GHOST_DATA_DIR"] = tempfile.mkdtemp(prefix="ghost-eval-")
os.environ["GHOST_MODE"] = "replay"
os.environ["TINYFISH_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""

from backend import audit, config, planner, verify  # noqa: E402
from backend.adversarial import requirement_flags  # noqa: E402
from backend.landed_cost import landed_cost  # noqa: E402
from backend.ml import normalize  # noqa: E402
from backend.models import Constraint, Offer, Requirements  # noqa: E402
from backend.ml.normalize import parse_warranty  # noqa: E402
from backend.pipeline import Mission, run_mission  # noqa: E402
from backend.purchase_order import render_po  # noqa: E402
from backend.simulate import simulate  # noqa: E402
from backend.tinyfish import provenance, recorded_date  # noqa: E402

config.TINYFISH_API_KEY, config.MODE, config.REPLAY_SPEED = "", "replay", 100000
HERE = Path(__file__).parent
CASES = json.loads((HERE / "cases.json").read_text())
GST18 = {"kind": "gst", "rate_pct": 18, "hsn": "8471", "source": "eval"}


def synth_offer(spec: dict, url="https://eval.test/p") -> Offer:
    s = dict(spec)
    w = parse_warranty(s.pop("warranty", ""))
    o = Offer(id="e" + str(abs(hash(json.dumps(spec, sort_keys=True))) % 10**8), vendor="Eval Vendor", name="Eval Laptop",
              unit_price=float(s.pop("price")), currency=s.pop("currency", "INR"), url=url, warranty=w,
              stock=s.pop("stock", ""), seller="Eval Vendor", condition="new", attributes={k: float(v) for k, v in s.items()})
    o.attributes.setdefault("warranty_years", w.years or 0.0)
    return o


def synth_req(currency: str, quantity: int, deadline=None, tax_rate_pct=None) -> Requirements:
    return Requirements(domain="laptops_in" if currency == "INR" else "laptops", item="eval", quantity=quantity,
                        currency=currency, deadline_days=deadline, tax_rate_pct=tax_rate_pct,
                        constraints=[Constraint(attribute="ram_gb", op=">=", value=8, hard=True)])


def verified(o: Offer, page: str, quantity: int) -> Offer:
    verify.verify_with_pages([o], {o.url: {"title": "", "text": page, "fetched_at": "2026-10-20",
                                           "provenance": {"kind": "synthetic", "source": "eval"}}}, quantity)
    return o


# ── 1. requirement matching ─────────────────────────────────────────────
def eval_requirements():
    domains = config.load_domains()
    rows, fields_ok, fields_n = [], 0, 0
    for c in CASES["requirement_matching"]:
        r = planner.plan_heuristic(c["objective"], domains)
        misses = []
        for k, want in c["expect"].items():
            fields_n += 1
            if k.startswith("constraint:"):
                con = next((x for x in r.constraints if x.attribute == k.split(":", 1)[1]), None)
                got = [con.op, con.value, con.hard] if con else None
                ok = got is not None and got[0] == want[0] and float(got[1]) == float(want[1]) and got[2] == want[2]
            else:
                got = getattr(r, k)
                ok = got == want
            fields_ok += ok
            if not ok:
                misses.append({"field": k, "expected": want, "got": got})
        rows.append({"objective": c["objective"][:90], "pass": not misses, "misses": misses})
    return {"metric": {"field_accuracy": round(fields_ok / fields_n, 3), "cases_passed": sum(r["pass"] for r in rows),
                       "cases": len(rows)}, "planner": "rule-based fallback (no Claude key in eval)", "rows": rows}


# ── 2. claim verification ───────────────────────────────────────────────
def _real_offers(domain: str, quantity: int) -> list[Offer]:
    cfg = config.load_domains()[domain]
    offers = []
    for p in sorted((config.REPLAY_DIR / domain).glob("*.json")):
        if p.name.startswith("_"):
            continue
        rec = json.loads(p.read_text())
        name = {"officedepot": "Office Depot", "hp": "hp.com", "eagleprotect": "eagleprotect.com"}.get(p.stem, p.stem.capitalize())
        name = {"cdw": "CDW"}.get(p.stem, name)
        offers += normalize.normalize_offers(name, rec["result"], cfg)
    data = json.loads((config.REPLAY_DIR / domain / "_fetch.json").read_text())
    pages = {u: {**pg, "fetched_at": recorded_date(data["_source"]), "provenance": provenance(data["_source"])}
             for u, pg in data["pages"].items()}
    verify.verify_with_pages(offers, pages, quantity)
    return offers


def eval_claims():
    real = {"laptops": _real_offers("laptops", 50), "lab_supplies": _real_offers("lab_supplies", 40)}
    rows, confusion = [], {}
    for c in CASES["claim_verification"]:
        if c["source"] == "real":
            o = next((x for x in real[c["domain"]] if x.vendor == c["vendor"] and x.unit_price == c["price"]), None)
            label = f"{c['vendor']} {c['price']:g} · {c['field']}"
        else:
            o = verified(synth_offer(c["offer"]), c["page"], 10)
            label = f"synthetic · {c['field']} · {c.get('why', '')[:50]}"
        claim = next((x for x in (o.claims if o else []) if x["field"] == c["field"]), None)
        got = claim["status"] if claim else "missing"
        confusion.setdefault(c["expect"], {}).setdefault(got, 0)
        confusion[c["expect"]][got] += 1
        rows.append({"case": label, "source": c["source"], "expected": c["expect"], "got": got, "pass": got == c["expect"],
                     "why": c.get("why", "")})
    n = len(rows)
    return {"metric": {"accuracy": round(sum(r["pass"] for r in rows) / n, 3), "cases": n,
                       "real_cases": sum(r["source"] == "real" for r in rows),
                       "verified_when_not_supported": sum(1 for r in rows if r["got"] == "verified" and r["expected"] != "verified")},
            "confusion": confusion, "rows": rows}


# ── 3. misleading-offer detection ───────────────────────────────────────
def eval_misleading():
    rows, tp, fp, fn = [], 0, 0, 0
    per_code: dict[str, dict] = {}
    for c in CASES["misleading_offers"]:
        cur = c["offer"].get("currency", "INR")
        q = c.get("quantity", 10)
        o = verified(synth_offer(c["offer"]), c["page"], q)
        o.flags += requirement_flags(o, synth_req(cur, q, c.get("deadline")))
        got = {f["code"] for f in o.flags if f["severity"] in ("high", "medium")} | \
              {f["code"] for f in o.flags if f["code"] in ("TAX_EXCLUSIVE",)}
        want = set(c["expect"])
        tp += len(got & want)
        fp += len(got - want)
        fn += len(want - got)
        for code in want | got:
            d = per_code.setdefault(code, {"tp": 0, "fp": 0, "fn": 0})
            d["tp" if code in want and code in got else "fp" if code in got else "fn"] += 1
        rows.append({"case": c["name"], "expected": sorted(want), "got": sorted(got), "pass": got == want,
                     "control": not want})
    controls = [r for r in rows if r["control"]]
    return {"metric": {"precision": round(tp / (tp + fp), 3) if tp + fp else None,
                       "recall": round(tp / (tp + fn), 3) if tp + fn else None,
                       "cases_exact": sum(r["pass"] for r in rows), "cases": len(rows),
                       "controls_clean": sum(r["pass"] for r in controls), "controls": len(controls)},
            "per_code": per_code, "rows": rows}


# ── 4. landed cost ──────────────────────────────────────────────────────
def eval_landed():
    rows = []
    for c in CASES["landed_cost"]:
        cur = c["offer"].get("currency", "INR")
        o = verified(synth_offer(c["offer"]), c["page"], c["quantity"])
        lc = landed_cost(o, synth_req(cur, c["quantity"], tax_rate_pct=c.get("tax_rate_pct")),
                         GST18 if c.get("tax") == "gst18" else None)
        ok = abs((lc["total"] or 0) - c["expect_total"]) < 0.005
        if c.get("expect_confidence"):
            ok = ok and lc["confidence"] == c["expect_confidence"]
        rows.append({"case": c["name"], "expected": c["expect_total"], "got": lc["total"], "confidence": lc["confidence"],
                     "unknowns": lc["unknowns"], "pass": ok})
    return {"metric": {"exact_to_the_cent": sum(r["pass"] for r in rows), "cases": len(rows),
                       "max_abs_error": round(max(abs((r["got"] or 0) - r["expected"]) for r in rows), 2)}, "rows": rows}


# ── 5 & 6. ranking changes and end-to-end ───────────────────────────────
MISSIONS = {
    "india": ("laptops_in", "Buy 25 laptops for our Bengaluru office, 16GB RAM, Windows 11 Pro, 3 year onsite warranty, "
                            "budget ₹20 lakh, delivery within 10 days", None),
    "laptops": ("laptops", "I need 50 laptops, 16GB RAM, Windows 11 Pro, 3-year onsite warranty preferred. Total budget $75k.",
                [("officedepot", "Office Depot"), ("hp", "hp.com")]),
    "gloves": ("lab_supplies", "Our lab needs 40 cases of powder-free nitrile exam gloves (ASTM D6319), at least 4 mil thick. "
                               "Budget around $7,000.", [("eagleprotect", "eagleprotect.com")]),
}


def run_mission_sync(key: str) -> tuple[Mission, float]:
    domain, objective, extra = MISSIONS[key]
    d = config.load_domains()
    req = planner.plan_heuristic(objective, d)
    vendors = [v for v in d[domain]["vendors"] if (config.REPLAY_DIR / domain / f"{v['key']}.json").exists()]
    vendors += [{"key": k, "name": n, "url": f"https://www.{k}.com", "hint": ""} for k, n in (extra or [])]
    m = Mission(req, vendors, objective, "rule-based (eval)", "replay")
    t0 = time.time()
    asyncio.run(run_mission(m))
    return m, round(time.time() - t0, 2)


def eval_ranking(missions: dict):
    rows = []
    for c in CASES["ranking_changes"]:
        m = missions[c["mission"]]
        r = simulate(m.report["offers"], m.req, config.load_domains()[m.req.domain], c["scenario"])
        vendor = r["after"]["vendor"] if r["after"] else None
        ok_vendor = (vendor is None) if c["expect_vendor"] is None else bool(vendor and c["expect_vendor"] in vendor)
        explained = bool(r["explanation"]) and (not r["recommendation_changed"] or len(r["explanation"]) >= 1)
        ok = ok_vendor and r["recommendation_changed"] == c["expect_changed"] and explained
        rows.append({"mission": c["mission"], "scenario": c["scenario"], "expected": c["expect_vendor"], "got": vendor,
                     "changed": r["recommendation_changed"], "explanation": r["explanation"][:3], "pass": ok})
    return {"metric": {"cases_passed": sum(r["pass"] for r in rows), "cases": len(rows)}, "rows": rows}


def eval_e2e(missions: dict, timings: dict):
    rows = []
    for key, m in missions.items():
        checks = {"completed": m.status == "awaiting_approval", "has_recommendation": bool(m.report and m.report["recommended_offer_id"])}
        rec = m.report["recommended_offer_id"] if m.report else None
        if rec:
            m.decision = {"decision": "approve", "offer_id": rec, "approver": "Eval", "note": "", "override": False,
                          "at": datetime.now(timezone.utc).isoformat()}
            m.log("human:Eval", "decision.approve", offer_id=rec)
            m.status = "approved"
            checks["po_stamped"] = "APPROVED by Eval" in render_po(m)
        checks["audit_chain_valid"] = audit.verify_chain(m.audit)["valid"]
        checks["every_offer_has_claims"] = all(o.get("claims") for o in (m.report or {}).get("offers", []))
        best = next((o for o in m.report["offers"] if o["id"] == rec), None) if rec else None
        rows.append({"mission": key, "seconds": timings[key], "offers": len((m.report or {}).get("offers", [])),
                     "recommended": f"{best['vendor']} · {best['name'][:50]}" if best else None,
                     "provisional": m.report["decision"]["provisional"] if m.report else None,
                     "checks": checks, "pass": all(checks.values())})
    return {"metric": {"completed": sum(r["pass"] for r in rows), "missions": len(rows)}, "rows": rows}


def to_markdown(res: dict) -> str:
    m = res["categories"]
    L = [f"# Ghost Operator evaluation results", "",
         f"Run {res['run_at']} · Python {res['python']} · replay recordings + labelled synthetic pages · no network, no LLM.",
         "", "> " + res["caveat"], "", "| Category | Result |", "|---|---|",
         f"| Requirement matching (rule-based planner) | {m['requirement_matching']['metric']['field_accuracy']:.1%} of fields, "
         f"{m['requirement_matching']['metric']['cases_passed']}/{m['requirement_matching']['metric']['cases']} objectives fully right |",
         f"| Critical-claim verification | {m['claim_verification']['metric']['accuracy']:.1%} of {m['claim_verification']['metric']['cases']} "
         f"labelled claims ({m['claim_verification']['metric']['real_cases']} on real pages); "
         f"{m['claim_verification']['metric']['verified_when_not_supported']} marked verified without support |",
         f"| Misleading-offer detection | precision {m['misleading_offers']['metric']['precision']}, recall {m['misleading_offers']['metric']['recall']}; "
         f"{m['misleading_offers']['metric']['cases_exact']}/{m['misleading_offers']['metric']['cases']} cases exact; "
         f"{m['misleading_offers']['metric']['controls_clean']}/{m['misleading_offers']['metric']['controls']} honest controls clean |",
         f"| Landed-cost accuracy | {m['landed_cost']['metric']['exact_to_the_cent']}/{m['landed_cost']['metric']['cases']} exact to the cent "
         f"(max error {m['landed_cost']['metric']['max_abs_error']}) |",
         f"| Ranking changes under constraints | {m['ranking_changes']['metric']['cases_passed']}/{m['ranking_changes']['metric']['cases']} |",
         f"| End-to-end completion | {m['end_to_end']['metric']['completed']}/{m['end_to_end']['metric']['missions']} missions |", ""]
    L.append("## Failures")
    any_fail = False
    for cat, data in m.items():
        for r in data["rows"]:
            if not r["pass"]:
                any_fail = True
                L.append(f"- **{cat}**: `{json.dumps({k: v for k, v in r.items() if k != 'pass'}, ensure_ascii=False)[:400]}`")
    if not any_fail:
        L.append("None.")
    L += ["", "## End-to-end missions", "", "| Mission | Offers | Recommended | Provisional | Seconds |", "|---|---|---|---|---|"]
    for r in m["end_to_end"]["rows"]:
        L.append(f"| {r['mission']} | {r['offers']} | {r['recommended']} | {r['provisional']} | {r['seconds']} |")
    return "\n".join(L) + "\n"


def main() -> int:
    t0 = time.time()
    missions, timings = {}, {}
    for k in MISSIONS:
        missions[k], timings[k] = run_mission_sync(k)
    res = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "python": platform.python_version(),
           "caveat": CASES["_note"],
           "categories": {"requirement_matching": eval_requirements(), "claim_verification": eval_claims(),
                          "misleading_offers": eval_misleading(), "landed_cost": eval_landed(),
                          "ranking_changes": eval_ranking(missions), "end_to_end": eval_e2e(missions, timings)}}
    res["seconds"] = round(time.time() - t0, 1)
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / "latest.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    md = to_markdown(res)
    (out / "latest.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
