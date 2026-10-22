"""Warranty / supplier risk: a small text classifier over warranty clauses + structured risk features.

The classifier is trained at startup on fixtures/warranty_clauses.json (labelled clause snippets).
Add more labelled rows there as you see real vendor pages; the model retrains on every boot.
"""
import json
from functools import lru_cache

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

from ..config import ROOT
from ..models import Offer
from .normalize import same_party

_SERVICE_RISK = {"onsite": 0.0, "depot": 0.45, "carry_in": 0.5, "mail_in": 0.55, "unknown": 0.6, "none": 1.0}


@lru_cache(maxsize=1)
def _model():
    rows = json.loads((ROOT / "fixtures" / "warranty_clauses.json").read_text())
    texts = [r["text"] for r in rows]
    labels = [r["risky"] for r in rows]
    pipe = make_pipeline(TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True),
                         LogisticRegression(C=4.0, max_iter=1000, class_weight="balanced"))
    pipe.fit(texts, labels)
    return pipe


def _top_terms(text: str, k: int = 3) -> list[str]:
    pipe = _model()
    vec, clf = pipe.named_steps["tfidfvectorizer"], pipe.named_steps["logisticregression"]
    x = vec.transform([text])
    contrib = x.multiply(clf.coef_[0]).toarray()[0]
    names = vec.get_feature_names_out()
    idx = [i for i in np.argsort(contrib)[::-1][:k] if contrib[i] > 0]
    return [names[i] for i in idx]


def score_offer(o: Offer, target_years: float) -> None:
    reasons = []
    w = o.warranty
    text = " ".join(filter(None, [w.raw, o.condition, o.seller]))
    clf_p = float(_model().predict_proba([text or "no warranty information"])[0, 1])

    rule = 0.0
    if w.years is None:
        rule += 0.35
        reasons.append("Warranty length not stated on the listing")
    elif w.years < target_years:
        rule += min(0.5, 0.2 * (target_years - w.years))
        reasons.append(f"Only {w.years:g}-yr coverage vs {target_years:g}-yr target")
    svc = _SERVICE_RISK[w.service]
    rule += 0.4 * svc
    if w.service in ("mail_in", "depot", "carry_in"):
        reasons.append(f"{w.service.replace('_', '-').title()} service: device leaves the user for repairs")
    if o.condition in ("refurbished", "used"):
        rule += 0.3
        reasons.append(f"Condition: {o.condition}")
    if o.seller and o.seller.lower() != "n/a" and not same_party(o.vendor, o.seller):
        rule += 0.2
        reasons.append(f"Sold by third party ({o.seller}): warranty may not be honoured by manufacturer")

    terms = _top_terms(text) if clf_p > 0.5 and text else []
    if terms:
        reasons.append("Clause model flagged: " + ", ".join(f"“{t}”" for t in terms))
    o.warranty_risk = round(min(1.0, 0.5 * clf_p + 0.5 * min(1.0, rule)), 3)
    o.warranty_risk_reasons = reasons
