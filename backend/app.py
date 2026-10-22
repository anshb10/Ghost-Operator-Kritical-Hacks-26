import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import audit, config, discovery, memory, planner
from .engine import analyze
from .exports import decision_report, offers_csv, quote_request
from .models import Offer, Requirements
from .pipeline import MISSIONS, MISSIONS_DIR, Mission, get_mission, run_mission
from .purchase_order import render_po
from .scoring import WEIGHTS, normalize_weights
from .simulate import simulate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ghost.app")


@asynccontextmanager
async def lifespan(_app):
    try:  # index saved missions into procurement memory (idempotent, fast)
        n = await asyncio.to_thread(memory.sync_from_disk, MISSIONS_DIR)
        log.info("procurement memory: indexed %d saved missions", n)
    except Exception as e:
        log.warning("memory sync failed: %s", e)
    yield


app = FastAPI(title="Ghost Operator", lifespan=lifespan)
_tasks: set[asyncio.Task] = set()
Mode = Literal["live", "replay"]


class PlanIn(BaseModel):
    objective: str
    mode: Optional[Mode] = None


class VendorIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9]{1,40}$")
    name: str = Field(max_length=80)
    url: str = Field(pattern=r"^https://[^\s]{4,500}$")
    hint: str = ""
    discovered: bool = True


class MissionIn(BaseModel):
    objective: str
    requirements: Requirements
    vendor_keys: list[str]
    extra_vendors: list[VendorIn] = []
    planner: str = "user-edited"
    mode: Optional[Mode] = None  # replay a mission on a live server (fixtures); live needs a TinyFish key


MIN_OVERRIDE_REASON = 3  # characters, after trimming


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    offer_id: Optional[str] = None
    approver: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=1000)
    weights: Optional[dict[str, float]] = None  # what-if weights in effect when the buyer decided
    scenario: Optional[dict] = None  # counterfactual requirements in effect when the buyer decided


class RerankIn(BaseModel):
    weights: dict[str, float]


class ScenarioIn(BaseModel):
    budget_total: Optional[float] = None
    deadline_days: Optional[int] = Field(default=None, ge=0, le=3650)
    quantity: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    tax_rate_pct: Optional[float] = Field(default=None, ge=0, le=100)
    constraints: Optional[list[dict]] = None
    weights: Optional[dict[str, float]] = None


class OutcomeIn(BaseModel):
    delivered_on: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    on_time: Optional[bool] = None
    actual_unit_price: Optional[float] = Field(default=None, gt=0)
    notes: str = Field(default="", max_length=1000)
    recorded_by: str = Field(default="", max_length=120)


def _mode(requested: Optional[str]) -> str:
    if requested == "live" and not config.TINYFISH_API_KEY:
        raise HTTPException(400, "live mode needs TINYFISH_API_KEY")
    return requested or config.effective_mode()


@app.get("/api/info")
def info():
    domains = config.load_domains()
    try:
        mem = memory.stats()
    except Exception:
        mem = None
    return {"mode": config.effective_mode(), "modes": ["live", "replay"] if config.TINYFISH_API_KEY else ["replay"],
            "concurrency": config.TINYFISH_CONCURRENCY, "model": config.ANTHROPIC_MODEL,
            "weights": WEIGHTS, "min_override_reason": MIN_OVERRIDE_REASON, "memory": mem,
            "domains": {k: {"label": d["label"], "vendors": d["vendors"], "currency": d.get("currency", "USD"),
                            "tax": d.get("tax"), "attributes": list(d["attributes"])} for k, d in domains.items()}}


def _recorded(domain: str, key: str) -> bool:
    return any((base / domain / f"{key}.json").exists() for base in (config.REPLAY_DIR, config.RECORD_DIR))


@app.post("/api/plan")
async def plan(body: PlanIn):
    domains = config.load_domains()
    mode = _mode(body.mode)
    req, used = await asyncio.to_thread(planner.plan, body.objective, domains)
    known = [dict(v) for v in domains[req.domain]["vendors"] if mode == "replay" or not v.get("fixture_only")]
    found = await discovery.discover(req, known, mode)
    for v in known + found["vendors"]:  # in replay mode only vendors with a recorded agent run can be dispatched
        v["recorded"] = mode == "live" or _recorded(req.domain, v["key"])
    return {"requirements": req.model_dump(), "planner": used, "vendors": known, "discovery": found, "mode": mode}


@app.post("/api/missions")
async def create_mission(body: MissionIn):
    domains = config.load_domains()
    if body.requirements.domain not in domains:
        raise HTTPException(400, "unknown domain")
    mode = _mode(body.mode)
    vendors = [v for v in domains[body.requirements.domain]["vendors"] if v["key"] in body.vendor_keys
               and (mode == "replay" or not v.get("fixture_only"))]
    known = {v["key"] for v in vendors}
    vendors += [v.model_dump() for v in body.extra_vendors if v.key not in known][:4]
    if not vendors:
        raise HTTPException(400, "pick at least one vendor")
    m = Mission(body.requirements, vendors, body.objective, body.planner, mode)
    m.log("buyer", "mission.created", objective=body.objective, requirements=body.requirements.model_dump(),
          vendors=[{k: v.get(k) for k in ("key", "name", "url")} for v in vendors], mode=mode, planner=body.planner)
    MISSIONS[m.id] = m
    t = asyncio.create_task(run_mission(m))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return {"id": m.id}


def _brief(d: dict) -> dict:
    rep, dec, req = d.get("report") or {}, d.get("decision") or {}, d["requirements"]
    pick_id = dec.get("offer_id") or rep.get("recommended_offer_id")
    o = next((x for x in rep.get("offers", []) if x["id"] == pick_id), None)
    unit = o and (o.get("effective_unit_price") or o.get("unit_price"))
    landed = o and (o.get("landed") or {}).get("total")
    return {"id": d["id"], "created": d["created"], "objective": d["objective"], "status": d["status"], "mode": d["mode"],
            "domain": req["domain"], "item": req["item"], "quantity": req["quantity"],
            "currency": req.get("currency", "USD"), "budget_total": req.get("budget_total", req.get("budget_total_usd")),
            "n_offers": len(rep.get("offers", [])), "provenance": rep.get("provenance", []),
            "pick": {"name": o["name"], "vendor": o["vendor"], "url": o.get("url", ""),
                     "total": landed if landed is not None else (round(unit * req["quantity"], 2) if unit else None),
                     "landed": landed is not None} if o else None,
            "approver": dec.get("approver") or None}


@app.get("/api/missions")
def list_missions(limit: int = 50):
    """Saved missions (data/missions/) plus any still running in memory, newest first."""
    seen: dict[str, dict] = {}
    for path in MISSIONS_DIR.glob("*.json") if MISSIONS_DIR.exists() else []:
        try:
            d = json.loads(path.read_text())
            seen[d["id"]] = _brief(d)
        except (ValueError, KeyError, TypeError):
            continue
    for mid, m in MISSIONS.items():
        seen[mid] = _brief(m.snapshot())
    return sorted(seen.values(), key=lambda b: b["created"], reverse=True)[:max(1, min(limit, 200))]


def _get(mid: str) -> Mission:
    m = get_mission(mid)
    if not m:
        raise HTTPException(404, "mission not found")
    return m


def _need_report(m: Mission) -> dict:
    if not m.report:
        raise HTTPException(409, "mission has no recommendation yet")
    return m.report


@app.get("/api/missions/{mid}")
def mission_detail(mid: str):
    return _get(mid).snapshot()


@app.get("/api/missions/{mid}/events")
async def events(mid: str, since: int = 0):
    m = _get(mid)

    async def gen():
        seq = since
        while True:
            while seq < len(m.events):
                yield f"data: {json.dumps(m.events[seq])}\n\n"
                seq += 1
            if m.status in ("awaiting_approval", "approved", "rejected", "failed") and seq >= len(m.events):
                yield "event: end\ndata: {}\n\n"
                return
            if not await m.wait_for(seq):
                yield ": keepalive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _domain_cfg(m: Mission) -> dict:
    return config.load_domains()[m.req.domain]


@app.post("/api/missions/{mid}/decision")
def decide(mid: str, body: DecisionIn):
    m = _get(mid)
    rep = _need_report(m)
    if m.decision:
        raise HTTPException(409, "this mission already has a recorded decision")
    ids = {o["id"] for o in rep["offers"]}
    offer_id = body.offer_id or rep["recommended_offer_id"]
    if offer_id not in ids:
        raise HTTPException(400, "unknown offer")
    sim = None
    if body.scenario:  # the buyer decided under revised requirements: check eligibility under those
        sim = simulate(rep["offers"], m.req, _domain_cfg(m), body.scenario)
        eligible = next((o for o in sim["offers"] if o["id"] == offer_id), {}).get("rank")
        scenario_pick = (sim["after"] or {}).get("offer_id")
    else:
        eligible = next(o for o in rep["offers"] if o["id"] == offer_id).get("rank")
        scenario_pick = None
    if body.decision == "approve" and not eligible:
        raise HTTPException(400, "that offer failed a must-have requirement and can't be approved")
    note = body.note.strip()
    override = offer_id not in (rep["recommended_offer_id"], scenario_pick)
    if body.decision == "approve" and override and len(note) < MIN_OVERRIDE_REASON:
        raise HTTPException(422, "approving an offer other than the recommendation needs a reason "
                                 f"(at least {MIN_OVERRIDE_REASON} characters); it is printed on the purchase order")
    m.decision = {**body.model_dump(exclude={"scenario"}), "note": note,
                  "override": body.decision == "approve" and override,
                  "offer_id": offer_id, "at": datetime.now(timezone.utc).isoformat()}
    if body.weights:
        m.decision["weights"] = normalize_weights(body.weights)
    if sim:
        m.decision["scenario"] = {"input": body.scenario, "changes": sim["changes"], "requirements": sim["requirements"],
                                  "recommended_offer_id": scenario_pick}
    m.status = "approved" if body.decision == "approve" else "rejected"
    o = next(x for x in rep["offers"] if x["id"] == offer_id)
    m.log(f"human:{body.approver or 'buyer'}", f"decision.{body.decision}", offer_id=offer_id, vendor=o["vendor"],
          name=o["name"], landed=o.get("landed", {}).get("total"), note=note, override=m.decision["override"],
          weights=m.decision.get("weights"), scenario=(m.decision.get("scenario") or {}).get("changes"))
    m.save()
    return m.snapshot()


@app.post("/api/missions/{mid}/rerank")
def rerank(mid: str, body: RerankIn):
    """What-if on weights only (kept for compatibility; /simulate covers requirements too). Nothing is saved."""
    m = _get(mid)
    rep = _need_report(m)
    res = analyze([Offer(**o) for o in rep["offers"]], m.req, _domain_cfg(m), body.weights)
    d = res["decision"]
    return {"weights": normalize_weights(body.weights), "default_weights": WEIGHTS,
            "recommended_offer_id": d["recommended_offer_id"], "value_pick_id": d["value_pick_id"],
            "offers": [{"id": o.id, "score": o.score, "rank": o.rank} for o in res["ranked"]]}


@app.post("/api/missions/{mid}/simulate")
def simulate_scenario(mid: str, body: ScenarioIn):
    """Counterfactual: same offers, same engine, different requirements/weights. Nothing is saved."""
    m = _get(mid)
    rep = _need_report(m)
    try:
        return simulate(rep["offers"], m.req, _domain_cfg(m), body.model_dump(exclude_unset=True))
    except (ValueError, TypeError) as e:
        raise HTTPException(422, f"invalid scenario: {e}")


@app.get("/api/missions/{mid}/audit")
def audit_trail(mid: str):
    m = _get(mid)
    return {"mission_id": m.id, "chain": audit.verify_chain(m.audit), "entries": m.audit}


@app.get("/api/missions/{mid}/audit.json")
def audit_download(mid: str):
    m = _get(mid)
    body = json.dumps({"mission_id": m.id, "chain": audit.verify_chain(m.audit), "entries": m.audit}, indent=1)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="ghost-operator-{m.id}-audit.json"'})


@app.post("/api/missions/{mid}/outcome")
def record_outcome(mid: str, body: OutcomeIn):
    m = _get(mid)
    if not m.decision or m.decision.get("decision") != "approve":
        raise HTTPException(409, "outcomes can only be recorded for an approved mission")
    o = next(x for x in m.report["offers"] if x["id"] == m.decision["offer_id"])
    row = memory.record_outcome(m.id, o["vendor"], m.req.currency, body.model_dump())
    m.log(f"human:{body.recorded_by or 'buyer'}", "outcome.recorded", **{k: v for k, v in row.items() if k != "mission_id"})
    m.save()
    return {"outcome": row, "outcomes": memory.outcomes(m.id)}


@app.get("/api/missions/{mid}/outcomes")
def list_outcomes(mid: str):
    return memory.outcomes(_get(mid).id)


@app.get("/api/missions/{mid}/history")
def mission_history(mid: str):
    """Fresh comparison of each offer against stored observations (memory grows after the mission ran)."""
    m = _get(mid)
    rep = _need_report(m)
    return memory.history_for(m.snapshot(), rep["offers"])


@app.get("/api/memory/stats")
def memory_stats():
    return memory.stats()


def _offer(m: Mission, offer_id: str) -> dict:
    o = next((x for x in (m.report or {}).get("offers", []) if x["id"] == offer_id), None)
    if not o:
        raise HTTPException(404, "offer not found")
    return o


@app.get("/api/missions/{mid}/offers.csv")
def export_csv(mid: str):
    m = _get(mid)
    if not m.report:
        raise HTTPException(409, "mission has no offers yet")
    return Response(offers_csv(m), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="ghost-operator-{m.id}-offers.csv"'})


@app.get("/api/missions/{mid}/offers/{offer_id}/quote-request")
def quote(mid: str, offer_id: str):
    m = _get(mid)
    return quote_request(m, _offer(m, offer_id))


@app.get("/missions/{mid}/po", response_class=HTMLResponse)
def po(mid: str, offer: Optional[str] = None):
    m = _get(mid)
    if offer:
        _offer(m, offer)
    return render_po(m, offer)


@app.get("/missions/{mid}/report", response_class=HTMLResponse)
def report_page(mid: str):
    m = _get(mid)
    _need_report(m)
    return decision_report(m, audit.verify_chain(m.audit))


app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
