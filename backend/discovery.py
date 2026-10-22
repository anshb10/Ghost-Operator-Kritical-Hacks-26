"""Vendor discovery with the TinyFish Search API.

The domain config ships a few known vendors; discovery finds more stores on the live web for whatever the
buyer asked for. Results are filtered to pages that look like places you can actually buy (not forums,
blogs, social media or reviews) and ranked by commerce signals in the title/snippet.
"""
import re
from urllib.parse import urlparse

from . import tinyfish
from .models import Requirements

NON_STORES = {"reddit.com", "youtube.com", "facebook.com", "quora.com", "instagram.com", "tiktok.com", "x.com",
              "twitter.com", "linkedin.com", "pinterest.com", "wikipedia.org", "medium.com", "microsoft.com",
              "laptopmag.com", "pcmag.com", "techradar.com", "tomsguide.com", "cnet.com", "theverge.com",
              "spiceworks.com", "community.spiceworks.com"}
CONTENT_PATH = re.compile(r"/(blog|blogs|news|article|articles|guide|guides|review|reviews|forum|t|comments|wiki)/", re.I)
CONTENT_TITLE = re.compile(r"\b(guide|how to|best .* \d{4}|vs\.?|review|tips|what is|specs and system requirements)\b", re.I)
COMMERCE = re.compile(r"(\$\s?\d|\bprice|\bshop\b|\bbuy\b|in stock|add to cart|per case|/cs\b|\bcase\b|wholesale|bulk|"
                      r"for sale|delivery|ships?\b|item #|sku)", re.I)
EXCLUDE_PARAM = "reddit.com,youtube.com,facebook.com,quora.com,instagram.com,tiktok.com,pinterest.com,wikipedia.org"


def site_key(url: str) -> str:
    """'https://www.officedepot.com/b/...' -> 'officedepot' (stable key used for agent recordings)."""
    host = urlparse(url).hostname or ""
    parts = [p for p in host.lower().split(".") if p not in ("www", "us", "shop", "store")]
    return re.sub(r"[^a-z0-9]", "", parts[-2] if len(parts) >= 2 else (parts[0] if parts else "site"))


def _base_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return ".".join(host.split(".")[-2:])


def build_query(req: Requirements) -> str:
    bits = [req.item]
    for c in req.constraints:
        if not c.hard:
            continue
        if c.attribute == "ram_gb":
            bits.append(f"{c.value:g}GB RAM" if isinstance(c.value, float) else f"{c.value}GB RAM")
        elif c.attribute == "os":
            bits.append("Windows 11 Pro" if "pro" in str(c.value).lower() else str(c.value))
        elif c.attribute == "certifications":
            bits.append(f"ASTM {c.value}" if str(c.value).upper().startswith("D") else str(c.value))
        elif c.attribute == "powder_free":
            bits.append("powder free")
    bits.append("buy price")
    return " ".join(bits)


def rank_candidates(results: list[dict], known_vendors: list[dict], limit: int = 6) -> list[dict]:
    known = {_base_domain(v["url"]) for v in known_vendors} | {site_key(v["url"]) for v in known_vendors}
    seen, out = set(), []
    for r in results:
        url = r.get("url", "")
        base, key = _base_domain(url), site_key(url)
        if not url.startswith("https://") or base in NON_STORES or base in known or key in known or key in seen:
            continue
        if CONTENT_PATH.search(urlparse(url).path + "/") or CONTENT_TITLE.search(r.get("title", "")):
            continue
        text = f"{r.get('title', '')} {r.get('snippet', '')}"
        signals = len(COMMERCE.findall(text))
        if signals == 0:
            continue
        seen.add(key)
        out.append({"key": key, "name": (r.get("site_name") or base).replace("www.", ""), "url": url,
                    "hint": "", "discovered": True, "snippet": r.get("snippet", "")[:220],
                    "search_rank": r.get("position"), "commerce_signals": signals})
    out.sort(key=lambda v: (-v["commerce_signals"], v["search_rank"] or 99))
    return out[:limit]


async def discover(req: Requirements, known_vendors: list[dict], mode: str) -> dict:
    query = build_query(req)
    try:
        data = await tinyfish.search(query, req.domain, mode, location="IN" if req.currency == "INR" else "US",
                                     exclude_domains=EXCLUDE_PARAM)
    except Exception as e:  # discovery is additive; never block planning on it
        return {"query": query, "vendors": [], "error": str(e)[:200], "n_results": 0}
    results = data.get("results", [])
    return {"query": data.get("query", query), "vendors": rank_candidates(results, known_vendors),
            "n_results": len(results)}
