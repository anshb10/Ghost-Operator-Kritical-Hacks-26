"""Printable draft purchase order (browser 'Print → Save as PDF'). Never sent anywhere."""
from datetime import date
from html import escape

from .exports import money


def render_po(mission, preview_offer_id: str | None = None) -> str:
    rep = mission.report or {}
    offer_id = (mission.decision or {}).get("offer_id") or preview_offer_id or rep.get("recommended_offer_id")
    o = next((x for x in rep.get("offers", []) if x["id"] == offer_id), None)
    if not o:
        return "<p>No offer selected.</p>"
    req = mission.req
    cur = req.currency
    dec = mission.decision or {}
    approved = dec.get("decision") == "approve"
    stamp = ("APPROVED by " + escape(dec.get("approver") or "buyer") + " · " +
             escape(dec.get("at", "")[:16].replace("T", " "))) if approved else "DRAFT — PENDING HUMAN APPROVAL"
    lc = o.get("landed") or {}
    unit = o.get("effective_unit_price") or o["unit_price"]
    lim = (o.get("verification") or {}).get("limit")
    price_note = (f"<p class=\"muted\">Unit price is the expected average: the {money(o['unit_price'], cur)} promo is capped at "
                  f"{lim['max_units']} units per order, the rest are costed at list price. Request a quote to lock it in.</p>"
                  if o.get("effective_unit_price") and lim else "")
    override = ""
    scenario_pick = (dec.get("scenario") or {}).get("recommended_offer_id")
    if offer_id != rep.get("recommended_offer_id") and offer_id != scenario_pick:
        rec = next((x for x in rep.get("offers", []) if x["id"] == rep.get("recommended_offer_id")), None)
        note = (dec.get("note") or "").strip()
        why = escape(note) if note else "<i>not yet given (required at approval)</i>"
        rec_label = f"{escape(rec['name'])} ({escape(rec['vendor'])})" if rec else "offer"
        override = ('<div class="callout">'
                    f"<b>Buyer override.</b> Chosen instead of the recommended {rec_label}."
                    f"<br><b>Reason:</b> {why}</div>")
    scenario = ""
    if (dec.get("scenario") or {}).get("changes"):
        scenario = ('<div class="callout"><b>Approved under revised requirements:</b> '
                    + "; ".join(escape(c) for c in dec["scenario"]["changes"]) + "</div>")
    if lc.get("lines"):
        rows = "".join(
            f"<tr><td colspan=\"3\">{escape(l['label'])} <span class=\"st {escape(l['status'])}\">{escape(l['status'])}</span>"
            f"<div class=\"muted small\">{escape(l['basis'])}</div></td>"
            f"<td class=\"num\" colspan=\"2\">{money(l['amount'], cur) if l['amount'] is not None else 'to be quoted'}"
            f"{' <span class=muted>(included)</span>' if l['included_in_price'] else ''}</td></tr>"
            for l in lc["lines"] if not l["key"].startswith("goods"))
        total_row = (f"<tr><td colspan=\"3\" class=\"num\"><b>Landed total ({escape(lc['confidence'])})</b></td>"
                     f"<td class=\"num\" colspan=\"2\"><b>{money(lc['total'], cur)}</b></td></tr>")
        goods_total = lc.get("goods_subtotal")
    else:  # mission saved before landed cost existed
        rows, total_row, goods_total = "", "", unit * req.quantity
    unknown = (f"<p class=\"muted\">Not yet known, to confirm with the vendor: {escape(', '.join(lc['unknowns']))}.</p>"
               if lc.get("unknowns") else "")
    tax = lc.get("tax") or {}
    gst = ""
    if cur == "INR":
        gst = (f"<p><b>GST:</b> {escape(str(tax.get('rate_pct') or '—'))}% · HSN {escape(str((mission_tax(mission) or {}).get('hsn', '—')))} · "
               f"{escape(tax.get('split') or '')}<br><b>Buyer GSTIN:</b> ____________ &nbsp; <b>Supplier GSTIN:</b> ____________"
               + (f" &nbsp; <b>Ship to:</b> {escape(req.ship_to_state)}" if req.ship_to_state else "") + "</p>")
    specs = "".join(f"<li>{escape(str(k))}: {escape(str(v))}</li>" for k, v in o["evidence"].items()
                    if k not in ("product_name", "price_usd", "price_inr", "product_page_url"))
    risks = "".join(f"<li>{escape(f['title'])}: {escape(f['detail'])}</li>" for f in o.get("flags", [])
                    if f["severity"] in ("high", "medium"))
    risks += "".join(f"<li>{escape(r)}</li>" for r in o["warranty_risk_reasons"])
    tco = o.get("tco_breakdown") or {}
    fixture = ('<div class="callout"><b>Test fixture.</b> This offer comes from a synthetic fixture with a fictional supplier; '
               'do not issue it.</div>' if (o.get("provenance") or {}).get("kind") == "synthetic" else "")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>PO-{mission.id}</title>
<style>
 body{{font:14px/1.55 "Helvetica Neue",Arial,sans-serif;max-width:800px;margin:40px auto;padding:32px 40px;color:#1d2740;background:#fffdf9;border:1px solid #e7ddcc}}
 html{{background:#faf6ef}} h1,h3{{font-family:Georgia,serif;font-weight:500}} h1{{font-size:26px;margin:0}} a{{color:#2b4c9b}}
 .muted{{color:#8a8172}} .small{{font-size:12px}} table{{width:100%;border-collapse:collapse;margin:16px 0}}
 td,th{{border-bottom:1px solid #e7ddcc;padding:8px;text-align:left;vertical-align:top}} th{{background:#faf6ef;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#8a8172}}
 .stamp{{display:inline-block;padding:6px 10px;border:2px solid {'#2f6b4f' if approved else '#93561a'};
   color:{'#2f6b4f' if approved else '#93561a'};font-weight:700;letter-spacing:.04em;margin:12px 0}}
 .callout{{border:1px solid #e7ddcc;border-left:4px solid #b0611c;background:#fcefdf;padding:10px 14px;margin:14px 0}}
 .st{{font-size:11px;padding:1px 6px;border-radius:8px;background:#eee;color:#555}} .st.confirmed{{background:#e2f3eb;color:#12805c}}
 .st.estimated{{background:#fcefdf;color:#93561a}} .st.unknown{{background:#eceef2;color:#747b8c}}
 .num{{text-align:right;font-variant-numeric:tabular-nums}} @media print{{button{{display:none}}}}
</style></head><body>
<button onclick="print()" style="float:right">Print / Save PDF</button>
<h1>Purchase Order PO-{mission.id.upper()}</h1>
<div class="muted">Prepared by Ghost Operator on {date.today().isoformat()}, mission {mission.id}</div>
<div class="stamp">{stamp}</div>{fixture}
<p><b>Vendor:</b> {escape(o['vendor'])} (seller: {escape(o['seller'] or o['vendor'])})<br>
<b>Objective:</b> {escape(mission.objective)}</p>
<table><tr><th>Item</th><th>SKU</th><th class="num">Qty</th><th class="num">Unit price</th><th class="num">Line total</th></tr>
<tr><td><a href="{escape(o['url'])}">{escape(o['name'])}</a></td><td>{escape(o['sku'] or '—')}</td>
<td class="num">{req.quantity}</td><td class="num">{money(unit, cur)}</td><td class="num">{money(goods_total, cur)}</td></tr>
{rows}{total_row}</table>
{price_note}{unknown}{gst}{override}{scenario}<p><b>Warranty as listed:</b> {escape(o['warranty']['raw'] or 'not stated')}<br>
<b>Estimated {req.quantity}-unit TCO:</b> {money(o.get('tco'), cur, 0)} (purchase {money(tco.get('purchase'), cur, 0)}, out-of-warranty repairs
{money(tco.get('out_of_warranty_repairs'), cur, 0)}, downtime {money(tco.get('downtime'), cur, 0)})</p>
<h3>Specifications (as extracted from vendor site)</h3><ul>{specs}</ul>
<h3>Risks to resolve before sending</h3><ul>{risks or '<li>None flagged</li>'}</ul>
<p class="muted">Prices were captured from the vendor's public website during the mission and may have changed.
Confirm the quote with the vendor before issuing. This document is not sent anywhere automatically.</p>
</body></html>"""


def mission_tax(mission) -> dict | None:
    from . import config
    return config.load_domains().get(mission.req.domain, {}).get("tax")
