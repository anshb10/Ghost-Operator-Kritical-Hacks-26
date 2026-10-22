"""Fair-price regression + anomaly detection.

For each offer we predict a 'fair' price from its specs using a ridge regression fit on the *other*
offers (leave-one-out, so an offer can't vouch for itself). Large residuals and isolation-forest
outliers are surfaced as anomalies: suspiciously cheap (grey market / refurb / wrong config) or
overpriced for the spec.
"""
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from ..models import Offer
from .normalize import same_party

LOW_PCT, HIGH_PCT = -0.30, 0.30


def _matrix(offers: list[Offer], features: list[str]) -> np.ndarray:
    X = np.array([[o.attributes.get(f, np.nan) for f in features] for o in offers], dtype=float)
    for j in range(X.shape[1]):
        col = X[:, j]
        med = np.nanmedian(col) if not np.all(np.isnan(col)) else 0.0
        col[np.isnan(col)] = med
    return X


def price_offers(offers: list[Offer], features: list[str]) -> dict:
    priced = [o for o in offers if o.unit_price]
    info = {"n": len(priced), "method": None}
    if len(priced) < 4:
        med = float(np.median([o.unit_price for o in priced])) if priced else None
        for o in priced:
            o.fair_price = med
            o.price_delta_pct = round((o.unit_price - med) / med, 3) if med else None
        info["method"] = "median (too few offers for regression)"
        return info

    X = _matrix(priced, features)
    y = np.log([o.unit_price for o in priced])
    Xs = StandardScaler().fit_transform(X)
    preds = np.empty(len(priced))
    for i in range(len(priced)):
        mask = np.arange(len(priced)) != i
        preds[i] = Ridge(alpha=1.0).fit(Xs[mask], y[mask]).predict(Xs[i:i + 1])[0]
    full = Ridge(alpha=1.0).fit(Xs, y)
    info.update(method="leave-one-out ridge on log(price)",
                coefficients={f: round(float(c), 3) for f, c in zip(features, full.coef_)})

    resid = y - preds
    iso_flags = np.zeros(len(priced), dtype=bool)
    if len(priced) >= 6:
        Z = np.column_stack([Xs, y, resid])
        iso = IsolationForest(n_estimators=200, contamination="auto", random_state=0).fit(Z)
        iso_flags = iso.predict(Z) == -1

    for o, p, r, iso_out in zip(priced, preds, resid, iso_flags):
        o.fair_price = round(float(np.exp(p)), 2)
        o.price_delta_pct = round(float(np.exp(r) - 1), 3)
        first_party_new = o.condition in ("new", "unknown") and same_party(o.vendor, o.seller)
        if o.price_delta_pct <= LOW_PCT and first_party_new:
            o.price_flag = "deal"
            o.anomaly = f"{-o.price_delta_pct:.0%} below fair price for these specs, sold new by {o.vendor}: likely a promotion, confirm quote validity for the full quantity"
        elif o.price_delta_pct <= LOW_PCT:
            o.price_flag = "suspicious"
            o.anomaly = f"{-o.price_delta_pct:.0%} below fair price ({o.condition}, seller: {o.seller}): verify authenticity, condition and warranty"
        elif o.price_delta_pct >= HIGH_PCT:
            o.price_flag = "overpriced"
            o.anomaly = f"{o.price_delta_pct:.0%} above fair price for these specs"
        elif iso_out:
            o.price_flag = "outlier"
            o.anomaly = "Unusual price/spec combination (isolation forest outlier)"
    return info


def cross_vendor_deltas(offers: list[Offer]) -> list[dict]:
    """Same product (matched group) cheaper elsewhere."""
    out = []
    by_group = {}
    for o in offers:
        if o.unit_price:
            by_group.setdefault(o.group_id, []).append(o)
    for g, items in by_group.items():
        vendors = {o.vendor for o in items}
        if len(vendors) < 2:
            continue
        items.sort(key=lambda o: o.unit_price)
        best, worst = items[0], items[-1]
        out.append({"group_id": g, "name": best.name, "best_vendor": best.vendor, "best_price": best.unit_price,
                    "worst_vendor": worst.vendor, "worst_price": worst.unit_price,
                    "spread_pct": round((worst.unit_price - best.unit_price) / best.unit_price, 3)})
    return out
