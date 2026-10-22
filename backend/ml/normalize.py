"""Turn messy TinyFish extractions into canonical, comparable Offer objects."""
import hashlib
import re

from ..models import Offer, Warranty

_NUM = r"(\d+(?:\.\d+)?)"


def same_party(vendor: str, seller: str) -> bool:
    """'eagleprotect.com' vs 'Eagle Protect', 'Lenovo' vs 'Lenovo US', 'CDW' vs 'CDW' → same company."""
    norm = lambda x: re.sub(r"\.(com|net|org|co|us)$", "", re.sub(r"[^a-z0-9.]", "", (x or "").lower().replace("www.", "")))
    a, b = norm(vendor), norm(seller)
    return not b or a in b or b in a


def parse_price(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    m = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)", str(v))
    return float(m.group(1).replace(",", "")) if m else None


def parse_gb(v) -> float | None:
    """'16 GB DDR5' -> 16, '1TB SSD' -> 1024, '512GB' -> 512. Ignores 'Up to' marketing ranges."""
    if v is None:
        return None
    if isinstance(v, (int, float)):  # agents sometimes return bare numbers (ram: 16, storage: 512): read as GB
        return float(v) if v > 0 else None
    s = str(v).lower()
    m = re.search(_NUM + r"\s*tb", s)
    if m:
        return float(m.group(1)) * 1024
    m = re.search(_NUM + r"\s*gb", s)
    return float(m.group(1)) if m else None


def parse_inches(v) -> float | None:
    m = re.search(_NUM + r"\s*(?:\"|in|”|-inch|inch)", str(v or "").lower())
    return float(m.group(1)) if m else None


def parse_number(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"([\d,]+(?:\.\d+)?)", str(v or ""))
    return float(m.group(1).replace(",", "")) if m else None


def cpu_tier(v) -> float | None:
    """Map CPU marketing names onto a rough 1-4 performance tier. 'Up to …' means unknown config."""
    s = re.sub(r"[®™]", "", str(v or "").lower())
    if not s or s.startswith("up to"):
        return None
    if "snapdragon" in s:
        return 3.5 if "elite" in s else 2.5
    m = re.search(r"(?:core\s*(?:ultra\s*)?|\bi|ryzen\s*(?:ai\s*)?(?:pro\s*)?|ultra\s*)([3579])\b", s)
    if not m:
        if "apple m" in s or re.search(r"\bm[1-5]\b", s):
            return 3.0
        return None
    tier = {"3": 1, "5": 2, "7": 3, "9": 4}[m.group(1)]
    return tier + (0.5 if "ultra" in s or "ai" in s else 0.0)


def parse_warranty(v) -> Warranty:
    raw = str(v or "").strip()
    # Only what is included counts: drop clauses about optional upgrades / add-ons / care packs for sale.
    parts = re.split(r"[;(]|\s+-\s+|,\s*(?=optional|upgrade)", raw.lower())
    s = " ".join(p for p in parts if not re.search(r"optional|upgrade|add-on|available for purchase|sold separately", p)).strip()
    if not s or s in ("n/a", "none", "not shown", "not specified"):
        return Warranty(raw=raw, years=None, service="unknown")
    if re.search(r"security|antivirus|software suite", s) and not re.search(r"warrant|hardware|service|support|onsite|depot", s):
        return Warranty(raw=raw, years=None, service="unknown")  # e.g. "3 Yr Wolf Pro Security" is a software bundle, not cover
    years = None
    m = re.search(_NUM + r"[\s-]*(?:year|yr)", s)
    if m:
        years = float(m.group(1))
    else:
        m = re.search(_NUM + r"[\s-]*(?:month|mo)\b", s)
        if m:
            years = float(m.group(1)) / 12
        elif re.search(r"\b(one|1)[\s-]year", s):
            years = 1.0
    if "onsite" in s or "on-site" in s or "next business day" in s or "prosupport" in s or "premier" in s:
        service = "onsite"
    elif "mail-in" in s or "mail in" in s:
        service = "mail_in"
    elif "depot" in s or "pick-up" in s or "pickup" in s or "courier" in s:
        service = "depot"
    elif "carry-in" in s or "carry in" in s:
        service = "carry_in"
    elif "no warranty" in s or "as is" in s or "as-is" in s:
        service, years = "none", 0.0
    else:
        service = "unknown"
    return Warranty(raw=raw, years=years, service=service)


def parse_condition(*texts) -> str:
    s = " ".join(str(t or "") for t in texts).lower()
    if "refurb" in s or "renewed" in s:
        return "refurbished"
    if re.search(r"\bused\b|pre-owned|open box", s):
        return "used"
    if "new" in s:
        return "new"
    return "unknown"


_PARSERS = {"gb": parse_gb, "inches": parse_inches, "number": parse_number, "cpu_tier": cpu_tier,
            "text": lambda v: ("yes" if v is True else "no" if v is False else ", ".join(map(str, v)) if isinstance(v, list)
                               else str(v).strip() if v not in (None, "", []) else None)}


def normalize_offers(vendor: str, raw_result, domain_cfg: dict) -> list[Offer]:
    from ..tinyfish import _maybe_json
    raw_result = _maybe_json(raw_result)
    products = raw_result.get("products", []) if isinstance(raw_result, dict) else (raw_result or [])
    offers = []
    for p in products:
        if not isinstance(p, dict):
            continue
        name = str(p.get("product_name") or p.get("name") or "").strip()
        if not name:
            continue
        attrs = {}
        for key, spec in domain_cfg["attributes"].items():
            val = _PARSERS[spec["type"]](p.get(spec["from"]))
            if val is not None:
                attrs[key] = val
        w = parse_warranty(p.get("warranty"))
        attrs["warranty_years"] = w.years if w.years is not None else 0.0
        attrs["warranty_onsite"] = 1.0 if w.service == "onsite" else 0.0
        url = str(p.get("product_page_url") or p.get("url") or "")
        price = p.get("price_usd") or p.get("price_inr") or p.get("price")
        uid = hashlib.sha1(f"{vendor}|{name}|{price}|{p.get('storage')}|{url}".encode()).hexdigest()[:10]
        offers.append(Offer(
            id=uid, vendor=vendor, name=name, sku=str(p.get("sku") or ""),
            unit_price=parse_price(price), currency=domain_cfg.get("currency", "USD"),
            url=url, attributes=attrs, warranty=w,
            seller=str(p.get("seller") or vendor),
            condition=parse_condition(p.get("condition"), name),
            stock=str(p.get("stock_status") or ""),
            evidence={k: v for k, v in p.items() if v not in (None, "")},
        ))
    return offers
