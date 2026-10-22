"""Grounding check for LLM-written memos: every money figure in the memo must match a figure the cost engine
produced (within rounding). A memo that cites anything else is discarded in favour of the template memo, and the
unsupported figures are recorded in the report and the audit trail."""
import re

MONEY = re.compile(r"(?:\$|₹|\bRs\.?\s?|\bINR\s?|\bUSD\s?)\s?(\d[\d,]*(?:\.\d+)?)\s*(k|K|lakh|lakhs|crore|cr|m|M|million)?\b")
SCALE = {"k": 1e3, "lakh": 1e5, "lakhs": 1e5, "crore": 1e7, "cr": 1e7, "m": 1e6, "million": 1e6}


def money_figures(text: str) -> list[float]:
    out = []
    for m in MONEY.finditer(text or ""):
        try:
            out.append(float(m.group(1).replace(",", "")) * SCALE.get((m.group(2) or "").lower(), 1))
        except ValueError:
            continue
    return out


def allowed_figures(report_offers: list[dict], req) -> set[float]:
    vals = {req.budget_total} if req.budget_total else set()
    q = req.quantity
    for o in report_offers:
        for v in (o.get("unit_price"), o.get("effective_unit_price")):
            if v:
                vals.update((v, v * q))
        for v in (o.get("fair_price"), o.get("tco")):
            if v:
                vals.add(v)
        lc = o.get("landed") or {}
        for k in ("total", "per_unit", "goods_subtotal", "pre_tax", "total_confirmed"):
            if lc.get(k):
                vals.add(lc[k])
        for line in lc.get("lines", []):
            if line.get("amount"):
                vals.add(abs(line["amount"]))
        for v in (o.get("tco_breakdown") or {}).values():
            if v:
                vals.add(v)
        lim = (o.get("verification") or {}).get("limit") or {}
        for k in ("list_price", "est_list_price"):
            if lim.get(k):
                vals.add(lim[k])
    if req.budget_total:
        for o in report_offers:
            t = (o.get("landed") or {}).get("total")
            if t:
                vals.add(abs(req.budget_total - t))
    return {v for v in vals if v}


def check_memo(memo: str, allowed: set[float]) -> dict:
    figs = money_figures(memo)
    bad = [f for f in figs if not any(abs(f - a) <= max(1.0, 0.015 * a) for a in allowed)]
    return {"ok": not bad, "checked": len(figs), "unsupported": bad}
