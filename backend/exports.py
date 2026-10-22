"""Offer CSV export, vendor quote-request drafts and the printable decision report. Nothing here is sent anywhere."""
import csv
import io
import re
from datetime import date
from html import escape

CSV_COLUMNS = ["rank", "recommended", "vendor", "name", "sku", "url", "currency", "unit_price", "effective_unit_price",
               "landed_total", "landed_per_unit", "landed_confidence", "landed_unknowns", "fair_price",
               "price_delta_pct", "price_flag", "stock", "condition", "seller", "warranty", "warranty_years",
               "warranty_service", "warranty_risk", "supplier_risk", "supplier_risk_level", "verification",
               "claims_verified", "claims_contradicted", "claims_inferred", "claims_unknown", "findings", "fit_score",
               "tco", "tco_purchase", "tco_repairs", "tco_downtime", "score", "passes_hard", "failed_constraints",
               "provenance"]


def _cell(v):
    # Scraped text is untrusted: stop spreadsheets from evaluating it as a formula.
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return "" if v is None else v


def offers_csv(mission) -> str:
    rep = mission.report or {}
    offers = rep.get("offers", [])
    attrs = sorted({k for o in offers for k in o.get("attributes", {})})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS + [f"attr_{a}" for a in attrs])
    for o in offers:
        t, wa, lc, rk = o.get("tco_breakdown") or {}, o.get("warranty") or {}, o.get("landed") or {}, o.get("risk") or {}
        cl = [c["status"] for c in o.get("claims", [])]
        row = [o.get("rank"), o["id"] == rep.get("recommended_offer_id"), o["vendor"], o["name"], o.get("sku"), o.get("url"),
               o.get("currency", "USD"), o.get("unit_price"), o.get("effective_unit_price"), lc.get("total"),
               lc.get("per_unit"), lc.get("confidence"), "; ".join(lc.get("unknowns", [])), o.get("fair_price"),
               o.get("price_delta_pct"), o.get("price_flag"), o.get("stock"), o.get("condition"), o.get("seller"),
               wa.get("raw"), wa.get("years"), wa.get("service"), o.get("warranty_risk"), rk.get("overall"), rk.get("level"),
               (o.get("verification") or {}).get("status"), cl.count("verified"), cl.count("contradicted"),
               cl.count("inferred"), cl.count("unknown"),
               "; ".join(f"{f['severity']}:{f['code']}" for f in o.get("flags", [])), o.get("fit_score"),
               o.get("tco"), t.get("purchase"), t.get("out_of_warranty_repairs"), t.get("downtime"),
               o.get("score") if o.get("rank") else None, o.get("passes_hard"), "; ".join(o.get("failed_constraints", [])),
               (o.get("provenance") or {}).get("kind")]
        w.writerow([_cell(v) for v in row + [o.get("attributes", {}).get(a) for a in attrs]])
    return buf.getvalue()


SPEC_LABELS = {"ram_gb": "RAM", "storage_gb": "storage", "display_in": "screen", "cpu": "CPU", "os": "OS",
               "count_per_case": "count per case", "thickness_mil": "thickness", "powder_free": "powder-free"}
SPEC_UNITS = {"ram_gb": " GB", "storage_gb": " GB", "display_in": "\"", "thickness_mil": " mil"}


def _num(v):
    return int(v) if isinstance(v, float) and v.is_integer() else v


def money(v, cur: str, d: int = 2) -> str:
    if v is None:
        return "—"
    if cur == "INR":  # Indian digit grouping: 12,34,567.00
        neg, v = v < 0, abs(v)
        whole, frac = f"{v:.{d}f}".split(".") if d else (f"{v:.0f}", "")
        head, tail = whole[:-3], whole[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        s = (head + "," + tail if head else tail) + ("." + frac if frac else "")
        return ("−" if neg else "") + "₹" + s
    return ("−" if v < 0 else "") + f"${abs(v):,.{d}f}"


def quote_request(mission, o: dict) -> dict:
    """A plain-text RFQ for one offer, built from what the agents, Fetch verification and cost analysis found."""
    req = mission.req
    q, cur = req.quantity, req.currency
    seen = (mission.created or "")[:10] or date.today().isoformat()
    name = o["name"].split(" (")[0]
    asks = [f"Your best unit price for {q} units, including any volume discount."]
    v, t, lc = o.get("verification") or {}, o.get("terms") or {}, o.get("landed") or {}
    if v.get("limit") and v["limit"]["max_units"] < q:
        asks.append(f"Your site lists {money(o['unit_price'], cur)} as a promo price limited to {v['limit']['max_units']} units "
                    f"per order. Can you extend that price, or a comparable one, to all {q} units?")
    if (moq := t.get("moq")) and moq["units"] > q:
        asks.append(f"Your page states a minimum order of {moq['units']}. Can you supply {q}?")
    stock = o.get("stock") or ""
    if re.search(r"(?i)backorder|sold out|out of stock|unavailable|limited", stock):
        asks.append(f"The listing shows \"{stock}\". What is the lead time to ship {q} units?")
    else:
        asks.append(f"Lead time to deliver {q} units" + (f" within {req.deadline_days} days." if req.deadline_days else "."))
    want_years = next((c.value for c in req.constraints if c.attribute == "warranty_years"), None)
    wa = o.get("warranty") or {}
    if req.domain.startswith("laptops") and (wa.get("service") != "onsite" or (wa.get("years") or 0) < float(want_years or 3)):
        asks.append(f"A price for {int(float(want_years or 3))}-year onsite warranty coverage "
                    f"(the listing states: {wa.get('raw') or 'no warranty terms'}).")
    if any(f["code"] == "WARRANTY_CONFLICT" for f in o.get("flags", [])):
        asks.append("Written confirmation of the warranty included at this price; your listing and product page disagree.")
    if v.get("status") in ("mismatch", "unverified", "partial"):
        asks.append("Written confirmation of the exact configuration and specifications at the quoted price.")
    if cur == "INR":
        asks.append("Whether the price includes GST, the HSN code and GST rate, and your GSTIN (so we can claim input tax credit).")
    unknown = [u.lower() for u in lc.get("unknowns", [])]
    if unknown:
        asks.append(f"The following, which your page doesn't state: {', '.join(unknown)}.")
    asks += ["How long the quote is valid, and your payment terms."]
    if cur != "INR" and "sales tax" not in unknown:
        asks.append("Shipping cost and any applicable taxes.")

    specs = ", ".join(f"{SPEC_LABELS.get(k, k.replace('_', ' '))}: {_num(val)}{SPEC_UNITS.get(k, '')}"
                      for k, val in (o.get("attributes") or {}).items()
                      if val not in (None, "") and k not in ("warranty_onsite", "warranty_years", "cpu_tier"))
    body = "\n".join([
        f"Hello {o['vendor']} sales team,",
        "",
        f"We are planning to purchase {q} × {name} and would like a written quote.",
        "",
        f"Product: {o['name']}",
        *([f"SKU: {o['sku']}"] if o.get("sku") else []),
        *([f"Listing: {o['url']}"] if o.get("url") else []),
        f"Price seen on your website ({seen}): {money(o['unit_price'], cur)} per unit" if o.get("unit_price") else "Price: not shown on the listing",
        *([f"Configuration: {specs}"] if specs else []),
        f"Quantity: {q}",
        *([f"Deliver to: {req.ship_to_state}"] if req.ship_to_state else []),
        "",
        "Could you please confirm:",
        *[f"{i}. {a}" for i, a in enumerate(asks, 1)],
        "",
        "Thank you,",
        (mission.decision or {}).get("approver") or "[Your name]",
        "[Company] · [Phone]" + (" · GSTIN: [your GSTIN]" if cur == "INR" else ""),
    ])
    return {"subject": f"Quote request: {q} × {name}", "body": body, "vendor": o["vendor"]}


STATUS_COLOR = {"verified": "#12805c", "contradicted": "#c23b2a", "inferred": "#b0611c", "unknown": "#747b8c",
                "confirmed": "#12805c", "estimated": "#b0611c", "high": "#c23b2a", "medium": "#b0611c",
                "low": "#747b8c", "info": "#747b8c"}
PRINT_CSS = """body{font:13px/1.5 "Helvetica Neue",Arial,sans-serif;max-width:960px;margin:32px auto;padding:0 32px;color:#121a2e}
h1{font:600 24px Georgia,serif;margin:0}h2{font:600 16px Georgia,serif;margin:28px 0 8px;border-bottom:1px solid #e7e2d7;padding-bottom:4px}
.muted{color:#747b8c}table{width:100%;border-collapse:collapse;margin:8px 0}td,th{border-bottom:1px solid #e7e2d7;padding:6px 8px;
text-align:left;vertical-align:top}th{background:#faf8f3;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#747b8c}
.num{text-align:right;font-variant-numeric:tabular-nums}.chip{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;
color:#fff}.banner{border:1px solid #e7ddcc;border-left:4px solid #b0611c;background:#fcefdf;padding:10px 14px;margin:12px 0}
.ok{border-left-color:#12805c;background:#e2f3eb}.snip{font:11px ui-monospace,Menlo,monospace;color:#3b4256}
@media print{button{display:none}h2{break-after:avoid}tr{break-inside:avoid}}"""


def _chip(text: str, key: str) -> str:
    return f'<span class="chip" style="background:{STATUS_COLOR.get(key, "#747b8c")}">{escape(text)}</span>'


def decision_report(mission, chain: dict) -> str:
    rep, req = mission.report, mission.req
    cur, q = req.currency, req.quantity
    offers = rep["offers"]
    by_id = {o["id"]: o for o in offers}
    dec = rep.get("decision") or {}
    human = mission.decision or {}
    pick = by_id.get(human.get("offer_id") or rep.get("recommended_offer_id"))
    synthetic = "synthetic" in rep.get("provenance", [])
    parts = [f"""<!doctype html><html><head><meta charset="utf-8"><title>Decision report {escape(mission.id)}</title>
<style>{PRINT_CSS}</style></head><body><button onclick="print()" style="float:right">Print / Save PDF</button>
<h1>Procurement decision report</h1>
<div class="muted">Mission {escape(mission.id)} · created {escape(mission.created[:16].replace('T', ' '))} UTC · mode {escape(mission.mode)}
 · generated {date.today().isoformat()}</div>"""]
    if synthetic:
        parts.append('<div class="banner"><b>Test fixture data.</b> Some offers come from synthetic, hand-written fixtures '
                     'with fictional suppliers. They exercise the checks; they are not market data.</div>')
    status = (f"Approved by {escape(human.get('approver') or 'buyer')} on {escape((human.get('at') or '')[:16].replace('T', ' '))}"
              if human.get("decision") == "approve" else "Rejected" if human.get("decision") == "reject"
              else "Pending human approval")
    parts.append(f'<div class="banner {"ok" if human.get("decision") == "approve" else ""}"><b>{status}.</b> '
                 f'Nothing has been ordered or sent by Ghost Operator.</div>')
    parts.append(f"<h2>Requirement</h2><p>{escape(mission.objective)}</p><table><tr><th>Quantity</th><th>Budget</th>"
                 f"<th>Deadline</th><th>Must-haves</th><th>Preferences</th></tr><tr><td>{q}</td>"
                 f"<td>{money(req.budget_total, cur, 0) if req.budget_total else 'none'}</td>"
                 f"<td>{f'{req.deadline_days} days' if req.deadline_days is not None else 'none'}</td>"
                 f"<td>{'<br>'.join(escape(f'{c.attribute} {c.op} {c.value}') for c in req.constraints if c.hard) or '—'}</td>"
                 f"<td>{'<br>'.join(escape(f'{c.attribute} {c.op} {c.value}') for c in req.constraints if not c.hard) or '—'}</td></tr></table>")
    if (human.get("scenario") or {}).get("changes"):
        parts.append("<div class='banner'><b>Approved under revised requirements:</b> "
                     + "; ".join(escape(c) for c in human["scenario"]["changes"]) + "</div>")
    if pick:
        lc = pick.get("landed") or {}
        parts.append(f"<h2>Recommendation</h2><p><b>{escape(pick['vendor'])}: {escape(pick['name'])}</b><br>"
                     f"Landed total {money(lc.get('total'), cur)} ({escape(lc.get('confidence', 'n/a'))}"
                     + (f"; not stated: {escape(', '.join(lc['unknowns']))}" if lc.get("unknowns") else "")
                     + f") · {money(lc.get('per_unit'), cur)}/unit</p>")
        if human.get("override"):
            parts.append(f"<div class='banner'><b>Buyer override.</b> Reason: {escape(human.get('note') or '')}</div>")
        parts.append("<ul>" + "".join(f"<li>{escape(w)}</li>" for w in dec.get("why", [])) + "</ul>")
        parts.append(f"<p class='muted'>Memo ({escape(rep.get('summary_by', ''))}): {escape(rep.get('summary', ''))}</p>")
    parts.append("<h2>Decision trace</h2><p class='muted'>Policy: " + " · ".join(escape(p) for p in dec.get("policy", []))
                 + "</p><table><tr><th>#</th><th>Offer</th><th class='num'>Score</th><th>Outcome</th><th>Reasons</th></tr>"
                 + "".join(f"<tr><td>{t['rank']}</td><td>{escape(t['vendor'])}: {escape(t['name'][:70])}</td>"
                           f"<td class='num'>{t['score']:.1f}</td><td>{escape(t['outcome'])}</td>"
                           f"<td>{'<br>'.join(escape(r) for r in t['reasons'])}</td></tr>" for t in dec.get("trace", []))
                 + "</table>")
    elig = [o for o in offers if o.get("rank")][:8]
    parts.append("<h2>Comparative statement of quotations</h2><table><tr><th>#</th><th>Supplier · item</th>"
                 "<th class='num'>Unit price</th><th class='num'>Landed total</th><th>Cost confidence</th>"
                 "<th>Warranty</th><th>Supplier risk</th><th>Verified claims</th></tr>"
                 + "".join(f"<tr><td>{o['rank']}</td><td>{escape(o['vendor'])}<br><span class='muted'>{escape(o['name'][:70])}</span></td>"
                           f"<td class='num'>{money(o.get('unit_price'), cur)}</td>"
                           f"<td class='num'>{money((o.get('landed') or {}).get('total'), cur)}</td>"
                           f"<td>{escape((o.get('landed') or {}).get('confidence', '—'))}</td>"
                           f"<td>{escape((o.get('warranty') or {}).get('raw') or 'not stated')}</td>"
                           f"<td>{escape((o.get('risk') or {}).get('level', '—'))}</td>"
                           f"<td>{sum(c['status'] == 'verified' for c in o.get('claims', []))}/{len(o.get('claims', []))}</td></tr>"
                           for o in elig) + "</table>")
    if pick:
        lc = pick.get("landed") or {}
        parts.append("<h2>Landed cost of the selected offer</h2><table><tr><th>Line</th><th class='num'>Amount</th>"
                     "<th>Status</th><th>Basis</th></tr>"
                     + "".join(f"<tr><td>{escape(l['label'])}</td><td class='num'>"
                               f"{money(l['amount'], cur) if l['amount'] is not None else 'unknown'}"
                               f"{' (incl.)' if l['included_in_price'] else ''}</td><td>{_chip(l['status'], l['status'])}</td>"
                               f"<td>{escape(l['basis'])}</td></tr>" for l in lc.get("lines", []))
                     + f"<tr><td><b>Total</b></td><td class='num'><b>{money(lc.get('total'), cur)}</b></td><td></td>"
                       f"<td>{escape((lc.get('tax') or {}).get('split') or '')}</td></tr></table>")
        parts.append("<h2>Evidence for the selected offer</h2><table><tr><th>Claim</th><th>Status</th><th>Agent said</th>"
                     "<th>Page says</th><th>Evidence</th></tr>"
                     + "".join(f"<tr><td>{escape(c['label'])}</td><td>{_chip(c['status'], c['status'])}</td>"
                               f"<td>{escape(str(c['claimed'] if c['claimed'] is not None else '—'))}</td>"
                               f"<td>{escape(str(c['observed'] if c['observed'] is not None else '—'))}</td>"
                               f"<td><div class='snip'>{escape(c['snippet'][:220])}</div><span class='muted'>"
                               f"{escape(c['uncertainty'] or '')} {escape(c.get('retrieved_at') or '')}</span></td></tr>"
                               for c in pick.get("claims", [])) + "</table>")
        rk = pick.get("risk") or {}
        parts.append("<h2>Supplier risk radar</h2><p>" + escape(rk.get("explain", "")) + "</p><table><tr><th>Dimension</th>"
                     "<th>Level</th><th>Why</th></tr>"
                     + "".join(f"<tr><td>{escape(d['label'])}</td><td>{escape(d['level'])}</td>"
                               f"<td>{'<br>'.join(escape(r) for r in d['reasons'])}</td></tr>" for d in rk.get("dimensions", []))
                     + "</table>")
    flagged = [(o, f) for o in offers for f in o.get("flags", []) if f["severity"] in ("high", "medium")]
    if flagged:
        parts.append("<h2>Adversarial findings</h2><table><tr><th>Supplier</th><th>Finding</th><th>Detail</th><th>Evidence</th></tr>"
                     + "".join(f"<tr><td>{escape(o['vendor'])}</td><td>{_chip(f['severity'], f['severity'])} {escape(f['title'])}</td>"
                               f"<td>{escape(f['detail'])}</td><td class='snip'>{escape((f['evidence'] or {}).get('snippet', '')[:200])}</td></tr>"
                               for o, f in flagged) + "</table>")
    parts.append(f"<h2>Audit trail</h2><p>{chain['entries']} hash-chained entries · chain "
                 f"{'intact' if chain['valid'] else 'BROKEN at entry ' + str(chain['broken_at'])}"
                 + (f" · head <span class='snip'>{chain.get('head', '')[:16]}…</span>" if chain.get("head") else "")
                 + f". Full log: <span class='snip'>/api/missions/{escape(mission.id)}/audit.json</span></p>")
    parts.append("<p class='muted'>Prices were captured from vendor websites (or test fixtures) at the times shown and may have "
                 "changed. Confirm with a written quote before issuing a purchase order.</p></body></html>")
    return "".join(parts)
