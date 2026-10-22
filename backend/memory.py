"""Procurement memory: missions, price observations, decisions and outcomes in SQLite (data/ghost.db).

An observation is one price seen for one listing at one time, keyed by where it came from. Replaying the same
recording in ten missions is one observation seen ten times, not ten data points, so history never inflates.
Comparable configurations share a key: model family + the specs that drive price (RAM, storage, CPU tier, pack size,
thickness), in the same currency.
"""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from . import config
from .ml.matching import _SPEC_KEYS, _family

SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (id TEXT PRIMARY KEY, created TEXT, objective TEXT, domain TEXT, item TEXT,
  quantity INTEGER, currency TEXT, budget REAL, mode TEXT, status TEXT, recommended_offer_id TEXT);
CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, observed_at TEXT, kind TEXT, source TEXT, domain TEXT,
  config_key TEXT, vendor TEXT, name TEXT, url TEXT, currency TEXT, unit_price REAL, verification TEXT);
CREATE TABLE IF NOT EXISTS mission_observations (mission_id TEXT, observation_id TEXT, offer_id TEXT,
  PRIMARY KEY (mission_id, observation_id));
CREATE TABLE IF NOT EXISTS decisions (mission_id TEXT PRIMARY KEY, at TEXT, decision TEXT, offer_id TEXT, vendor TEXT,
  approver TEXT, note TEXT, override INTEGER, landed_total REAL, currency TEXT);
CREATE TABLE IF NOT EXISTS outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT, at TEXT, vendor TEXT,
  delivered_on TEXT, on_time INTEGER, actual_unit_price REAL, currency TEXT, notes TEXT, recorded_by TEXT);
CREATE INDEX IF NOT EXISTS obs_key ON observations (config_key, currency);
"""


def _db() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DATA_DIR / "ghost.db")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def config_key(o: dict, domain: str) -> str:
    a = o.get("attributes") or {}
    specs = "|".join(f"{k}={a[k]:g}" for k in _SPEC_KEYS if isinstance(a.get(k), (int, float)))
    return f"{domain}|{_family(o['name'])}|{specs}"


def _observation(o: dict, snap: dict) -> dict | None:
    if not o.get("unit_price"):
        return None
    prov = o.get("provenance") or {}
    kind = prov.get("kind") or ("live" if snap.get("mode") == "live" else "recorded")
    observed = (o.get("verification") or {}).get("retrieved_at") or (snap["created"] if kind == "live" else None)
    if not observed:
        from .tinyfish import recorded_date
        observed = recorded_date(prov.get("source", "")) or "undated"
    source = prov.get("source") or ("TinyFish live run" if kind == "live" else "replay recording (pre-upgrade mission)")
    # A recording is static, so the same listing+price from it is one observation however often it is replayed (and
    # whether or not the mission predates provenance labels). Live observations are distinct per day.
    when = observed[:10] if kind == "live" else ""
    oid = hashlib.sha1(f"{kind}|{o.get('url')}|{o['name']}|{o['unit_price']}|{when}".encode()).hexdigest()[:16]
    return {"id": oid, "observed_at": observed, "kind": kind, "source": source, "domain": snap["requirements"]["domain"],
            "config_key": config_key(o, snap["requirements"]["domain"]), "vendor": o["vendor"], "name": o["name"],
            "url": o.get("url", ""), "currency": snap["requirements"].get("currency", "USD"), "unit_price": o["unit_price"],
            "verification": (o.get("verification") or {}).get("status", "")}


def observation_ids(snap: dict) -> dict[str, str]:
    """offer_id → observation_id for a mission snapshot."""
    out = {}
    for o in (snap.get("report") or {}).get("offers", []):
        ob = _observation(o, snap)
        if ob:
            out[o["id"]] = ob["id"]
    return out


def record_mission(snap: dict) -> None:
    rep, req = snap.get("report") or {}, snap["requirements"]
    with closing(_db()) as con, con:
        con.execute("INSERT OR REPLACE INTO missions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (snap["id"], snap["created"], snap["objective"], req["domain"], req["item"], req["quantity"],
                     req.get("currency", "USD"), req.get("budget_total", req.get("budget_total_usd")), snap["mode"],
                     snap["status"], rep.get("recommended_offer_id")))
        for o in rep.get("offers", []):
            ob = _observation(o, snap)
            if not ob:
                continue
            con.execute("INSERT INTO observations VALUES (:id,:observed_at,:kind,:source,:domain,:config_key,"
                        ":vendor,:name,:url,:currency,:unit_price,:verification) ON CONFLICT(id) DO UPDATE SET "
                        "observed_at=excluded.observed_at, source=excluded.source, config_key=excluded.config_key, "
                        "verification=excluded.verification WHERE observations.observed_at='undated'", ob)
            con.execute("INSERT OR IGNORE INTO mission_observations VALUES (?,?,?)", (snap["id"], ob["id"], o["id"]))
        d = snap.get("decision")
        if d:
            o = next((x for x in rep.get("offers", []) if x["id"] == d.get("offer_id")), {})
            con.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (snap["id"], d.get("at"), d.get("decision"), d.get("offer_id"), o.get("vendor"), d.get("approver"),
                         d.get("note"), int(bool(d.get("override"))), (o.get("landed") or {}).get("total"),
                         req.get("currency", "USD")))


def record_outcome(mission_id: str, vendor: str, currency: str, outcome: dict) -> dict:
    row = {"mission_id": mission_id, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "vendor": vendor,
           "delivered_on": outcome.get("delivered_on"), "on_time": outcome.get("on_time"),
           "actual_unit_price": outcome.get("actual_unit_price"), "currency": currency,
           "notes": outcome.get("notes", ""), "recorded_by": outcome.get("recorded_by", "")}
    with closing(_db()) as con, con:
        con.execute("INSERT INTO outcomes (mission_id, at, vendor, delivered_on, on_time, actual_unit_price, currency, notes,"
                    " recorded_by) VALUES (:mission_id,:at,:vendor,:delivered_on,:on_time,:actual_unit_price,:currency,"
                    ":notes,:recorded_by)", row)
    return row


def outcomes(mission_id: str) -> list[dict]:
    with closing(_db()) as con:
        return [dict(r) for r in con.execute("SELECT * FROM outcomes WHERE mission_id=? ORDER BY id", (mission_id,))]


def history_for(snap: dict, offers: list[dict]) -> dict[str, dict]:
    """For each offer: earlier observations of a comparable configuration (same key and currency), excluding this
    offer's own observation. Includes the buyer's own track record with the vendor."""
    req = snap["requirements"]
    cur, domain = req.get("currency", "USD"), req["domain"]
    own = observation_ids(snap)
    out = {}
    with closing(_db()) as con:
        for o in offers:
            if not o.get("unit_price"):
                continue
            rows = [dict(r) for r in con.execute(
                "SELECT o.*, (SELECT COUNT(*) FROM mission_observations m WHERE m.observation_id=o.id) AS seen_in "
                "FROM observations o WHERE config_key=? AND currency=? AND id != ? ORDER BY observed_at DESC",
                (config_key(o, domain), cur, own.get(o["id"], "")))]
            track = con.execute(
                "SELECT COUNT(*) AS approved, (SELECT COUNT(*) FROM outcomes WHERE vendor=?) AS outcomes, "
                "(SELECT SUM(on_time) FROM outcomes WHERE vendor=?) AS on_time FROM decisions "
                "WHERE vendor=? AND decision='approve' AND mission_id != ?",
                (o["vendor"], o["vendor"], o["vendor"], snap["id"])).fetchone()
            h = {"comparable": len(rows), "observations": rows[:6],
                 "vendor_record": {"approved_orders": track["approved"], "outcomes": track["outcomes"],
                                   "on_time": track["on_time"] or 0}}
            if rows:
                prices = [r["unit_price"] for r in rows]
                last = rows[0]
                h.update({"last": last, "min": min(prices), "max": max(prices),
                          "delta_vs_last_pct": round((o["unit_price"] - last["unit_price"]) / last["unit_price"], 3)})
            out[o["id"]] = h
    return out


def stats() -> dict:
    with closing(_db()) as con:
        q = lambda sql: con.execute(sql).fetchone()[0]
        return {"missions": q("SELECT COUNT(*) FROM missions"), "observations": q("SELECT COUNT(*) FROM observations"),
                "observations_by_kind": {r[0]: r[1] for r in con.execute("SELECT kind, COUNT(*) FROM observations GROUP BY kind")},
                "decisions": q("SELECT COUNT(*) FROM decisions"), "outcomes": q("SELECT COUNT(*) FROM outcomes"),
                "configurations": q("SELECT COUNT(DISTINCT config_key) FROM observations")}


def sync_from_disk(missions_dir) -> int:
    """Backfill from saved mission files (idempotent). Returns how many missions were read."""
    n = 0
    for path in sorted(missions_dir.glob("*.json")) if missions_dir.exists() else []:
        try:
            snap = json.loads(path.read_text())
            snap["requirements"].setdefault("currency", "USD")
            if snap.get("report"):
                record_mission(snap)
                n += 1
        except (ValueError, KeyError, TypeError, sqlite3.Error):
            continue
    return n
