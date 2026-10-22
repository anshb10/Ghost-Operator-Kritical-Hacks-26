"""Mission orchestration: plan -> parallel TinyFish agents -> verification -> analysis -> recommendation -> human approval.

Stages and who owns them:
  sourcing agents     TinyFish Web Agents, one per vendor (this file)
  verification agent  TinyFish Fetch + claims ledger + adversarial checks (verify.py, evidence.py, adversarial.py)
  ML                  entity resolution and fair-price model (ml/)
  cost / risk / decision  engine.analyze → landed_cost.py, risk.py, decision.py
  memo                Claude (optional) explains the decision; grounding.py rejects figures it didn't get from us
Every stage appends to the mission's hash-chained audit log (audit.py).
"""
import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

from . import audit, config, grounding, memory, planner, tinyfish, verify
from .engine import analyze, roles_summary
from .exports import money
from .ml import matching, normalize, pricing
from .models import Offer, Requirements

log = logging.getLogger("ghost.pipeline")
PRODUCTS_PER_VENDOR = 4


class Mission:
    def __init__(self, req: Requirements, vendors: list[dict], objective: str, planner_used: str, mode: str | None = None):
        self.id = uuid.uuid4().hex[:8]
        self.created = datetime.now(timezone.utc).isoformat()
        self.objective = objective
        self.req = req
        self.vendors = vendors
        self.planner_used = planner_used
        self.mode = mode or config.effective_mode()
        self.status = "browsing"
        self.agents = {v["key"]: {"vendor": v["name"], "url": v["url"], "status": "queued", "steps": [],
                                  "stream_url": None, "run_id": None, "n_offers": 0, "error": None, "provenance": None}
                       for v in vendors}
        self.offers: list[Offer] = []
        self.report: dict | None = None
        self.decision: dict | None = None
        self.audit: list[dict] = []
        self.events: list[dict] = []
        self._cond = asyncio.Condition()

    async def emit(self, kind: str, **data):
        async with self._cond:
            self.events.append({"seq": len(self.events), "kind": kind, "t": time.time(), **data})
            self._cond.notify_all()

    async def wait_for(self, seq: int, timeout: float = 15.0) -> bool:
        async with self._cond:
            try:
                await asyncio.wait_for(self._cond.wait_for(lambda: len(self.events) > seq), timeout)
                return True
            except asyncio.TimeoutError:
                return False

    def log(self, actor: str, action: str, **data) -> None:
        audit.append(self.audit, actor, action, data)

    def snapshot(self) -> dict:
        return {"id": self.id, "objective": self.objective, "created": self.created, "mode": self.mode,
                "status": self.status, "planner": self.planner_used, "requirements": self.req.model_dump(),
                "agents": self.agents, "report": self.report, "decision": self.decision}

    def save(self) -> None:
        MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
        snap = self.snapshot()
        (MISSIONS_DIR / f"{self.id}.json").write_text(json.dumps({**snap, "vendors": self.vendors, "audit": self.audit}))
        try:
            memory.record_mission(snap)
        except Exception as e:  # memory is an index over the mission files; never lose a mission because of it
            log.warning("memory write failed for %s: %s", self.id, e)

    @classmethod
    def load(cls, mid: str) -> "Mission | None":
        path = MISSIONS_DIR / f"{mid}.json"
        if not mid.isalnum() or not path.exists():
            return None
        d = json.loads(path.read_text())
        m = cls(Requirements(**d["requirements"]), d["vendors"], d["objective"], d["planner"], d["mode"])
        m.id, m.created, m.status = d["id"], d["created"], d["status"]
        m.agents, m.report, m.decision = d["agents"], d["report"], d["decision"]
        m.audit = d.get("audit", [])
        m.events = [{"seq": 0, "kind": "report", "t": time.time(), "status": m.status}] if m.report else []
        return m


MISSIONS_DIR = config.DATA_DIR / "missions"
MISSIONS: dict[str, Mission] = {}


def get_mission(mid: str) -> "Mission | None":
    if mid not in MISSIONS:
        m = Mission.load(mid)
        if m:
            MISSIONS[mid] = m
    return MISSIONS.get(mid)


async def _run_agent(m: Mission, v: dict, domain_cfg: dict, sem: asyncio.Semaphore) -> list[Offer]:
    a = m.agents[v["key"]]
    goal = domain_cfg["goal_template"].format(item=m.req.item, criteria=planner.criteria_text(m.req),
                                              n=PRODUCTS_PER_VENDOR, hint=v.get("hint", ""))
    async with sem:
        a["status"] = "running"
        await m.emit("agent", vendor=v["key"], status="running", goal=goal)
        started, steps = time.time(), []
        stream = tinyfish.run_live(v["url"], goal) if m.mode == "live" else tinyfish.run_replay(m.req.domain, v["key"])
        try:
            async for ev in stream:
                if ev["type"] == "started":
                    a["run_id"] = ev["run_id"]
                elif ev["type"] == "stream":
                    a["stream_url"] = ev["url"]
                    await m.emit("agent", vendor=v["key"], status="running", stream_url=ev["url"])
                elif ev["type"] == "progress":
                    steps.append(ev["message"])
                    a["steps"].append(ev["message"])
                    await m.emit("agent_step", vendor=v["key"], message=ev["message"])
                elif ev["type"] == "complete":
                    offers = normalize.normalize_offers(v["name"], ev["result"], domain_cfg)
                    source = f"TinyFish run {a['run_id']} at {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
                    prov = ev.get("provenance") or tinyfish.provenance(source, live=True)
                    for o in offers:
                        o.provenance = prov
                    a.update(status="done", n_offers=len(offers), provenance=prov)
                    if m.mode == "live":
                        tinyfish.record(m.req.domain, v["key"], steps, ev["result"], started, source)
                    m.log(f"sourcing-agent:{v['key']}", "sourcing.completed", vendor=v["name"], url=v["url"],
                          offers=len(offers), steps=len(steps), provenance=prov,
                          extracted=[{"id": o.id, "name": o.name, "unit_price": o.unit_price, "url": o.url} for o in offers])
                    await m.emit("agent", vendor=v["key"], status="done", n_offers=len(offers),
                                 seconds=round(time.time() - started), provenance=prov["kind"])
                    return offers
                elif ev["type"] == "error":
                    raise RuntimeError(ev["message"])
            raise RuntimeError("agent ended without a result")
        except Exception as e:
            log.warning("agent %s failed: %s", v["key"], e)
            a.update(status="failed", error=str(e)[:300])
            m.log(f"sourcing-agent:{v['key']}", "sourcing.failed", vendor=v["name"], error=a["error"])
            await m.emit("agent", vendor=v["key"], status="failed", error=a["error"])
            return []


def pick_recommendation(ranked: list[Offer]) -> tuple[Offer | None, Offer | None]:
    """Kept for callers of the old API: (recommended, value pick) under the evidence policy in decision.py."""
    from .decision import decide
    d = decide(ranked)
    by_id = {o.id: o for o in ranked}
    return by_id.get(d["recommended_offer_id"]), by_id.get(d["value_pick_id"])


def _money(v: float | None, cur: str) -> str:
    return money(v, cur, 0)


def _template_summary(req: Requirements, ranked: list[Offer], decision: dict) -> str:
    by_id = {o.id: o for o in ranked}
    best, value = by_id.get(decision["recommended_offer_id"]), by_id.get(decision["value_pick_id"])
    cur = req.currency
    ok = [o for o in ranked if o.rank]
    if not ok:
        from collections import Counter
        reasons = Counter("landed total over budget" if f.startswith("landed total") else
                          "unit price over budget" if f.startswith("unit price") else
                          "can't meet the deadline" if f.startswith("delivery") else f.split(" (")[0]
                          for o in ranked for f in o.failed_constraints if not f.startswith("unverified"))
        why = "; ".join(f"{r} ({n} offers)" for r, n in reasons.most_common(2))
        return (f"No option met every must-have requirement. Most common blockers: {why or 'missing prices'}. "
                "Loosening that constraint or adding vendors may surface a viable choice.")
    lc = best.landed or {}
    total = lc.get("total")
    s = (f"{'Provisionally recommend' if decision['provisional'] else 'Recommend'} {req.quantity} × {best.name} from "
         f"{best.vendor}: landed total {_money(total, cur)} ({_money(lc.get('per_unit'), cur)}/unit, {lc.get('confidence', 'n/a')}"
         + (f"; not stated: {', '.join(lc['unknowns'])}" if lc.get("unknowns") else "") + "). ")
    if req.budget_total and total is not None:
        s += f"That is {_money(abs(req.budget_total - total), cur)} {'under' if total <= req.budget_total else 'over'} budget. "
    if best.verification.get("status") == "verified":
        s += "Price and specs were re-checked on the vendor's product page. "
    if value:
        from .decision import blockers
        s += (f"{value.vendor}'s {value.name.split(' (')[0]} scores higher ({value.score:.0f} vs {best.score:.0f}), but "
              f"{'; '.join(blockers(value))}; request a written quote before switching. ")
    runner = next((o for o in ok if o is not best and o is not value), None)
    if runner:
        s += (f"The runner-up is {runner.vendor}'s {runner.name.split(' (')[0]} at "
              f"{_money((runner.landed or {}).get('total'), cur)} landed, scoring {runner.score:.0f} vs {best.score:.0f}"
              + (f", but it is {re.sub(r'(?i)^item ', '', runner.stock).lower()}. "
                 if re.search(r"(?i)backorder|sold out", runner.stock or "") else ". "))
    trapped = [o for o in ranked if any(f["code"] == "BULK_CAP" for f in o.flags) and o is not value and o.effective_unit_price]
    if trapped:
        t = min(trapped, key=lambda o: o.unit_price)
        where = "a listing of the same model caps" if t.verification["limit"].get("inferred_from") else "its product page caps"
        s += (f"Note: {t.vendor}'s {_money(t.unit_price, cur)} {t.name.split(' (')[0]} looks cheapest, but {where} that "
              f"promo at {t.verification['limit']['max_units']} units per order, so the expected average is "
              f"{_money(t.effective_unit_price, cur)}. ")
    bad = [f for f in best.flags if f["severity"] in ("high", "medium")]
    if bad:
        s += f"Open finding on the pick: {bad[0]['title'].lower()}. "
    elif best.warranty_risk_reasons:
        s += f"Main risk: {best.warranty_risk_reasons[0][0].lower() + best.warranty_risk_reasons[0][1:]}. "
    if req.domain.startswith("laptops") and (best.warranty.service != "onsite" or (best.warranty.years or 0) < 3):
        s += "Ask the vendor to quote a warranty extension or onsite upgrade before approving. "
    return s.strip()


def _compact_flags(offers: list[Offer]) -> list[dict]:
    return [{"offer_id": o.id, "vendor": o.vendor, "code": f["code"], "severity": f["severity"], "title": f["title"]}
            for o in offers for f in o.flags if f["severity"] in ("high", "medium")]


async def run_mission(m: Mission) -> None:
    domain_cfg = config.load_domains()[m.req.domain]
    try:
        sem = asyncio.Semaphore(config.TINYFISH_CONCURRENCY if m.mode == "live" else 8)
        results = await asyncio.gather(*[_run_agent(m, v, domain_cfg, sem) for v in m.vendors])
        offers = [o for batch in results for o in batch]

        m.status = "analyzing"
        await m.emit("status", status="analyzing")
        await m.emit("ml", step="normalize", message=f"Normalized {len(offers)} offers from "
                     f"{sum(1 for r in results if r)} vendors into canonical specs")

        vsum = await verify.verify_offers(offers, m.req.domain, m.mode, m.req.quantity)
        cl = vsum.get("claims", {})
        await m.emit("verify", step="verify", message=f"TinyFish Fetch API re-read {vsum['pages_fetched']} product pages: "
                     f"{vsum['verified']} verified, {vsum['partial']} partly verified, {vsum['mismatch']} mismatched, {vsum['unverified']} unverifiable, "
                     f"{vsum['corrections']} correction(s) applied", summary=vsum)
        m.log("verification-agent", "verification.completed", summary=vsum,
              contradicted=[{"offer_id": o.id, "field": c["field"], "claimed": c["claimed"], "observed": c["observed"],
                             "source_url": c["source_url"], "snippet": c["snippet"]}
                            for o in offers for c in o.claims if c["status"] == "contradicted"])
        await m.emit("ml", step="claims", message=f"Claims ledger: {cl.get('verified', 0)} verified, "
                     f"{cl.get('contradicted', 0)} contradicted, {cl.get('inferred', 0)} inferred, {cl.get('unknown', 0)} unknown; "
                     f"{vsum.get('flags', 0)} adversarial finding(s)")

        groups = matching.group_offers(offers)
        await m.emit("ml", step="match", message=f"Entity resolution (TF-IDF char n-grams + spec agreement): "
                     f"{len(groups)} product(s) found at multiple listings")

        price_info = pricing.price_offers(offers, domain_cfg["price_features"])
        n_anom = sum(1 for o in offers if o.anomaly)
        await m.emit("ml", step="price", message=f"Fair-price model ({price_info['method']}): {n_anom} anomaly flag(s)")

        result = analyze(offers, m.req, domain_cfg)
        ranked, decision = result["ranked"], result["decision"]
        await m.emit("ml", step="risk", message="Landed cost + supplier risk radar (evidence, spec, delivery, warranty, "
                     "price transparency, integrity)")
        m.log("cost-analyst", "cost.landed", currency=m.req.currency,
              offers=[{"offer_id": o.id, "total": o.landed.get("total"), "confidence": o.landed.get("confidence"),
                       "unknowns": o.landed.get("unknowns")} for o in ranked if o.landed])
        m.log("risk-analyst", "risk.assessed", findings=_compact_flags(ranked),
              radar=[{"offer_id": o.id, "overall": o.risk.get("overall"), "level": o.risk.get("level")} for o in ranked])
        m.offers = ranked
        n_ok = sum(1 for o in ranked if o.rank)
        await m.emit("ml", step="rank", message=f"Ranked {n_ok} eligible offers by fit, lifetime cost, supplier risk, "
                     f"price sanity and source trust; {len(ranked) - n_ok} excluded")
        from .scoring import WEIGHTS
        m.log("ranking", "ranking.completed", weights=WEIGHTS,
              ranking=[{"offer_id": o.id, "rank": o.rank, "score": o.score, "parts": o.score_parts,
                        "failed": o.failed_constraints} for o in ranked])
        m.log("decision-agent", "decision.recommended", policy=decision["policy"], trace=decision["trace"],
              why=decision["why"], recommended_offer_id=decision["recommended_offer_id"],
              value_pick_id=decision["value_pick_id"], provisional=decision["provisional"])

        deltas = pricing.cross_vendor_deltas(ranked)
        families = matching.family_matches(ranked)
        insights = {"cross_vendor": deltas, "families": families, "price_model": price_info, "groups": groups,
                    "verification": vsum}
        best = next((o for o in ranked if o.id == decision["recommended_offer_id"]), None)
        offers_out = [o.model_dump() for o in ranked]
        top = [{k: v for k, v in o.items() if k not in ("evidence", "claims", "risk", "history")} for o in offers_out[:4]]
        top.insert(0, {"recommended_offer_id": decision["recommended_offer_id"], "why": decision["why"],
                       "value_pick_needing_quote": decision["value_pick_id"], "policy": decision["policy"]})
        memo_check = None
        try:
            summary = await asyncio.to_thread(planner.summarize_with_claude, m.req, top,
                                              {"cross_vendor": deltas, "families": families[:3]})
            memo_check = grounding.check_memo(summary, grounding.allowed_figures(offers_out, m.req))
            if not memo_check["ok"]:
                raise RuntimeError(f"memo cites figures not produced by the cost engine: {memo_check['unsupported']}")
            summary_by = "claude"
        except Exception as e:
            log.info("summary fallback: %s", e)
            summary, summary_by = _template_summary(m.req, ranked, decision), "template"
        m.log("memo", "memo.drafted", by=summary_by, grounding=memo_check)

        landed_best = (best.landed or {}).get("total") if best else None
        m.report = {
            "summary": summary, "summary_by": summary_by, "memo_check": memo_check,
            "recommended_offer_id": decision["recommended_offer_id"],
            "value_pick_id": decision["value_pick_id"],
            "decision": decision,
            "roles": roles_summary(m.agents, vsum, ranked, decision),
            "currency": m.req.currency,
            "provenance": sorted({(o.provenance or {}).get("kind", "unknown") for o in ranked}),
            "offers": offers_out,
            "insights": insights,
            "budget_ok": (landed_best <= m.req.budget_total) if landed_best is not None and m.req.budget_total else None,
        }
        try:
            hist = memory.history_for(m.snapshot(), m.report["offers"])
            for o in m.report["offers"]:
                o["history"] = hist.get(o["id"], {})
        except Exception as e:
            log.warning("history lookup failed: %s", e)
        m.status = "awaiting_approval"
        m.save()
        await m.emit("report", status=m.status)
    except Exception as e:
        log.exception("mission failed")
        m.status = "failed"
        m.log("system", "mission.failed", error=str(e)[:500])
        m.save()
        await m.emit("status", status="failed", error=str(e))
