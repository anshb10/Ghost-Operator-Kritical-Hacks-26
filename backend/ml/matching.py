"""Entity resolution: find the same product (and the same model line) across vendors so we compare like with like.

Two levels:
  * exact groups  — same model AND compatible specs (complete-linkage, so one offer with missing specs
                    can't chain two different configurations together)
  * family matches — same model line sold by different vendors in different configs
                    ("ThinkPad E14 Gen 7" at Lenovo vs CDW), compared on specs-per-dollar
"""
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from ..models import Offer

_NOISE = re.compile(r"\b(laptop|notebook|business|computer|pc|new|with|and|the|for|black|grey|gray|silver)\b")
_SPECS = re.compile(r"\b(\d+\s*(gb|tb|w|mhz|mt/s)|core\s*(ultra\s*)?\d|i[3579]|ryzen\s*\d|win(dows)?\s*11\s*pro|ssd|ram|"
                    r"snapdragon|x\s*plus|x1p[-\d]*)\b")
SIM_THRESHOLD = 0.62
FAMILY_THRESHOLD = 0.55
_SPEC_KEYS = ("ram_gb", "storage_gb", "cpu_tier", "count_per_case", "thickness_mil")


def _clean(name: str) -> str:
    s = re.sub(r"[®™″\"()\-–,]", " ", name.lower())
    return re.sub(r"\s+", " ", _NOISE.sub(" ", s)).strip()


def _family(name: str) -> str:
    return re.sub(r"\s+", " ", _SPECS.sub(" ", _clean(name))).strip()


def _same_model(a: Offer, b: Offer) -> bool:
    """Family names may differ only by part-number-like tokens (e.g. '255u'), not by words like 'plus' or 'amd'."""
    diff = set(_family(a.name).split()) ^ set(_family(b.name).split())
    return all(re.fullmatch(r"[a-z]+\d+[a-z\d]*|\d+[a-z]+[a-z\d]*", t) for t in diff)


def _specs_compatible(a: Offer, b: Offer) -> bool:
    for k in _SPEC_KEYS:
        va, vb = a.attributes.get(k), b.attributes.get(k)
        if va is not None and vb is not None and abs(va - vb) > 1e-6:
            return False
    return True


def _sim(texts: list[str]):
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))
    return cosine_similarity(vec.fit_transform(texts))


def group_offers(offers: list[Offer]) -> list[dict]:
    """Assign group_id to each offer (in place). Returns groups spanning >1 offer."""
    if len(offers) < 2:
        for o in offers:
            o.group_id = "g0"
        return []
    sim = _sim([_clean(o.name) for o in offers])
    clusters = [[i] for i in range(len(offers))]
    pairs = sorted(((sim[i, j], i, j) for i in range(len(offers)) for j in range(i + 1, len(offers))
                    if sim[i, j] >= SIM_THRESHOLD), reverse=True)
    where = list(range(len(offers)))
    for s, i, j in pairs:
        ci, cj = where[i], where[j]
        if ci == cj:
            continue
        if all(_specs_compatible(offers[a], offers[b]) and _same_model(offers[a], offers[b])
               for a in clusters[ci] for b in clusters[cj]):
            clusters[ci] += clusters[cj]
            for k in clusters[cj]:
                where[k] = ci
            clusters[cj] = []
    out, gid = [], 0
    for members in clusters:
        if not members:
            continue
        for k in members:
            offers[k].group_id = f"g{gid}"
        if len(members) > 1:
            out.append({"group_id": f"g{gid}", "offer_ids": [offers[k].id for k in members],
                        "names": [offers[k].name for k in members]})
        gid += 1
    return out


def family_matches(offers: list[Offer]) -> list[dict]:
    """Same model line at different vendors — even in different configs."""
    if len(offers) < 2:
        return []
    fams = [_family(o.name) for o in offers]
    sim = _sim(fams)
    out = []
    for i in range(len(offers)):
        for j in range(i + 1, len(offers)):
            a, b = offers[i], offers[j]
            if a.vendor == b.vendor or sim[i, j] < FAMILY_THRESHOLD or not (a.unit_price and b.unit_price):
                continue
            na, nb = (set(re.findall(r"\b\d+\b", f)) for f in (fams[i], fams[j]))
            if not (na <= nb or nb <= na):  # model numbers must agree: Pro 14 != Pro 16
                continue
            cheaper, other = (a, b) if a.unit_price <= b.unit_price else (b, a)
            better_specs = [k for k in ("ram_gb", "storage_gb", "cpu_tier")
                            if (cheaper.attributes.get(k) or 0) > (other.attributes.get(k) or 0)]
            worse_specs = [k for k in ("ram_gb", "storage_gb", "cpu_tier")
                           if (cheaper.attributes.get(k) or 0) < (other.attributes.get(k) or 0)]
            out.append({"family": fams[i], "similarity": round(float(sim[i, j]), 3),
                        "cheaper": {"id": cheaper.id, "vendor": cheaper.vendor, "name": cheaper.name, "price": cheaper.unit_price},
                        "pricier": {"id": other.id, "vendor": other.vendor, "name": other.name, "price": other.unit_price},
                        "saving_pct": round((other.unit_price - cheaper.unit_price) / other.unit_price, 3),
                        "cheaper_has_better": better_specs, "cheaper_has_worse": worse_specs})
    return sorted(out, key=lambda d: -d["similarity"])
