"""TinyFish clients: Web Agent (SSE), Search API and Fetch API, each with a replay twin for demos.

Web Agent: run_live / run_replay yield the same normalized events:
  {"type": "started",  "run_id": str}
  {"type": "stream",   "url": str}            live browser view
  {"type": "progress", "message": str}
  {"type": "complete", "result": dict|list}
  {"type": "error",    "message": str}
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx

from . import config

log = logging.getLogger("ghost.tinyfish")


def provenance(source: str, live: bool = False) -> dict:
    """Label a recording honestly: real TinyFish data (live now, or recorded earlier) vs hand-written test fixtures."""
    if live:
        return {"kind": "live", "source": source}
    kind = "synthetic" if re.search(r"(?i)synthetic|fictional", source or "") else "recorded"
    return {"kind": kind, "source": source or "recording (source not stated)"}


def recorded_date(source: str) -> str | None:
    """'Real TinyFish run … on 2026-10-16 (…)' -> '2026-10-16'. Recordings without a date stay undated."""
    m = re.search(r"\d{4}-\d{2}-\d{2}", source or "")
    return m.group(0) if m else None


def _maybe_json(v):
    """Agent results arrive as a dict, a JSON string, or (e.g. when a run hits its step budget) a dict wrapping a
    JSON string under "result"/"resultJson". Unwrap until we reach the payload."""
    for _ in range(3):
        if isinstance(v, str):
            try:  # raw_decode tolerates trailing junk such as a stray "}" after the JSON
                v, _ = json.JSONDecoder().raw_decode(v.strip()[v.strip().find("{"):] if "{" in v else v.strip())
            except json.JSONDecodeError:
                return {"raw_text": v}
        elif isinstance(v, dict) and "products" not in v and len(v) == 1 and next(iter(v)) in ("result", "resultJson", "output"):
            v = next(iter(v.values()))
        else:
            break
    return v


async def run_live(url: str, goal: str) -> AsyncIterator[dict]:
    body = {
        "url": url,
        "goal": goal,
        "browser_profile": config.TINYFISH_BROWSER_PROFILE,
        "agent_config": {"max_duration_seconds": config.TINYFISH_MAX_SECONDS},
    }
    headers = {"X-API-Key": config.TINYFISH_API_KEY, "Accept": "text/event-stream"}
    timeout = httpx.Timeout(connect=20, read=config.TINYFISH_MAX_SECONDS + 60, write=20, pool=20)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", f"{config.TINYFISH_BASE_URL}/v1/automation/run-sse",
                                 json=body, headers=headers) as resp:
            if resp.status_code >= 400:
                text = (await resp.aread()).decode(errors="replace")[:300]
                yield {"type": "error", "message": f"TinyFish HTTP {resp.status_code}: {text}"}
                return
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                kind = str(ev.get("type", "")).upper()
                if kind == "STARTED":
                    yield {"type": "started", "run_id": ev.get("run_id") or ev.get("runId")}
                elif kind == "STREAMING_URL":
                    yield {"type": "stream", "url": ev.get("streaming_url") or ev.get("streamingUrl")}
                elif kind == "PROGRESS":
                    yield {"type": "progress", "message": ev.get("purpose") or ev.get("message") or "working…"}
                elif kind == "COMPLETE":
                    status = str(ev.get("status", "COMPLETED")).upper()
                    if status == "COMPLETED":
                        result = ev.get("result", ev.get("resultJson"))
                        yield {"type": "complete", "result": _maybe_json(result)}
                    else:
                        err = ev.get("error") or {}
                        msg = err.get("message") if isinstance(err, dict) else str(err)
                        yield {"type": "error", "message": msg or f"run {status.lower()}"}
                    return
    yield {"type": "error", "message": "stream ended without a COMPLETE event"}


async def run_replay(domain: str, vendor_key: str) -> AsyncIterator[dict]:
    path = config.REPLAY_DIR / domain / f"{vendor_key}.json"
    if not path.exists():
        path = config.RECORD_DIR / domain / f"{vendor_key}.json"
    if not path.exists():
        yield {"type": "error", "message": "no recording for this vendor (run once in live mode to record it)"}
        return
    rec = json.loads(path.read_text())
    yield {"type": "started", "run_id": f"replay-{vendor_key}"}
    steps = rec.get("steps") or ["Working…"]
    delay = rec.get("duration_s", 60) / max(config.REPLAY_SPEED, 0.01) / (len(steps) + 1)
    for s in steps:
        await asyncio.sleep(delay)
        yield {"type": "progress", "message": s}
    await asyncio.sleep(delay)
    if rec.get("error"):
        yield {"type": "error", "message": rec["error"]}
    else:
        yield {"type": "complete", "result": rec.get("result", {}), "provenance": provenance(rec.get("_source", ""))}


def record(domain: str, vendor_key: str, steps: list[str], result, started: float, source: str) -> None:
    d = config.RECORD_DIR / domain
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vendor_key}.json").write_text(json.dumps({
        "_source": source, "duration_s": round(time.time() - started), "steps": steps[-40:], "result": result,
    }, indent=2))


# ── Search API: discover vendors on the live web ─────────────────────────────

SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, headers={"X-API-Key": config.TINYFISH_API_KEY})


async def search(query: str, domain: str, mode: str, location: str = "US", exclude_domains: str = "") -> dict:
    """GET api.search.tinyfish.ai → {"query", "results": [{position, site_name, title, snippet, url}]}"""
    if mode != "live":
        path = _replay_path(domain, "_search.json")
        return json.loads(path.read_text()) if path else {"query": query, "results": []}
    params = {"query": query, "location": location}
    if exclude_domains:
        params["exclude_domains"] = exclude_domains
    async with _client(30) as c:
        r = await c.get(SEARCH_URL, params=params)
        r.raise_for_status()
        data = r.json()
    _record_json(domain, "_search.json", {**data, "_source": f"TinyFish Search API {time.strftime('%Y-%m-%d')}"})
    return data


# ── Fetch API: read product pages to verify what the agents extracted ────────

async def fetch_pages(urls: list[str], domain: str, mode: str) -> dict[str, dict]:
    """POST api.fetch.tinyfish.ai (≤10 URLs per call) → {url: {"title", "text", "fetched_at", "provenance"}};
    failures are omitted. fetched_at is when the page text was actually retrieved (the recording date in replay)."""
    urls = list(dict.fromkeys(u for u in urls if u.startswith("http")))
    if mode != "live":
        pages = {}
        for base in (config.REPLAY_DIR, config.RECORD_DIR):  # curated fixtures, then anything recorded live
            path = base / domain / "_fetch.json"
            if path.exists():
                data = json.loads(path.read_text())
                src = data.get("_source", "")
                for u, p in data["pages"].items():
                    pages[u] = {**p, "fetched_at": p.get("fetched_at") or recorded_date(src),
                                "provenance": provenance(src)}
        await asyncio.sleep(min(2.0, 0.15 * len(urls)))
        return {u: pages[u] for u in urls if u in pages}
    out = {}
    async with _client(90) as c:
        async def batch(chunk):
            r = await c.post(FETCH_URL, json={"urls": chunk, "format": "markdown", "ttl": 3600,
                                              "purpose": "Verify price, specs, warranty and purchase limits "
                                                         "extracted by a procurement agent"})
            r.raise_for_status()
            return r.json()
        results = await asyncio.gather(*[batch(urls[i:i + 10]) for i in range(0, len(urls), 10)],
                                       return_exceptions=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for res in results:
        if isinstance(res, Exception):
            log.warning("TinyFish fetch batch failed: %s", res)
            continue
        for page in res.get("results", []):
            out[page["url"]] = {"title": page.get("title", ""), "text": page.get("text", ""), "fetched_at": now}
    _merge_record(domain, out)
    return {u: {**p, "provenance": provenance("TinyFish Fetch API", live=True)} for u, p in out.items()}


def _replay_path(domain: str, name: str):
    for base in (config.REPLAY_DIR, config.RECORD_DIR):  # curated fixtures first, then anything recorded live
        if (base / domain / name).exists():
            return base / domain / name
    return None


def _record_json(domain: str, name: str, obj) -> None:
    d = config.RECORD_DIR / domain
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False))


def _merge_record(domain: str, pages: dict) -> None:
    path = config.RECORD_DIR / domain / "_fetch.json"
    existing = json.loads(path.read_text())["pages"] if path.exists() else {}
    _record_json(domain, "_fetch.json", {"_source": "TinyFish Fetch API (recorded live)", "pages": {**existing, **pages}})
