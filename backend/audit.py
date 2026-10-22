"""Tamper-evident audit trail: an append-only, hash-chained log per mission.

Each entry records who (system stage or named human), what, when, and the data that drove it, plus the SHA-256 of
the previous entry. Editing any past entry breaks every hash after it, which `verify_chain` reports. This is evidence
of tampering, not prevention: anyone with write access to data/ can rewrite the whole chain.
"""
import hashlib
import json
from datetime import datetime, timezone

GENESIS = "0" * 64
_FIELDS = ("seq", "at", "actor", "action", "data", "prev_hash")


def _digest(entry: dict) -> str:
    body = json.dumps({k: entry[k] for k in _FIELDS}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def append(log: list, actor: str, action: str, data: dict) -> dict:
    # Round-trip through JSON so the hash covers exactly what will be stored and reloaded.
    entry = {"seq": len(log), "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "actor": actor,
             "action": action, "data": json.loads(json.dumps(data, default=str)),
             "prev_hash": log[-1]["hash"] if log else GENESIS}
    entry["hash"] = _digest(entry)
    log.append(entry)
    return entry


def verify_chain(log: list) -> dict:
    prev = GENESIS
    for i, e in enumerate(log):
        if e.get("seq") != i or e.get("prev_hash") != prev or _digest(e) != e.get("hash"):
            return {"valid": False, "broken_at": i, "entries": len(log)}
        prev = e["hash"]
    return {"valid": True, "broken_at": None, "entries": len(log), "head": prev if log else None}
