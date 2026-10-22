"""Adversarial vendor verification: read the commercial small print off a product page and flag misleading offers.

Vendor pages are untrusted input. Everything here is a deterministic reading of the fetched page text; nothing is
taken from an LLM. Two kinds of output:

  extract_terms(text)      commercial terms with the snippet they came from: shipping, tax inclusion, fees,
                           minimum order, lead time, stock quantity, pack size, warranty fine print, and any text
                           that tries to instruct an AI agent (prompt injection)
  static_flags(offer)      findings that depend only on the page: spec contradictions, price not on page,
                           misleading unit basis, hidden charges, warranty conflicts, stock problems, injection
  requirement_flags(o, r)  findings that depend on the buyer's requirements and are recomputed in every scenario:
                           promo cap below quantity, MOQ above quantity, stock below quantity, delivery vs deadline

Severity: high = changes whether this offer can be bought as advertised; medium = changes the cost or needs a
quote; low/info = worth knowing. Missing information is reported as unknown, never as wrongdoing.
"""
import re

from .models import Offer, Requirements

CUR = r"(?:\$|₹|\bRs\.?|\bINR)"
AMT = CUR + r"\s?([\d,]+(?:\.\d+)?)"


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _snip(text: str, m: re.Match, pad: int = 60) -> str:
    s = text[max(0, m.start() - pad): m.end() + pad]
    return ("…" if m.start() > pad else "") + re.sub(r"\s+", " ", s).strip() + ("…" if m.end() + pad < len(text) else "")


FREE_SHIP = re.compile(r"(?i)\b(free (?:storewide |expedited |standard )?(?:shipping|delivery)|ships free|free\s*delivery|"
                       r"delivery\s*:?\s*free)\b")
# "Free shipping on orders over $35": free only above a threshold
FREE_OVER = re.compile(r"(?i)\bfree (?:standard |expedited )?(?:shipping|delivery)\s*(?:on|for)?\s*(?:all\s+)?orders?\s*"
                       r"(?:over|above|of|worth)\s*" + AMT + r"(?:\s*(?:or more|\+))?")
PER = r"(?:\s*(?:per|/|a|an)\s*(unit|item|laptop|piece|pc|order|shipment))?"
SHIP_AMT = re.compile(r"(?i)\b(?:shipping|delivery|freight)(?: charges?| fee| cost)?\s*(?:of|:|-)?\s*" + AMT + PER)
TAX_INCL = re.compile(r"(?i)\b(incl(?:usive|\.|uding)? (?:of )?(?:all )?(?:taxes|gst|tax|vat)|gst included|price includes (?:gst|tax))\b")
TAX_EXCL = re.compile(r"(?i)(\+\s*(?:applicable\s+)?(?:gst|tax(?:es)?|vat)\b|\bexcl(?:usive|\.|uding)? (?:of )?(?:all )?(?:taxes|gst|tax|vat)|"
                      r"\bplus (?:applicable )?(?:gst|taxes|tax)\b|\bgst (?:extra|additional|as applicable)\b|\btaxes extra\b)")
FEE = re.compile(r"(?i)\b(handling|processing|convenience|packaging|installation|setup|platform|service|restocking|cod)"
                 r"\s*(?:fee|charge)s?\s*(?:of|:|-)?\s*" + AMT + PER)
MOQ = re.compile(r"(?i)(?:minimum order(?: quantity)?|\bmoq\b|\bmin\.?\s*order)\s*(?:qty|quantity)?\s*(?:is|of|:|-)?\s*(\d[\d,]*)")
LEAD_RANGE = re.compile(r"(?i)\b(?:usually |typically )?(ships?|dispatch(?:ed|es)?|deliver(?:ed|y)|lead time|arrives?)"
                        r"\s*(?:in|within|:)?\s*(\d+)\s*(?:-|to|–)\s*(\d+)\s*(business\s+|working\s+)?(days?|weeks?)\b")
LEAD_ONE = re.compile(r"(?i)\b(?:usually |typically )?(ships?|dispatch(?:ed|es)?|deliver(?:ed|y)|lead time|arrives?)"
                      r"\s*(?:in|within|:)\s*(\d+)\s*(business\s+|working\s+)?(days?|weeks?)\b")
SHIPS_TODAY = re.compile(r"(?i)\b(ships today|same[- ]day dispatch|dispatched today)\b")
ONLY_LEFT = re.compile(r"(?i)\b(?:only|just)\s+(\d+)\s+(?:units?\s+|items?\s+|pieces?\s+)?(?:left|remaining|in stock)\b")
LIMITED = re.compile(r"(?i)\b(limited stock|few left|low stock|selling fast)\b")
# "pack of 100", "case quantity: 1000", "100 gloves/carton". Not "Master Carton Quantity" (cartons per shipper)
# and not "Carton: 9 1/4 x 2 3/8" (dimensions).
PACK = re.compile(r"(?i)(?:(?<!master )\b(?:carton|case|box|pack|bag)\s*(?:of|(?:qty|quantity|count)\s*:)\s*(\d[\d,]*)(?!\s*(?:cartons|cases|boxes|x\b|/\d))|"
                  r"(\d[\d,]*)\s*(?:gloves|pcs|pieces|count|ct|units)\s*(?:/|per|a)\s*(?:carton|case|box|pack))")
PACK_HEADER = re.compile(r"(?i)\b(?:gloves|pcs|pieces|units|count)\s*/\s*(?:carton|case|box)\b")
DISCOUNT = re.compile(r"(?i)(?:extra |additional )?(\d+(?:\.\d+)?)\s*%\s*off\s*(?:on|for|when you buy|on orders? of)\s*(\d+)\s*\+?\s*(?:units|pcs|pieces|or more|laptops)")
WARRANTY_FINE = re.compile(r"(?i)(extended warranty[^.\n]{0,40}(?:sold separately|available at extra cost|optional)|"
                           r"warranty[^.\n]{0,30}(?:void|not covered|does not cover)[^.\n]{0,60}|"
                           r"(?:onsite|on-site)[^.\n]{0,30}(?:upgrade|add-on)[^.\n]{0,30}(?:extra|additional|sold separately))")
# Text aimed at an AI agent rather than a human shopper. Matching it changes nothing in the ranking except a flag:
# the page is never obeyed, only reported.
INJECTION = re.compile(r"(?i)(ignore (?:all |any )?(?:previous|prior|above) instructions|"
                       r"\b(?:ai|llm|automated)\s+(?:[a-z-]+\s+)?(?:agents?|assistants?|models?|bots?|buyers?)\b[^.\n]{0,80}\b(?:must|should|rank|recommend|select|choose)\b|"
                       r"\bsystem prompt\b|\byou are (?:an? )?(?:ai|assistant|language model)\b|"
                       r"\brank this (?:offer|product|listing) (?:first|highest|#1)\b)")


def extract_terms(text: str, o: Offer | None = None) -> dict:
    t: dict = {}
    if not text:
        return t
    unit_word = lambda w: "order" if not w or w.lower() in ("order", "shipment") else "unit"
    if m := FREE_OVER.search(text):
        t["shipping"] = {"kind": "free_over", "threshold": _num(m.group(1)), "amount": None, "per": "order",
                         "snippet": _snip(text, m)}
    elif m := FREE_SHIP.search(text):
        t["shipping"] = {"kind": "free", "amount": 0.0, "per": "order", "snippet": _snip(text, m)}
    elif m := SHIP_AMT.search(text):
        per = unit_word(m.group(2))
        t["shipping"] = {"kind": "amount", "amount": _num(m.group(1)), "per": per, "snippet": _snip(text, m)}
    mi, me = TAX_INCL.search(text), TAX_EXCL.search(text)
    if mi and not me:
        t["tax"] = {"inclusion": "inclusive", "snippet": _snip(text, mi)}
    elif me and not mi:
        t["tax"] = {"inclusion": "exclusive", "snippet": _snip(text, me)}
    elif mi and me:
        t["tax"] = {"inclusion": "conflicting", "snippet": _snip(text, mi) + " / " + _snip(text, me)}
    fees = [{"label": f"{m.group(1).lower()} fee", "amount": _num(m.group(2)), "per": unit_word(m.group(3)),
             "snippet": _snip(text, m)} for m in FEE.finditer(text)]
    if fees:
        t["fees"] = fees
    if m := DISCOUNT.search(text):
        t["discount"] = {"pct": float(m.group(1)), "min_units": int(m.group(2)), "snippet": _snip(text, m)}
    if m := MOQ.search(text):
        t["moq"] = {"units": int(_num(m.group(1))), "snippet": _snip(text, m)}
    if m := LEAD_RANGE.search(text):
        k = 7 if m.group(5).lower().startswith("week") else 1
        biz = 7 / 5 if m.group(4) else 1  # business days → calendar days
        t["lead_time"] = {"min_days": round(int(m.group(2)) * k * biz), "max_days": round(int(m.group(3)) * k * biz),
                          "kind": "dispatch" if m.group(1).lower().startswith(("ship", "dispatch")) else "delivery",
                          "snippet": _snip(text, m)}
    elif m := LEAD_ONE.search(text):
        k = 7 if m.group(4).lower().startswith("week") else 1
        d = round(int(m.group(2)) * k * (7 / 5 if m.group(3) else 1))
        t["lead_time"] = {"min_days": d, "max_days": d,
                          "kind": "dispatch" if m.group(1).lower().startswith(("ship", "dispatch")) else "delivery",
                          "snippet": _snip(text, m)}
    elif m := SHIPS_TODAY.search(text):
        t["lead_time"] = {"min_days": 0, "max_days": 1, "kind": "dispatch", "snippet": _snip(text, m)}
    if m := ONLY_LEFT.search(text):
        t["stock_qty"] = {"units": int(m.group(1)), "snippet": _snip(text, m)}
    elif m := LIMITED.search(text):
        t["stock_qty"] = {"units": None, "snippet": _snip(text, m)}
    if m := PACK.search(text):
        t["pack"] = {"count": int(_num(m.group(1) or m.group(2))), "snippet": _snip(text, m), "method": "stated"}
    elif o is not None and (h := PACK_HEADER.search(text)) and o.sku and o.sku in text:
        # Tabular listings ("MODEL NO. | MIL | GLOVES/CARTON | PRICE"): take the largest whole number between the
        # SKU and its first price. Labelled as a table reading so a reviewer knows it's a heuristic.
        i = text.find(o.sku) + len(o.sku)
        row = text[i: i + 160]
        cut = re.search(CUR, row)
        nums = [int(n) for n in re.findall(r"(?<![\d.$₹])(\d{2,5})(?![\d.])", row[: cut.start() if cut else None])]
        if nums:
            t["pack"] = {"count": max(nums), "snippet": re.sub(r"\s+", " ", row[: (cut.end() + 8) if cut else 120]).strip(),
                         "method": "table heading " + re.sub(r"\s+", "", h.group(0)).upper()}
    if m := WARRANTY_FINE.search(text):
        t["warranty_fine_print"] = {"text": m.group(0).strip(), "snippet": _snip(text, m)}
    inj = [_snip(text, m, 40) for m in INJECTION.finditer(text)][:3]
    if inj:
        t["injection"] = inj
    return t


def check_unit_basis(o: Offer, text: str) -> None:
    """Pack-size trap: the agent priced N items per case while the page sells a different pack. The page wins for
    the pack count; the price claim is then contradicted with the per-item arithmetic shown."""
    pack = (o.terms or {}).get("pack")
    claimed = o.attributes.get("count_per_case")
    if not pack or not claimed or abs(pack["count"] - claimed) < 1e-6:
        return
    o.attributes["count_per_case"] = float(pack["count"])
    o.verification.setdefault("corrections", []).append(
        f"count_per_case: {claimed:g} → {pack['count']} (page sells packs of {pack['count']})")
    o.verification.setdefault("checks", []).append(
        {"field": "count_per_case", "ok": False, "claimed": claimed, "page": pack["count"], "snippet": pack["snippet"]})
    o.verification["unit_basis"] = {"claimed_count": claimed, "page_count": pack["count"]}
    # Show the arithmetic: which page price (tier) the agent's figure came from, scaled by the pack ratio.
    page_prices = [_num(x) for x in re.findall(AMT, pack["snippet"])]
    if o.unit_price and page_prices:
        ratio = claimed / pack["count"]
        implied = o.unit_price / ratio
        match = next((p for p in page_prices if abs(p - implied) <= max(0.01, 0.01 * p)), None)
        note = (f"page prices a pack of {pack['count']} at " + " / ".join(f"{p:g}" for p in page_prices)
                + (f"; the agent's {o.unit_price:g} for {claimed:g} = {ratio:g} packs at the {match:g} tier" if match else ""))
        checks = o.verification["checks"]
        o.verification["checks"] = [c for c in checks if c["field"] != "price"]
        o.verification["checks"].insert(0, {"field": "price", "ok": False, "claimed": o.unit_price, "page": page_prices,
                                            "note": note, "snippet": pack["snippet"]})
        o.verification["unit_basis"]["page_prices"] = page_prices
    if o.verification.get("status") in ("verified", "partial", "unverified"):
        o.verification["status"] = "mismatch"


def _flag(code: str, severity: str, title: str, detail: str, snippet: str = "", source_url: str = "") -> dict:
    return {"code": code, "severity": severity, "title": title, "detail": detail,
            "evidence": {"snippet": snippet, "source_url": source_url}}


def _money(v: float, cur: str) -> str:
    from .exports import money
    return money(v, cur, 0 if v >= 100 else 2)


def static_flags(o: Offer) -> list[dict]:
    out, v, t, url = [], o.verification or {}, o.terms or {}, o.url
    checks = {c["field"]: c for c in v.get("checks", [])}
    cur = o.currency
    for field, label in (("ram_gb", "RAM"), ("storage_gb", "storage"), ("thickness_mil", "thickness")):
        c = checks.get(field)
        if c and c["ok"] is False:
            out.append(_flag("SPEC_CONTRADICTED", "high", f"{label} differs at the advertised price",
                             f"Agent read {c['claimed']:g}; the product page shows {c['page']:g} at this price. The page "
                             "value is used.", c.get("snippet", ""), url))
    ub = v.get("unit_basis")
    if ub:
        price_note = ""
        if o.unit_price:
            per_item = o.unit_price / ub["claimed_count"]
            price_note = (f" The agent's {_money(o.unit_price, cur)} implies {_money(per_item, cur)} per item for "
                          f"{ub['claimed_count']:g}; the page prices a pack of {ub['page_count']}.")
        out.append(_flag("UNIT_BASIS_MISMATCH", "high", "Price quoted on a different unit basis",
                         f"Agent reported {ub['claimed_count']:g} per case; the page sells {ub['page_count']} per pack, so "
                         f"ordering by 'case' would buy a different quantity than intended.{price_note}",
                         t.get("pack", {}).get("snippet", ""), url))
    c = checks.get("price")
    if c and c["ok"] is False and not ub:
        out.append(_flag("PRICE_NOT_ON_PAGE", "medium", "Advertised price not found on the product page",
                         "The page shows " + ", ".join(_money(x, cur) for x in (c["page"] or [])) +
                         " instead. Price may have changed or belong to a different configuration.", c.get("snippet", ""), url))
    c = checks.get("warranty")
    if c and c["ok"] is False:
        out.append(_flag("WARRANTY_CONFLICT", "high", "Warranty on the page contradicts the listing",
                         f"Listing says “{c['claimed']}”; the page says “{c['page']}”.", c.get("snippet", ""), url))
    if wf := t.get("warranty_fine_print"):
        claimed_long = (o.warranty.years or 0) >= 2 or o.warranty.service == "onsite"
        out.append(_flag("WARRANTY_FINE_PRINT", "medium" if claimed_long else "low", "Warranty small print",
                         f"Page says: “{wf['text']}”." + (" The listing's longer/onsite cover may be a paid add-on."
                                                          if claimed_long else ""), wf["snippet"], url))
    c = checks.get("stock")
    if c and c["ok"] is False:
        out.append(_flag("OUT_OF_STOCK", "high", "Page shows the item is unavailable",
                         f"Agent reported “{c['claimed']}”; the page says “{c['page']}”.", c.get("snippet", ""), url))
    elif re.search(r"(?i)backorder|sold out|out of stock|unavailable", o.stock or ""):
        out.append(_flag("OUT_OF_STOCK", "high", "Listed as unavailable", f"Stock status: “{o.stock}”.", "", url))
    sq = t.get("stock_qty")
    if sq and sq["units"] is None:
        out.append(_flag("STOCK_UNCERTAIN", "low", "Stock described as limited", "The page signals low stock.", sq["snippet"], url))
    for f in t.get("fees", []):
        out.append(_flag("HIDDEN_CHARGE", "medium", f"Extra charge: {f['label']}",
                         f"{_money(f['amount'], cur)} per {f['per']}, not included in the advertised price.", f["snippet"], url))
    sh = t.get("shipping")
    if sh and sh["kind"] == "amount" and sh["amount"] > 0:
        out.append(_flag("HIDDEN_CHARGE", "medium", "Delivery is charged",
                         f"{_money(sh['amount'], cur)} per {sh['per']} on top of the advertised price.", sh["snippet"], url))
    tx = t.get("tax")
    if tx and tx["inclusion"] == "exclusive":
        out.append(_flag("TAX_EXCLUSIVE", "low", "Advertised price excludes tax",
                         "Tax is added on top of the listed price; the landed cost includes it.", tx["snippet"], url))
    elif tx and tx["inclusion"] == "conflicting":
        out.append(_flag("TAX_CONFLICT", "medium", "Page is inconsistent about tax",
                         "The page says both tax-inclusive and tax-extra; the landed cost assumes tax is extra (upper bound).",
                         tx["snippet"], url))
    for s in t.get("injection", []):
        out.append(_flag("PAGE_INJECTION", "high", "Page contains instructions aimed at AI agents",
                         "Text on the page tries to steer an automated buyer. It was ignored; treat this vendor's other "
                         "claims with extra care.", s, url))
    return out


def requirement_flags(o: Offer, req: Requirements) -> list[dict]:
    out, t, v, url, q = [], o.terms or {}, o.verification or {}, o.url, req.quantity
    lim = v.get("limit")
    if lim and lim["max_units"] < q:
        inferred = " (inferred from a sibling listing of the same model)" if lim.get("inferred_from") else ""
        out.append(_flag("BULK_CAP", "high", f"Promo price limited to {lim['max_units']} units per order",
                         f"You need {q}. The remaining {q - lim['max_units']} units are costed at list price"
                         f"{' if known' if not lim.get('list_price') else ''}{inferred}.", lim.get("snippet", lim.get("text", "")), url))
    if (moq := t.get("moq")) and moq["units"] > q:
        out.append(_flag("MOQ_ABOVE_QUANTITY", "medium", f"Minimum order is {moq['units']} units",
                         f"You need {q}; the vendor's minimum is {moq['units']}.", moq["snippet"], url))
    if (sq := t.get("stock_qty")) and sq["units"] is not None and sq["units"] < q:
        out.append(_flag("STOCK_BELOW_QUANTITY", "high", f"Only {sq['units']} in stock",
                         f"You need {q}; the page shows {sq['units']} available.", sq["snippet"], url))
    if req.deadline_days is not None:
        lt = t.get("lead_time")
        if lt and lt["min_days"] > req.deadline_days:
            out.append(_flag("DELIVERY_INFEASIBLE", "high", "Cannot arrive by your deadline",
                             f"Page states {lt['kind']} in {lt['min_days']}–{lt['max_days']} days; deadline is "
                             f"{req.deadline_days} days.", lt["snippet"], url))
        elif lt and lt["max_days"] > req.deadline_days:
            out.append(_flag("DELIVERY_AT_RISK", "medium", "Delivery may miss your deadline",
                             f"Page states {lt['kind']} in {lt['min_days']}–{lt['max_days']} days; deadline is "
                             f"{req.deadline_days} days.", lt["snippet"], url))
        elif not lt:
            out.append(_flag("DELIVERY_UNKNOWN", "info", "Delivery time not stated",
                             "The page gives no lead time, so the deadline can't be checked. Ask in the quote request.", "", url))
    return out

