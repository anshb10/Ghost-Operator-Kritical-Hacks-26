const $ = (s) => document.querySelector(s);
// Everything scraped from vendor sites is untrusted: always escape before rendering.
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? esc(u) : "#");
const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* Money in the mission's currency: ₹12,34,567 (Indian grouping) or $1,234,567. */
const CUR = () => MISSION?.requirements?.currency || PLAN?.requirements?.currency || "USD";
function money(n, d = 0, cur = CUR()) {
  if (n == null || !isFinite(n)) return "—";
  const loc = cur === "INR" ? "en-IN" : "en-US";
  return (n < 0 ? "−" : "") + (cur === "INR" ? "₹" : "$") + Math.abs(Number(n)).toLocaleString(loc, { maximumFractionDigits: d, minimumFractionDigits: d });
}

const EXAMPLES = [
  ["50 office laptops", $("#objective").value, null],
  ["India · 25 laptops (test fixtures)", "Buy 25 laptops for our Bengaluru office, 16GB RAM, Windows 11 Pro, 3 year onsite warranty, budget ₹20 lakh, delivery within 10 days. Compare suppliers, check GST and delivery charges, and prepare a purchase order.", "replay"],
  ["Lab gloves", "Our lab needs 40 cases of powder-free nitrile exam gloves (ASTM D6319), at least 4 mil thick. Budget around $7,000. Compare suppliers and flag shelf-life or return-policy risks.", null],
  ["Dev team laptops", "Buy 12 developer laptops with 32GB RAM and 1TB SSD, Windows 11 Pro, onsite warranty required. Budget $30k.", null],
];
const LABELS = {
  ram_gb: "RAM (GB)", storage_gb: "Storage (GB)", cpu_tier: "CPU tier (1–4)", display_in: "Screen size (in)", os: "Operating system",
  cpu: "Processor", warranty_years: "Warranty (years)", warranty_onsite: "Onsite warranty (1 = yes)", count_per_case: "Count per case",
  thickness_mil: "Thickness (mil)", powder_free: "Powder-free", certifications: "Certifications",
};
const OPS = { ">=": "at least", "<=": "at most", "==": "equals", contains: "contains" };
const STAGE_NAMES = ["Ready", "Planning", "Discovering", "Sourcing", "Verifying", "Deciding", "Complete"];
const VSTATUS = {
  verified: ["✓ Verified on page", "Price and specs match the vendor's product page."],
  partial: ["◐ Specs verified", "Specs match the product page; the price is rendered client-side and couldn't be read."],
  mismatch: ["✕ Page disagrees", "The vendor's product page contradicts something the agent extracted."],
  unverified: ["○ Not checked", "The product page couldn't be read."],
};
const CLAIM = { verified: "Verified", contradicted: "Contradicted", inferred: "Inferred", unknown: "Unknown" };
const RISK_DIMS = ["evidence", "spec", "delivery", "warranty", "price", "integrity"];
const TILE_COLORS = ["#c4551f", "#3f3f46", "#12805c", "#b0611c", "#7a3fd1", "#c23b2a", "#0e7490", "#8a5a06"];
const ICONS = {
  normalize: '<path d="M4 7h16M4 12h10M4 17h6"/>',
  verify: '<path d="M12 3.5 5 6.5v5c0 4.2 3 7.6 7 9 4-1.4 7-4.8 7-9v-5z"/><path d="m9 12 2.2 2.2L15.5 10"/>',
  claims: '<path d="M9 5h10M9 12h10M9 19h10"/><path d="m3.5 5 1.2 1.2L7 4M3.5 12l1.2 1.2L7 11M4 19h2"/>',
  match: '<circle cx="8" cy="12" r="4"/><circle cx="16" cy="12" r="4"/>',
  price: '<path d="M20 12 12 20l-8-8V4h8z"/><circle cx="8.5" cy="8.5" r="1.3"/>',
  risk: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v4M12 17v.5"/>',
  rank: '<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>',
  family: '<path d="M7 7h10M7 7l3-3M7 7l3 3M17 17H7m10 0-3-3m3 3-3 3"/>',
};
const icon = (k) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${ICONS[k] || ""}</svg>`;
const ML_STEPS = [
  ["normalize", "Normalize", "Canonical specs"],
  ["verify", "Verify", "TinyFish Fetch re-reads pages"],
  ["claims", "Claims & traps", "Evidence ledger, adversarial checks"],
  ["price", "Fair price", "Ridge regression + anomalies"],
  ["risk", "Cost & risk", "Landed cost, risk radar"],
  ["rank", "Decide", "Rank, then evidence policy"],
];
const WEIGHT_LABELS = {
  fit: ["Requirement fit", "Specs match what you asked for"],
  value: ["Lifetime cost", "Landed cost + repairs + downtime"],
  risk: ["Supplier risk", "From the six-dimension radar"],
  anomaly: ["Price sanity", "Penalise suspicious prices"],
  trust: ["Source trust", "Claims confirmed on the page"],
};
const BETTER = { ram_gb: "max", storage_gb: "max", cpu_tier: "max", warranty_years: "max", thickness_mil: "max" };
const STATUS_LABEL = { awaiting_approval: "Awaiting approval", approved: "Approved", rejected: "Rejected", failed: "Failed", browsing: "Running", analyzing: "Analyzing" };

let INFO = null, PLAN = null, MISSION = null, ES = null, MODE = null;
let HISTORY = [], HISTORY_ALL = false;
let SELECTED = null;  // offer id the buyer will approve (defaults to the recommendation)
let SIM = null;       // /simulate result while the scenario differs from the mission
const COMPARE = new Set();
const clocks = {};
const trunc = (s, n) => (String(s).length > n ? String(s).slice(0, n - 1) + "…" : String(s));
const fmtDate = (iso) => new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

/* Company logo from the vendor's domain (favicon service); the coloured initial stays underneath as a fallback.
   Test-fixture domains (.test) never leave the page. */
function tile(name, url = "") {
  const n = String(name || "?").replace(/^www\./, "");
  let h = 0; for (const c of n) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const domain = (host(url) || (/\./.test(n) ? n : "")).split(".").slice(-2).join(".");
  const logo = domain && !/\.test$/.test(domain) ? `<img src="https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=128" alt="" loading="lazy" onerror="this.parentNode.classList.remove('logo');this.remove()">` : "";
  return `<span class="mono-tile ${logo ? "logo" : ""}" style="--tile:${TILE_COLORS[h % TILE_COLORS.length]}"><b>${esc(n[0].toUpperCase())}</b>${logo}</span>`;
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

/* Errors and notices go to an inline toast instead of alert() so the page stays usable. */
let toastTimer = null;
function toast(msg, kind = "bad") {
  const t = $("#toast"); t.className = "toast " + kind; $("#toast-msg").textContent = msg;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 9000);
}

/* stage = index into the 5-step pipeline (1..5); 6 = all done */
function setStage(n, label) {
  document.querySelectorAll("#stages span").forEach((s, i) => { s.className = i + 1 < n ? "done" : i + 1 === n ? "active" : ""; });
  document.querySelectorAll("#pipe li").forEach((li, i) => { li.className = i + 1 < n ? "done" : i + 1 === n ? "active" : ""; });
  $("#stage-label").textContent = label || STAGE_NAMES[Math.min(n, 6)];
}

function countUp(el) {
  const target = parseFloat(el.dataset.count), prefix = el.dataset.prefix || "", cur = el.dataset.cur;
  const fmt = (v) => (cur ? (prefix === "−" ? "−" : "") + money(Math.abs(v), 0, cur) : prefix + Math.round(v).toLocaleString("en-US"));
  if (REDUCED || !isFinite(target)) { el.textContent = fmt(target || 0); return; }
  const t0 = performance.now();
  const tick = (t) => { const k = Math.min(1, (t - t0) / 900); el.textContent = fmt(target * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
}

async function init() {
  try { INFO = await api("/api/info"); }
  catch (e) { $("#mode").innerHTML = "<i></i>Server unreachable"; toast("Can't reach the Ghost Operator server: " + e.message); return; }
  MODE = INFO.mode;
  renderMode();
  $("#examples").innerHTML = EXAMPLES.map((e, i) => `<button class="chip" data-i="${i}">${esc(e[0])}</button>`).join("");
  $("#examples").onclick = (ev) => {
    const i = ev.target.dataset.i; if (i == null) return;
    $("#objective").value = EXAMPLES[i][1];
    if (EXAMPLES[i][2]) { MODE = EXAMPLES[i][2]; renderMode(); }
  };
  $("#mode-seg").onclick = (ev) => { const m = ev.target.dataset.mode; if (m) { MODE = m; renderMode(); } };
  $("#toast-x").onclick = () => $("#toast").classList.add("hidden");
  $("#btn-plan").onclick = plan;
  $("#btn-run").onclick = dispatch;
  $("#r-cur").onchange = () => { PLAN.requirements.currency = $("#r-cur").value; renderCurrencyFields(); };
  $("#btn-addc").onclick = () => { PLAN.requirements.constraints.push({ attribute: attrsFor(PLAN.requirements.domain)[0], op: ">=", value: 0, hard: false, weight: 1 }); renderCons(); };
  $("#btn-compare").onclick = openCompare;
  $("#btn-history-all").onclick = () => { HISTORY_ALL = !HISTORY_ALL; renderHistory(); };
  $("#history").onclick = (ev) => { const b = ev.target.closest("[data-mid]"); if (b) openMission(b.dataset.mid); };
  document.querySelectorAll("dialog").forEach((d) => d.addEventListener("click", (ev) => {
    if (ev.target === d || ev.target.closest("[data-close]")) d.close();  // backdrop or ✕
  }));
  $("#q-subject").oninput = $("#q-body").oninput = updateMailto;
  $("#q-copy").onclick = copyQuote;
  $("#memo-act").onclick = (ev) => { const b = ev.target.closest("[data-act=quote]"); if (b) openQuote(b.dataset.oid); };
  $("#sim-reset").onclick = () => { resetSim(); runSim(); };
  renderSliders();
  setStage(1, "Ready");
  await loadHistory();
  const linked = location.hash.match(/^#m=([a-z0-9]+)$/i);
  if (linked) openMission(linked[1]);
}

function renderMode() {
  const m = $("#mode");
  const live = MODE === "live";
  m.className = "status " + MODE;
  m.innerHTML = `<i></i>${live ? "Live · TinyFish" : "Replay · recordings & fixtures"}`;
  $("#pipe-mode").textContent = live ? "live" : "replay";
  const seg = $("#mode-seg");
  seg.classList.toggle("hidden", !(INFO.modes || []).includes("live"));
  seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === MODE)));
}

const attrsFor = (domain) => [...(INFO.domains[domain]?.attributes || []), "warranty_years", "warranty_onsite"];

/* ── plan ─────────────────────────────────────────────── */
async function plan() {
  const b = $("#btn-plan"); b.disabled = true; b.classList.add("busy"); b.innerHTML = MODE === "live" ? "Planning &amp; searching the web" : "Planning";
  setStage(1, "Planning");
  try {
    PLAN = await api("/api/plan", { method: "POST", body: JSON.stringify({ objective: $("#objective").value, mode: MODE }) });
    const r = PLAN.requirements;
    $("#planner-used").textContent = "Planned by " + PLAN.planner;
    $("#r-item").value = r.item; $("#r-qty").value = r.quantity; $("#r-budget").value = r.budget_total ?? "";
    $("#r-cur").value = r.currency; $("#r-deadline").value = r.deadline_days ?? ""; $("#r-state").value = r.ship_to_state || "";
    $("#r-notes").textContent = r.notes || "";
    renderCurrencyFields(); renderCons(); renderVendors();
    ["#s-agents", "#s-ml", "#s-report"].forEach(hide);
    show("#s-plan"); setStage(3, "Ready to source");
    $("#s-plan").scrollIntoView({ behavior: REDUCED ? "auto" : "smooth" });
  } catch (e) { toast("Planning failed: " + e.message); setStage(1, "Ready"); }
  b.disabled = false; b.classList.remove("busy"); b.innerHTML = 'Plan mission <span class="arrow">→</span>';
}

function renderCurrencyFields() {
  const inr = $("#r-cur").value === "INR";
  $("#r-budget-l").textContent = `Total budget, landed (${inr ? "₹" : "$"})`;
  $("#r-state-f").classList.toggle("hidden", !inr);
}

function renderCons() {
  const attrs = attrsFor(PLAN.requirements.domain);
  $("#cons").innerHTML = PLAN.requirements.constraints.map((c, i) => `<tr data-i="${i}">
    <td><select data-k="attribute" aria-label="Attribute">${[...new Set([...attrs, c.attribute])].map((a) => `<option value="${esc(a)}" ${a === c.attribute ? "selected" : ""}>${esc(LABELS[a] || a)}</option>`).join("")}</select></td>
    <td><select data-k="op" aria-label="Rule">${Object.entries(OPS).map(([o, l]) => `<option value="${o}" ${o === c.op ? "selected" : ""}>${l}</option>`).join("")}</select></td>
    <td><input data-k="value" value="${esc(c.value)}" aria-label="Value"></td>
    <td class="c"><label class="switch"><input type="checkbox" data-k="hard" ${c.hard ? "checked" : ""} aria-label="Must-have"><i></i></label></td>
    <td><input data-k="weight" type="number" step="0.5" min="0" max="3" value="${esc(c.weight)}" style="width:70px" aria-label="Weight"></td>
    <td><button class="del" data-del="${i}" title="Remove" aria-label="Remove">✕</button></td></tr>`).join("");
  $("#cons").onchange = (ev) => {
    const tr = ev.target.closest("tr"), k = ev.target.dataset.k, c = PLAN.requirements.constraints[tr.dataset.i];
    if (k === "hard") c.hard = ev.target.checked;
    else if (k === "weight") c.weight = parseFloat(ev.target.value) || 0;
    else if (k === "value") c.value = ev.target.value !== "" && !isNaN(ev.target.value) ? parseFloat(ev.target.value) : ev.target.value;
    else c[k] = ev.target.value;
  };
  $("#cons").onclick = (ev) => { if (ev.target.dataset.del != null) { PLAN.requirements.constraints.splice(+ev.target.dataset.del, 1); renderCons(); } };
}

function renderVendors() {
  const disc = PLAN.discovery || { vendors: [] };
  $("#search-q").textContent = disc.query ? `“${disc.query}” · ${disc.n_results} results → ${disc.vendors.length} stores` : (disc.error ? "Search unavailable" : "No search run");
  const row = (v, kind, i) => {
    const ok = v.recorded !== false;
    const tag = v.fixture_only ? '<span class="tag fixture">Test fixture</span>'
      : !ok ? '<span class="tag na">live only</span>'
      : v.untested_live ? '<span class="tag na" title="Configured but not yet verified with a live run">untested</span>'
      : kind === "found" ? '<span class="tag found">Found by Search</span>' : '<span class="tag known">Known</span>';
    return `<label class="vrow ${ok ? "" : "off"}" title="${esc(v.snippet || "")}">${tile(v.name, v.url)}
      <span><div class="vname">${esc(v.name.replace(/^www\./, ""))}</div><div class="vdom">${esc(host(v.url))}</div></span>${tag}
      <span class="switch"><input type="checkbox" data-kind="${kind}" data-i="${i}" ${ok ? "checked" : "disabled"} aria-label="Dispatch to ${esc(v.name)}"><i></i></span></label>`;
  };
  $("#vendors").innerHTML = `<div class="vgroup">Known vendors</div>` + PLAN.vendors.map((v, i) => row(v, "known", i)).join("")
    + (disc.vendors.length ? `<div class="vgroup">Found on the live web</div>` + disc.vendors.map((v, i) => row(v, "found", i)).join("") : "");
  const count = () => { $("#vendor-count").textContent = `${document.querySelectorAll("#vendors input:checked").length} selected`; };
  $("#vendors").onchange = count; count();
}

async function dispatch() {
  const r = PLAN.requirements;
  r.item = $("#r-item").value; r.quantity = parseInt($("#r-qty").value, 10) || 1;
  r.currency = $("#r-cur").value;
  r.budget_total = $("#r-budget").value ? parseFloat($("#r-budget").value) : null;
  r.max_unit_price = r.budget_total ? Math.round((r.budget_total / r.quantity) * 100) / 100 : null;
  r.deadline_days = $("#r-deadline").value !== "" ? parseInt($("#r-deadline").value, 10) : null;
  r.ship_to_state = r.currency === "INR" ? $("#r-state").value.trim() : "";
  const picked = [...document.querySelectorAll("#vendors input:checked")];
  const vendor_keys = picked.filter((i) => i.dataset.kind === "known").map((i) => PLAN.vendors[+i.dataset.i].key);
  const extra_vendors = picked.filter((i) => i.dataset.kind === "found").map((i) => {
    const v = PLAN.discovery.vendors[+i.dataset.i]; return { key: v.key, name: v.name.replace(/^www\./, ""), url: v.url, hint: v.hint || "" };
  });
  if (!picked.length) { toast("Pick at least one vendor.", "warn"); return; }
  const b = $("#btn-run"); b.disabled = true; b.classList.add("busy");
  try {
    const { id } = await api("/api/missions", { method: "POST", body: JSON.stringify({ objective: $("#objective").value, requirements: r, vendor_keys, extra_vendors, planner: PLAN.planner, mode: PLAN.mode }) });
    MISSION = await api(`/api/missions/${id}`);
    history.replaceState(null, "", `#m=${id}`);
    ["#s-ml", "#s-report"].forEach(hide);
    renderAgents(); renderMlFlow(); show("#s-agents"); setStage(3);
    $("#s-agents").scrollIntoView({ behavior: REDUCED ? "auto" : "smooth" });
    listen(id);
  } catch (e) { toast("Could not start: " + e.message); }
  b.disabled = false; b.classList.remove("busy");
}

/* ── agents ───────────────────────────────────────────── */
function renderAgents() {
  $("#agents-sub").textContent = MISSION.mode === "live" ? `TinyFish Web Agents · ${INFO.concurrency} at a time · live browser view` : "Replaying recorded TinyFish runs and labelled test fixtures";
  $("#agents").innerHTML = Object.entries(MISSION.agents).map(([k, v], i) => `
    <article class="win ${esc(v.status)}" id="ag-${esc(k)}" style="animation-delay:${i * 70}ms">
      <div class="chrome"><span class="lights"><i></i><i></i><i></i></span>
        <span class="url"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>${esc(host(v.url))}</span>
        <span class="st ${esc(v.status)}">${esc(v.status)}</span></div>
      <div class="viewport"><div class="skeleton"><span class="bar"></span><span class="tiles"><i></i><i></i><i></i><i></i><i></i><i></i></span></div>
        <span class="cursor"></span><div class="done-mark"><div><b class="n">${v.n_offers || 0}</b>offers extracted</div></div></div>
      <ol class="log"></ol>
      <div class="win-ft"><span>${esc(v.vendor)}</span><span class="clk">0:00</span></div>
    </article>`).join("");
}

function updateAgent(ev) {
  const el = $(`#ag-${CSS.escape(ev.vendor)}`); if (!el) return;
  const log = el.querySelector(".log");
  if (ev.kind === "agent_step") {
    const li = document.createElement("li"); li.textContent = ev.message; log.appendChild(li); log.scrollTop = log.scrollHeight; return;
  }
  el.className = "win " + ev.status;
  const st = el.querySelector(".st"); st.className = "st " + ev.status;
  st.textContent = ev.status === "done" && ev.provenance === "synthetic" ? "fixture" : ev.status;
  if (ev.status === "running" && !clocks[ev.vendor]) {
    const t0 = Date.now(), clk = el.querySelector(".clk");
    clocks[ev.vendor] = setInterval(() => { const s = Math.floor((Date.now() - t0) / 1000); clk.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }, 500);
  }
  if (ev.stream_url && /^https:\/\//.test(ev.stream_url) && !el.querySelector("iframe")) {
    el.querySelector(".viewport").insertAdjacentHTML("beforeend", `<iframe src="${esc(ev.stream_url)}" title="Live browser" sandbox="allow-scripts allow-same-origin"></iframe>`);
  }
  if (ev.status === "done" || ev.status === "failed") {
    clearInterval(clocks[ev.vendor]); delete clocks[ev.vendor];
    el.querySelector("iframe")?.remove();
    if (ev.status === "done") el.querySelector(".done-mark .n").textContent = ev.n_offers ?? 0;
    if (ev.error) { const li = document.createElement("li"); li.className = "err"; li.textContent = ev.error; log.appendChild(li); }
  }
}

/* ── analysis ─────────────────────────────────────────── */
function renderMlFlow() {
  $("#mlflow").innerHTML = ML_STEPS.map(([k, t, d]) => `<div class="mlnode ${k}" id="ml-${k}"><span class="ic">${icon(k)}</span><h4>${t}</h4><p>${d}</p></div>`).join("");
}

function updateMl(ev) {
  if ($("#s-ml").classList.contains("hidden")) show("#s-ml");
  setStage(ev.step === "verify" || ev.step === "claims" ? 4 : 5);
  const node = $(`#ml-${CSS.escape(ev.step)}`); if (!node) return;
  node.classList.add("on");
  node.querySelector("p").textContent = shortMl(ev);
}
function shortMl(ev) {
  const s = ev.summary;
  if (ev.step === "verify" && s) return `${s.pages_fetched} pages re-read · ${s.verified} confirmed · ${s.mismatch} disagree${s.corrections ? ` · ${s.corrections} fixed` : ""}`;
  return ev.message.replace(/^[^:]+:\s*/, "").replace(/\(.*?\)/g, "").trim();
}

function listen(id) {
  ES?.close();  // only ever follow the mission on screen
  const es = ES = new EventSource(`/api/missions/${id}/events`);
  es.onmessage = async (msg) => {
    if (MISSION?.id !== id) { es.close(); return; }
    const ev = JSON.parse(msg.data);
    if (ev.kind === "agent" || ev.kind === "agent_step") updateAgent(ev);
    else if (ev.kind === "ml" || ev.kind === "verify") updateMl(ev);
    else if (ev.kind === "report") { MISSION = await api(`/api/missions/${id}`); renderReport(); loadHistory(); }
    else if (ev.kind === "status" && ev.status === "failed") { toast("Mission failed: " + ev.error); loadHistory(); }
  };
  es.onerror = () => { if (es.readyState === EventSource.CLOSED) toast("Lost the live connection; reopen the mission from history.", "warn"); };
  es.addEventListener("end", () => es.close());
}

/* ── mission history ──────────────────────────────────── */
async function loadHistory() {
  try { HISTORY = await api("/api/missions"); } catch { HISTORY = []; }
  try { INFO.memory = await api("/api/memory/stats"); } catch { /* memory optional */ }
  renderHistory();
}

function renderHistory() {
  if (!HISTORY.length) { hide("#s-history"); return; }
  const n = HISTORY.length, list = HISTORY_ALL ? HISTORY : HISTORY.slice(0, 6), mem = INFO.memory;
  $("#history-sub").textContent = `${n} saved` + (mem ? ` · memory: ${mem.observations} distinct price observations across ${mem.configurations} configurations, ${mem.decisions} decisions, ${mem.outcomes} outcomes` : "");
  $("#btn-history-all").textContent = HISTORY_ALL ? "Show fewer" : `Show all ${n}`;
  $("#btn-history-all").classList.toggle("hidden", n <= 6);
  $("#history").innerHTML = list.map((h) => `
    <button class="card hcard ${MISSION?.id === h.id ? "current" : ""}" data-mid="${esc(h.id)}">
      <div class="h-top">${h.pick ? tile(h.pick.vendor, h.pick.url) : tile(h.item)}
        <div><div class="h-item">${esc(h.quantity)} × ${esc(h.item)}</div><div class="h-date">${esc(fmtDate(h.created))} · ${esc(h.mode)}${(h.provenance || []).includes("synthetic") ? " · fixture" : ""}</div></div>
        <span class="hst ${esc(h.status)}">${esc(STATUS_LABEL[h.status] || h.status)}</span></div>
      <div class="h-pick">${h.pick ? `<b>${esc(trunc(h.pick.name, 70))}</b><span>${esc(h.pick.vendor)} · ${money(h.pick.total, 0, h.currency)}${h.pick.landed ? " landed" : ""}${h.approver ? ` · signed by ${esc(h.approver)}` : ""}</span>`
        : `<span>${h.n_offers ? "No offer met every must-have" : "No report yet"}</span>`}</div>
    </button>`).join("");
  show("#s-history");
}

async function openMission(id) {
  try { MISSION = await api(`/api/missions/${encodeURIComponent(id)}`); }
  catch (e) { history.replaceState(null, "", location.pathname); toast("Could not open mission " + id + ": " + e.message); return; }
  history.replaceState(null, "", `#m=${MISSION.id}`);
  $("#objective").value = MISSION.objective;
  ["#s-plan", "#s-agents", "#s-ml", "#s-report"].forEach(hide);
  if (MISSION.report) renderReport();
  else if (MISSION.status === "browsing" || MISSION.status === "analyzing") {
    renderAgents(); renderMlFlow(); show("#s-agents"); setStage(3); listen(MISSION.id);
    $("#s-agents").scrollIntoView({ behavior: REDUCED ? "auto" : "smooth" });
  } else toast(`Mission ${MISSION.id} has no decision brief (status: ${MISSION.status}).`, "warn");
  renderHistory();
}

/* ── decision brief ───────────────────────────────────── */
const unitOf = (o) => o.effective_unit_price || o.unit_price;
const landedOf = (o) => (o.landed && o.landed.total != null ? o.landed.total : unitOf(o) * MISSION.requirements.quantity);
const offerById = (id) => MISSION.report.offers.find((o) => o.id === id);
const recId = () => (SIM ? SIM.after?.offer_id : MISSION.report.recommended_offer_id);
const confChip = (c) => (c ? `<span class="cc ${esc(c)}">${esc(c)}</span>` : "");
const levelChip = (l) => `<span class="lv ${esc(l || "unknown")}">${esc(l || "unknown")}</span>`;

function renderReport() {
  const rep = MISSION.report, req = MISSION.requirements, offers = rep.offers;
  const best = offers.find((o) => o.id === rep.recommended_offer_id);
  SELECTED = MISSION.decision?.offer_id || rep.recommended_offer_id;
  SIM = null; COMPARE.clear();
  setStage(5, MISSION.decision ? "Complete" : "Awaiting approval");
  $("#summary-by").textContent = rep.summary_by === "claude" ? "Memo drafted by Claude, figures checked against the cost engine" : "Memo drafted from the ranked evidence";
  $("#memo").textContent = rep.summary;
  $("#memo-check").innerHTML = rep.memo_check ? (rep.memo_check.ok ? `<span class="cc confirmed">${rep.memo_check.checked} figures grounded</span>` : `<span class="cc unknown">LLM memo rejected: unsupported figures</span>`) : "";
  $("#csv-link").href = `/api/missions/${MISSION.id}/offers.csv`;
  $("#report-link").href = `/missions/${MISSION.id}/report`;
  $("#audit-link").href = `/api/missions/${MISSION.id}/audit.json`;
  const valuePick = offers.find((o) => o.id === rep.value_pick_id);
  $("#memo-act").innerHTML = valuePick ? `<button class="btn btn-ghost btn-sm" data-act="quote" data-oid="${esc(valuePick.id)}">Draft quote request to ${esc(valuePick.vendor)}</button>` : "";
  const prov = rep.provenance || [];
  const fb = $("#fixture-banner");
  fb.classList.toggle("hidden", !prov.includes("synthetic") && MISSION.mode !== "replay");
  fb.innerHTML = prov.includes("synthetic")
    ? "<b>Test fixtures.</b> These suppliers are fictional and their pages hand-written to exercise the checks (see fixtures/replay/laptops_in/README.md). Not market data."
    : "<b>Replay.</b> Offers and pages are real TinyFish recordings from the dates shown in each claim, not today's prices.";

  if (best) {
    const a = best.attributes || {}, lc = best.landed || {};
    const specs = [
      a.ram_gb && `${a.ram_gb} GB RAM`, a.storage_gb && `${a.storage_gb >= 1024 ? a.storage_gb / 1024 + " TB" : a.storage_gb + " GB"} SSD`,
      a.thickness_mil && `${a.thickness_mil} mil`, a.count_per_case && `${a.count_per_case}/case`, a.os,
    ].filter(Boolean).map((s) => `<span class="spec">${esc(s)}</span>`);
    specs.push(`<span class="spec ${best.warranty.service === "onsite" ? "ok" : "warn"}">${esc(best.warranty.raw || "Warranty not stated")}</span>`);
    if (best.verification?.status === "verified") specs.unshift(`<span class="spec ok">✓ Verified on vendor page</span>`);
    $("#pick").innerHTML = `
      <div class="pick-top">${tile(best.vendor, best.url)}<div><div class="pick-label">${rep.decision?.provisional ? "Provisional · needs quote" : "Recommended"}</div><div class="pick-vendor">${esc(best.vendor)}${best.sku ? " · " + esc(best.sku) : ""} · ${esc(best.stock || "stock n/a")}</div></div></div>
      <h3><a class="pname" href="${safeUrl(best.url)}" target="_blank" rel="noopener noreferrer">${esc(best.name)}</a></h3>
      <div class="specs">${specs.join("")}</div>
      <div class="pick-price"><span class="big">${money(lc.per_unit ?? unitOf(best))}</span><span class="unit">per unit, landed</span>
        <span class="total">${req.quantity} units ${confChip(lc.confidence)}<b>${money(landedOf(best))}</b></span></div>
      ${lc.unknowns?.length ? `<p class="unk">Not stated by the vendor: ${esc(lc.unknowns.join(", "))}. Ask in the quote.</p>` : ""}`;
    const cl = best.claims || [];
    const ver = cl.filter((c) => c.status === "verified").length;
    const total = landedOf(best);
    const headroom = req.budget_total ? req.budget_total - total : null;
    $("#kpis").innerHTML = [
      ["var(--brand)", "Landed total", `<span data-count="${total}" data-cur="${CUR()}">0</span>`, `${confChip(lc.confidence)} incl. ${lc.tax?.name || "tax"} ${lc.tax?.inclusion === "inclusive" ? "(in price)" : lc.tax?.rate_pct != null ? lc.tax.rate_pct + "%" : "unknown"}`],
      ["var(--navy)", "Lifetime cost", `<span data-count="${best.tco}" data-cur="${CUR()}">0</span>`, "landed + expected repairs & downtime"],
      ["var(--ok)", "Budget headroom", headroom == null ? "—" : `<span data-count="${Math.abs(headroom)}" data-prefix="${headroom < 0 ? "−" : ""}" data-cur="${CUR()}">0</span>`,
        headroom == null ? "No budget set" : `<span class="s ${headroom >= 0 ? "ok" : "bad"}">${headroom >= 0 ? "within" : "over"} ${money(req.budget_total)}</span>`],
      ["var(--gold)", "Claims verified", `<span data-count="${ver}">0</span><span style="color:var(--faint);font-size:18px"> / ${cl.length}</span>`,
        `<div class="meter"><i style="width:${cl.length ? Math.round((ver / cl.length) * 100) : 0}%;background:var(--ok)"></i></div>`],
    ].map(([c, l, v, s]) => `<div class="card kpi"><div class="l"><i style="background:${c}"></i>${l}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join("");
    document.querySelectorAll("#kpis [data-count]").forEach(countUp);
  } else {
    $("#pick").innerHTML = `<div class="pick-label">No recommendation</div><h3>No option met every must-have.</h3><p class="notes">See the excluded offers below, or try a different budget, deadline or requirement in the scenario simulator.</p>`;
    $("#kpis").innerHTML = "";
  }

  renderTrace(); renderFindings();
  const ins = [];
  (rep.insights.families || []).slice(0, 2).forEach((f) => {
    const better = f.cheaper_has_better.length ? ` and has better ${f.cheaper_has_better.join(", ").replace(/_gb|_tier/g, "")}` : "";
    const worse = f.cheaper_has_worse.length ? `, but lower ${f.cheaper_has_worse.join(", ").replace(/_gb|_tier/g, "")}` : "";
    ins.push(["family", "Same model, other vendor", `<b>${esc(trunc(f.cheaper.name, 44))}</b> is ${Math.round(f.saving_pct * 100)}% cheaper at ${esc(f.cheaper.vendor)} than at ${esc(f.pricier.vendor)}${better}${worse}.`]);
  });
  (rep.insights.cross_vendor || []).forEach((d) => ins.push(["same", "Identical product", `${esc(d.name)}: ${money(d.best_price)} at ${esc(d.best_vendor)} vs ${money(d.worst_price)} at ${esc(d.worst_vendor)}.`]));
  const seen = offers.filter((o) => o.history?.comparable).slice(0, 1);
  seen.forEach((o) => { const h = o.history; ins.push(["price", "Seen before", `${esc(trunc(o.name, 40))}: comparable configuration last observed at ${money(h.last.unit_price)} on ${esc(String(h.last.observed_at).slice(0, 10))} (${esc(h.last.kind)}), now ${money(o.unit_price)} (${h.delta_vs_last_pct > 0 ? "+" : ""}${Math.round(h.delta_vs_last_pct * 100)}%).`]); });
  $("#insights").innerHTML = ins.map(([k, t, b], i) => `<div class="card insight ${k}" style="animation-delay:${i * 70}ms"><span class="ii">${icon(k === "same" ? "match" : k)}</span><div><span class="k">${t}</span>${b}</div></div>`).join("");

  resetSim(); renderSim(); renderTable(); renderDecision(); renderOutcome(); loadAudit();
  show("#s-report");
  $("#s-report").scrollIntoView({ behavior: REDUCED ? "auto" : "smooth" });
}

function renderTrace() {
  const d = SIM ? SIM.decision : MISSION.report.decision;
  if (!d) { $("#trace").innerHTML = `<li class="muted">This mission was saved before decision traces existed.</li>`; $("#roles").innerHTML = ""; return; }
  $("#trace").innerHTML = (d.trace || []).map((t) => `<li class="${esc(t.outcome)}"><span class="tr-rk">#${t.rank}</span>
      <div><b>${esc(t.vendor)}</b> <span class="muted">${esc(trunc(t.name, 52))} · score ${Math.round(t.score)}</span>
      <div class="tr-out">${t.outcome === "recommended" ? "✓ Recommended" : t.outcome === "provisional" ? "◐ Provisional pick" : "Skipped"}: ${t.reasons.map(esc).join("; ")}</div></div></li>`).join("")
    || `<li class="muted">No eligible offer.</li>`;
  const why = (d.why || []).map((w) => `<li>${esc(w)}</li>`).join("");
  $("#roles").innerHTML = `<ul class="why">${why}</ul><h5>Policy</h5><ol class="policy-l">${(d.policy || []).map((p) => `<li>${esc(p)}</li>`).join("")}</ol>`
    + ((MISSION.report.roles || []).length ? `<h5>Who did what</h5><table class="roles">${MISSION.report.roles.map((r) => `<tr><th>${esc(r.role)}</th><td>${esc(r.responsibility)}<div class="muted">${esc(r.output)}</div></td></tr>`).join("")}</table>` : "");
}

function renderFindings() {
  const offers = viewOffers();
  const items = offers.flatMap((o) => (o.flags || []).filter((f) => f.severity === "high" || f.severity === "medium").map((f) => ({ o, f })));
  items.sort((a, b) => (a.f.severity === "high" ? 0 : 1) - (b.f.severity === "high" ? 0 : 1));
  $("#findings-meta").textContent = `${items.length} finding${items.length === 1 ? "" : "s"} across ${new Set(items.map((x) => x.o.id)).size} offers`;
  $("#findings").innerHTML = items.length ? items.slice(0, 12).map(({ o, f }) => `<div class="fd ${esc(f.severity)}">
      <div class="fd-hd"><span class="sev">${esc(f.severity)}</span><b>${esc(f.title)}</b></div>
      <div class="fd-who">${esc(o.vendor)} · ${esc(trunc(o.name, 50))}</div>
      <div class="fd-d">${esc(f.detail)}</div>
      ${f.evidence?.snippet ? `<blockquote>${esc(trunc(f.evidence.snippet, 220))}</blockquote>` : ""}</div>`).join("")
    + (items.length > 12 ? `<p class="muted">${items.length - 12} more in the offer details.</p>` : "")
    : `<p class="muted">No medium or high findings. Unknowns (not stated by vendors) are listed per offer, not treated as problems.</p>`;
}

/* Offers in display order: the scenario's ranking when the simulator differs from the mission, else the mission's own. */
function viewOffers() {
  const offers = MISSION.report.offers;
  if (!SIM) return offers;
  const byId = new Map(offers.map((o) => [o.id, o]));
  return SIM.offers.map((x) => ({ ...byId.get(x.id), ...x }));
}

function renderTable() {
  const rep = MISSION.report, offers = viewOffers();
  const origRank = new Map(rep.offers.map((o) => [o.id, o.rank]));
  const valueId = SIM ? SIM.value_pick_id : rep.value_pick_id;
  const rid = recId();
  const locked = !!MISSION.decision;
  const eligible = offers.filter((o) => o.rank).length;
  $("#offers-meta").textContent = `${offers.length} offers · ${eligible} eligible · ${offers.length - eligible} excluded${SIM ? " · scenario ranking" : ""}`;
  $("#price-note").textContent = "Landed total = goods (with promo caps and discounts) + delivery + fees + tax; confirmed = stated on the vendor's page, estimated = a stated assumption, incomplete = something isn't stated (listed, never assumed zero). Score weighs fit, lifetime cost, supplier risk, price sanity and source trust.";
  const firstExcluded = offers.findIndex((o) => !o.rank);
  $("#offers").innerHTML = offers.map((o, idx) => {
    const isBest = o.id === rid;
    const moved = SIM && o.rank && origRank.get(o.id) ? origRank.get(o.id) - o.rank : 0;
    const hi = (o.flags || []).filter((f) => f.severity === "high" || f.severity === "medium");
    const flags = [
      isBest ? `<span class="flag rec">${SIM ? "Scenario pick" : "Recommended"}</span>` : "",
      SIM && o.id === rep.recommended_offer_id && !isBest ? '<span class="flag">Mission pick</span>' : "",
      o.id === SELECTED && !isBest ? `<span class="flag mine">${locked ? "Approved instead" : "Your choice"}</span>` : "",
      o.id === valueId ? '<span class="flag quote">Ask for quote</span>' : "",
      (o.provenance || {}).kind === "synthetic" ? '<span class="flag fixture">fixture</span>' : "",
      ...hi.slice(0, 3).map((f) => `<span class="flag ${f.severity === "high" ? "bad" : "warn"}" title="${esc(f.detail)}">${esc(f.title)}</span>`),
      hi.length > 3 ? `<span class="flag">+${hi.length - 3}</span>` : "",
      ...o.failed_constraints.filter((f) => !f.startsWith("delivery")).map((f) => `<span class="flag ${f.startsWith("unverified") ? "warn" : "bad"}">${esc(f)}</span>`),
    ].join("");
    const v = o.verification?.status, lc = o.landed || {}, rk = o.risk || {};
    const sep = idx === firstExcluded ? `<tr class="excluded-hd"><td colspan="10">Excluded · did not meet a must-have, the budget or the deadline</td></tr>` : "";
    const choose = locked ? "" : o.rank
      ? (o.id === SELECTED ? `<span class="act-note">✓ Selected for approval</span>` : `<button class="btn btn-primary btn-sm" data-act="choose" data-oid="${esc(o.id)}">Choose this offer</button>`)
      : `<span class="act-note">Excluded offers can't be approved</span>`;
    return `${sep}<tr class="row ${isBest ? "best" : ""} ${o.id === SELECTED && !isBest ? "mine" : ""} ${o.rank ? "" : "excluded"}" style="animation-delay:${Math.min(idx, 14) * 40}ms">
      <td class="cmpcol"><input type="checkbox" data-cmp="${esc(o.id)}" ${COMPARE.has(o.id) ? "checked" : ""} aria-label="Compare ${esc(o.name)}"></td>
      <td class="rk">${o.rank ?? "–"}${moved ? `<span class="mv ${moved > 0 ? "up" : "down"}">${moved > 0 ? "▲" : "▼"}${Math.abs(moved)}</span>` : ""}</td>
      <td><div class="pcell">${tile(o.vendor, o.url)}<div><a class="pname" href="${safeUrl(o.url)}" target="_blank" rel="noopener noreferrer">${esc(o.name)}</a>
        <div class="pvendor">${esc(o.vendor)}${o.sku ? " · " + esc(o.sku) : ""}</div><div class="flags">${flags}</div></div></div></td>
      <td class="num">${money(o.unit_price, 2)}${o.effective_unit_price ? `<div class="eff">≈ ${money(o.effective_unit_price)} avg (capped promo)</div>` : ""}
        ${v ? `<div class="vb ${esc(v)}" title="${esc(VSTATUS[v]?.[1] || "")}">${VSTATUS[v]?.[0] || ""}</div>` : ""}</td>
      <td class="num">${lc.total != null ? money(lc.total) : "—"}<div>${confChip(lc.confidence)}</div></td>
      <td><div class="mini"><div class="meter"><i style="width:${Math.round(o.fit_score * 100)}%"></i></div><span>${Math.round(o.fit_score * 100)}%</span></div></td>
      <td><div class="wtxt">${esc(o.warranty.raw || "not stated")}</div></td>
      <td>${levelChip(rk.level)}${rk.uncertainty > 0.35 ? `<div class="unc" title="Share of the assessment based on missing information">${Math.round(rk.uncertainty * 100)}% unknown</div>` : ""}</td>
      <td class="num"><span class="score">${o.rank ? Math.round(o.score) : "–"}</span></td>
      <td><button class="btn btn-link" data-id="${esc(o.id)}" aria-expanded="false">Why</button></td></tr>
      <tr class="details hidden" id="d-${esc(o.id)}"><td colspan="10">
        <div class="row-acts">${choose}${o.unit_price ? `<button class="btn btn-ghost btn-sm" data-act="quote" data-oid="${esc(o.id)}">Draft quote request</button>` : ""}</div>
        ${detailTabs(o)}</td></tr>`;
  }).join("");
  $("#offers").onclick = (ev) => {
    const tab = ev.target.closest("[data-tab]");
    if (tab) { const box = tab.closest(".tabs"); box.querySelectorAll("[data-tab]").forEach((b) => b.setAttribute("aria-selected", String(b === tab))); box.querySelectorAll(".pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== tab.dataset.tab)); return; }
    const b = ev.target.closest("button"); if (!b) return;
    if (b.dataset.id) { const d = $(`#d-${CSS.escape(b.dataset.id)}`); d.classList.toggle("hidden"); b.setAttribute("aria-expanded", String(!d.classList.contains("hidden"))); }
    else if (b.dataset.act === "choose") choose(b.dataset.oid);
    else if (b.dataset.act === "quote") openQuote(b.dataset.oid);
  };
  $("#offers").onchange = (ev) => {
    const id = ev.target.dataset.cmp; if (!id) return;
    if (ev.target.checked) COMPARE.add(id); else COMPARE.delete(id);
    updateCompareUi();
  };
  updateCompareUi();
}

/* Re-render the table but keep any open "Why" panels (and their tab) open. */
function refreshTable() {
  const open = [...document.querySelectorAll("#offers tr.details:not(.hidden)")].map((tr) => [tr.id, tr.querySelector("[data-tab][aria-selected=true]")?.dataset.tab]);
  renderTable();
  open.forEach(([id, tab]) => {
    const tr = document.getElementById(id); if (!tr) return;
    tr.classList.remove("hidden");
    if (tab) tr.querySelector(`[data-tab="${tab}"]`)?.click();
  });
}

function detailTabs(o) {
  const tabs = [["evidence", "Evidence"], ["cost", "Landed cost"], ["risk", "Risk radar"], ["findings", `Findings (${(o.flags || []).length})`], ["history", "History"], ["raw", "Raw extraction"]];
  return `<div class="tabs"><div class="tabbar" role="tablist">${tabs.map(([k, l], i) => `<button role="tab" data-tab="${k}" aria-selected="${i === 0}">${l}</button>`).join("")}</div>
    <div class="pane" data-pane="evidence">${evidenceHtml(o)}</div>
    <div class="pane hidden" data-pane="cost">${costHtml(o)}</div>
    <div class="pane hidden" data-pane="risk">${riskHtml(o)}</div>
    <div class="pane hidden" data-pane="findings">${findingsHtml(o)}</div>
    <div class="pane hidden" data-pane="history">${historyHtml(o)}</div>
    <div class="pane hidden" data-pane="raw"><pre>${esc(JSON.stringify(o.evidence, null, 1))}</pre>
      <p class="src">Provenance: ${esc((o.provenance || {}).kind || "unknown")} · ${esc((o.provenance || {}).source || "")}</p></div></div>`;
}

function evidenceHtml(o) {
  const cl = o.claims || [];
  if (!cl.length) return `<p class="muted">No claims ledger for this offer (mission predates it).</p>`;
  const fmtv = (f, x) => (x == null ? "—" : Array.isArray(x) ? x.map((y) => (f === "price" ? money(y, 2) : esc(y))).join(" / ") : f === "price" && typeof x === "number" ? money(x, 2) : esc(x));
  const fixes = (o.verification?.corrections || []).map((c) => `<li>${esc(c)}</li>`).join("");
  return `<table class="claims"><thead><tr><th>Claim</th><th>Status</th><th>Agent said</th><th>Vendor page says</th><th>Evidence</th></tr></thead><tbody>
    ${cl.map((c) => `<tr><td>${esc(c.label)}</td><td><span class="cs ${esc(c.status)}">${CLAIM[c.status] || esc(c.status)}</span></td>
      <td>${fmtv(c.field, c.claimed)}</td><td>${fmtv(c.field, c.observed)}</td>
      <td>${c.snippet ? `<q>${esc(trunc(c.snippet, 180))}</q>` : ""}<div class="muted small">${esc(c.uncertainty || c.method || "")}${c.retrieved_at ? ` · retrieved ${esc(String(c.retrieved_at).slice(0, 16).replace("T", " "))}` : ""}
      ${c.source_url && c.status !== "unknown" ? ` · <a href="${safeUrl(c.source_url)}" target="_blank" rel="noopener noreferrer">${esc(host(c.source_url))}</a>` : ""}</div></td></tr>`).join("")}</tbody></table>
    ${fixes ? `<h5>Corrections applied (the page wins)</h5><ul>${fixes}</ul>` : ""}`;
}

function costHtml(o) {
  const lc = o.landed || {};
  if (!lc.lines) return `<p class="muted">No landed-cost breakdown for this offer.</p>`;
  const t = o.tco_breakdown || {};
  return `<table class="claims"><thead><tr><th>Line</th><th class="num">Amount</th><th>Status</th><th>Basis</th></tr></thead><tbody>
    ${lc.lines.map((l) => `<tr><td>${esc(l.label)}</td><td class="num">${l.amount == null ? "unknown" : money(l.amount, 2)}${l.included_in_price ? ' <span class="muted">(included)</span>' : ""}</td>
      <td><span class="cc ${esc(l.status)}">${esc(l.status)}</span></td><td class="small">${esc(l.basis)}${l.evidence ? `<q>${esc(trunc(l.evidence, 120))}</q>` : ""}</td></tr>`).join("")}
    <tr class="tot"><td><b>Landed total</b></td><td class="num"><b>${money(lc.total, 2)}</b></td><td>${confChip(lc.confidence)}</td><td class="small">${esc(lc.tax?.split || "")}${lc.unknowns?.length ? ` Unknown, not included: ${esc(lc.unknowns.join(", "))}.` : ""}</td></tr></tbody></table>
    <p class="small muted">Lifetime cost ${money(o.tco)} = landed ${money(t.purchase)} + expected out-of-warranty repairs ${money(t.out_of_warranty_repairs)} + downtime ${money(t.downtime)}.${o.fair_price ? ` Fair-price model: ${money(o.fair_price)} (${o.price_delta_pct > 0 ? "+" : ""}${Math.round((o.price_delta_pct || 0) * 100)}%).` : ""}${o.anomaly ? " " + esc(o.anomaly) + "." : ""}</p>`;
}

function radarSvg(dims) {
  const cx = 110, cy = 100, R = 76, n = dims.length;
  const pt = (i, r) => [cx + r * Math.sin((2 * Math.PI * i) / n), cy - r * Math.cos((2 * Math.PI * i) / n)];
  const ring = (f) => dims.map((_, i) => pt(i, R * f).join(",")).join(" ");
  const poly = dims.map((d, i) => pt(i, R * (d.risk ?? 0)).join(",")).join(" ");
  return `<svg class="radar" viewBox="-40 -4 300 208" role="img" aria-label="Supplier risk radar">
    ${[0.25, 0.5, 0.75, 1].map((f) => `<polygon points="${ring(f)}" class="ring"/>`).join("")}
    ${dims.map((d, i) => { const [x, y] = pt(i, R); return `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" class="axis ${d.risk == null ? "unk" : ""}"/>`; }).join("")}
    <polygon points="${poly}" class="area"/>
    ${dims.map((d, i) => { const [x, y] = pt(i, R + 14); return `<text x="${x}" y="${y}" class="${d.risk == null ? "unk" : ""}" text-anchor="${x < cx - 5 ? "end" : x > cx + 5 ? "start" : "middle"}" dominant-baseline="middle">${esc(d.label.split(" ")[0])}</text>`; }).join("")}
  </svg>`;
}

function riskHtml(o) {
  const rk = o.risk || {};
  if (!rk.dimensions) return `<p class="muted">No risk radar for this offer.</p>`;
  return `<div class="riskwrap">${radarSvg(rk.dimensions)}<div><p><b>${esc(rk.explain)}</b></p>
    <p class="small muted">Outer edge = high risk. Grey axes are unknown: missing evidence raises uncertainty (${Math.round((rk.uncertainty || 0) * 100)}%), not the risk score.</p>
    <ul class="dims">${rk.dimensions.map((d) => `<li>${levelChip(d.level)} <b>${esc(d.label)}</b><ul>${d.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></li>`).join("")}</ul></div></div>`;
}

function findingsHtml(o) {
  const fl = o.flags || [];
  if (!fl.length) return `<p class="muted">No findings for this offer.</p>`;
  return fl.map((f) => `<div class="fd ${esc(f.severity)}"><div class="fd-hd"><span class="sev">${esc(f.severity)}</span><b>${esc(f.title)}</b> <code>${esc(f.code)}</code></div>
    <div class="fd-d">${esc(f.detail)}</div>${f.evidence?.snippet ? `<blockquote>${esc(f.evidence.snippet)}</blockquote>` : ""}</div>`).join("");
}

function historyHtml(o) {
  const h = o.history || {};
  const vr = h.vendor_record || {};
  const track = vr.approved_orders ? `<p>Your record with ${esc(o.vendor)}: ${vr.approved_orders} approved order(s), ${vr.outcomes} outcome(s) recorded${vr.outcomes ? `, ${vr.on_time} on time` : ""}.</p>` : `<p class="muted">No earlier approved orders with ${esc(o.vendor)}.</p>`;
  if (!h.comparable) return track + `<p class="muted">No earlier observation of a comparable configuration (same model family and price-driving specs, same currency). Replaying the same recording doesn't count as a new observation.</p>`;
  return track + `<p>${h.comparable} earlier observation(s) of a comparable configuration · range ${money(h.min)}–${money(h.max)} · now ${h.delta_vs_last_pct > 0 ? "+" : ""}${Math.round(h.delta_vs_last_pct * 100)}% vs last.</p>
    <table class="claims"><thead><tr><th>Observed</th><th>Vendor · listing</th><th class="num">Unit price</th><th>Source</th></tr></thead><tbody>
    ${h.observations.map((r) => `<tr><td>${esc(String(r.observed_at).slice(0, 10))}</td><td>${esc(r.vendor)} · ${esc(trunc(r.name, 50))}</td><td class="num">${money(r.unit_price, 2)}</td><td><span class="cc ${r.kind === "live" ? "confirmed" : r.kind === "synthetic" ? "unknown" : "estimated"}">${esc(r.kind)}</span> ${esc(r.verification || "")}</td></tr>`).join("")}</tbody></table>`;
}

/* ── scenario simulator ───────────────────────────────── */
let simTimer = null, simSeq = 0;
const sliderWeights = () => Object.fromEntries([...document.querySelectorAll("#sliders [data-w]")].map((i) => [i.dataset.w, +i.value]));
const isDefaultWeights = (w) => Object.entries(INFO.weights).every(([k, d]) => Math.abs(w[k] - d * 100) < 0.5);

function renderSliders() {
  $("#sliders").innerHTML = Object.entries(WEIGHT_LABELS).map(([k, [l, h]]) => `
    <label class="slider"><span class="sl-top"><b>${l}</b><output id="w-${k}"></output></span>
      <input type="range" min="0" max="100" step="5" data-w="${k}" aria-label="${l} weight"><small>${h}</small></label>`).join("");
  $("#sliders").oninput = (ev) => { if (ev.target.dataset.w) { showWeightShares(); queueSim(); } };
  resetSliders();
}
function resetSliders() {
  document.querySelectorAll("#sliders [data-w]").forEach((i) => { i.value = Math.round(INFO.weights[i.dataset.w] * 100); });
  showWeightShares();
}
function showWeightShares() {
  const w = sliderWeights(), total = Object.values(w).reduce((a, b) => a + b, 0) || 1;
  Object.entries(w).forEach(([k, v]) => { $(`#w-${k}`).textContent = `${Math.round((v / total) * 100)}%`; });
}

function resetSim() {
  const r = MISSION.requirements;
  $("#sim-budget-l").textContent = `Budget (${r.currency === "INR" ? "₹" : "$"})`;
  $("#sim-budget").value = r.budget_total ?? ""; $("#sim-deadline").value = r.deadline_days ?? "";
  $("#sim-qty").value = r.quantity; $("#sim-tax").value = r.tax_rate_pct ?? "";
  $("#sim-cons").innerHTML = r.constraints.map((c, i) => `<div class="sc" data-i="${i}"><span>${esc(LABELS[c.attribute] || c.attribute)} ${esc(OPS[c.op])}</span>
    <input data-k="value" value="${esc(c.value)}" aria-label="${esc(LABELS[c.attribute] || c.attribute)} value">
    <label class="switch" title="Must-have"><input type="checkbox" data-k="hard" ${c.hard ? "checked" : ""} aria-label="${esc(LABELS[c.attribute] || c.attribute)} must-have"><i></i></label></div>`).join("");
  resetSliders();
  SIM = null;
  ["#sim-budget", "#sim-deadline", "#sim-qty", "#sim-tax"].forEach((s) => { $(s).oninput = queueSim; });
  $("#sim-cons").oninput = $("#sim-cons").onchange = queueSim;
}

function scenario() {
  const r = MISSION.requirements, sc = {};
  const num = (s) => ($(s).value === "" ? null : parseFloat($(s).value));
  const b = num("#sim-budget"), d = num("#sim-deadline"), q = num("#sim-qty"), t = num("#sim-tax");
  if (b !== (r.budget_total ?? null)) sc.budget_total = b;
  if (d !== (r.deadline_days ?? null)) sc.deadline_days = d == null ? null : Math.round(d);
  if (q && q !== r.quantity) sc.quantity = Math.round(q);
  if (t !== (r.tax_rate_pct ?? null)) sc.tax_rate_pct = t;
  const cons = r.constraints.map((c, i) => {
    const row = $(`#sim-cons [data-i="${i}"]`), v = row.querySelector("[data-k=value]").value;
    return { ...c, value: v !== "" && !isNaN(v) ? parseFloat(v) : v, hard: row.querySelector("[data-k=hard]").checked };
  });
  if (cons.some((c, i) => c.value !== r.constraints[i].value || c.hard !== r.constraints[i].hard)) sc.constraints = cons;
  const w = sliderWeights();
  if (!isDefaultWeights(w) && Object.values(w).some(Boolean)) sc.weights = w;
  return sc;
}

function queueSim() { clearTimeout(simTimer); simTimer = setTimeout(runSim, 250); }

async function runSim() {
  const sc = scenario(), seq = ++simSeq;
  if (!Object.keys(sc).length) { SIM = null; }
  else {
    $("#sim-out").classList.add("busy");
    try {
      const res = await api(`/api/missions/${MISSION.id}/simulate`, { method: "POST", body: JSON.stringify(sc) });
      if (seq !== simSeq) return;  // a newer change already landed
      SIM = { ...res, input: sc };
    } catch (e) { $("#sim-out").classList.remove("busy"); $("#sim-out").innerHTML = `<p class="err">Couldn't run the scenario: ${esc(e.message)}</p>`; return; }
  }
  $("#sim-out").classList.remove("busy");
  if (!MISSION.decision && SELECTED && !viewOffers().find((o) => o.id === SELECTED)?.rank) SELECTED = recId();
  renderSim(); renderTrace(); renderFindings(); refreshTable(); renderDecision();
}

function renderSim() {
  const out = $("#sim-out"), rep = MISSION.report;
  if (!SIM) {
    const best = offerById(rep.recommended_offer_id);
    out.innerHTML = `<div class="sim-idle"><b>Mission requirements.</b> ${best ? `${esc(best.vendor)} · ${esc(trunc(best.name, 60))} is the pick at ${money(landedOf(best))} landed.` : "No eligible offer."}
      <p class="muted">Try: change the deadline, budget, quantity or a must-have. The ranking below and the decision trace update, and every change is explained.</p></div>`;
    return;
  }
  const a = SIM.after, b = SIM.before;
  const pickBtn = a && !MISSION.decision && a.offer_id !== SELECTED ? `<button class="btn btn-primary btn-sm" data-act="choose" data-oid="${esc(a.offer_id)}">Choose this offer</button>` : "";
  out.innerHTML = `<div class="sim-changes">${SIM.changes.map((c) => `<span class="chg">${esc(c)}</span>`).join("")}</div>
    <div class="sim-pick ${SIM.recommendation_changed ? "changed" : ""}">
      <div><small>Mission pick</small><b>${b ? esc(b.vendor) : "none"}</b><span>${b ? money(b.landed_total) : ""}</span></div>
      <span class="arrow">→</span>
      <div><small>${SIM.recommendation_changed ? "Scenario pick (changed)" : "Scenario pick"}</small><b>${a ? esc(a.vendor) : "none eligible"}</b><span>${a ? money(a.landed_total) + (a.provisional ? " · provisional" : "") : ""}</span></div>
      ${pickBtn}</div>
    <ul class="sim-why">${SIM.explanation.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>
    ${SIM.changed_offers.length ? `<details class="sim-moves" ${SIM.changed_offers.length <= 4 ? "open" : ""}><summary>${SIM.changed_offers.length} offer(s) changed</summary><ul>${SIM.changed_offers.slice(0, 10).map((r) => `<li><b>${esc(r.vendor)}</b> ${r.before.rank ? "#" + r.before.rank : "excluded"} → ${r.after.rank ? "#" + r.after.rank : "excluded"}${r.reasons.length ? ": " + r.reasons.map(esc).join("; ") : ""}</li>`).join("")}</ul></details>` : ""}
    ${!MISSION.decision && a ? `<p class="small muted">Approving while this scenario is active records these changes on the PO and in the audit trail.</p>` : ""}`;
  out.onclick = (ev) => { const btn = ev.target.closest("[data-act=choose]"); if (btn) choose(btn.dataset.oid); };
}

/* ── compare ──────────────────────────────────────────── */
function updateCompareUi() {
  const n = COMPARE.size, b = $("#btn-compare");
  b.disabled = n < 2;
  b.textContent = n ? `Compare ${n}` : "Compare";
  b.title = n < 2 ? "Tick 2 or 3 offers in the table" : "";
  document.querySelectorAll("#offers [data-cmp]").forEach((i) => { i.disabled = !i.checked && n >= 3; });
}

function openCompare() {
  const q = SIM?.requirements?.quantity || MISSION.requirements.quantity, rid = recId();
  const list = viewOffers().filter((o) => COMPARE.has(o.id));
  const attrs = [...new Set(list.flatMap((o) => Object.keys(o.attributes || {})))].filter((a) => a !== "warranty_onsite");
  const pct = (x) => (x == null ? "—" : Math.round(x * 100) + "%");
  const RISK_N = { low: 0, moderate: 1, elevated: 2, high: 3 };
  // [label, render, numeric value for "best" highlighting, which direction wins]
  const rows = [
    ["Rank · score", (o) => (o.rank ? `#${o.rank} · ${Math.round(o.score)}` : "Excluded"), (o) => (o.rank ? o.score : null), "max"],
    ["Unit price", (o) => money(o.unit_price, 2), (o) => o.unit_price, "min"],
    [`Landed total for ${q}`, (o) => money(o.landed?.total) + " " + confChip(o.landed?.confidence), (o) => o.landed?.total, "min"],
    ["Not stated", (o) => esc((o.landed?.unknowns || []).join(", ") || "—")],
    ["Lifetime cost", (o) => money(o.tco), (o) => o.tco, "min"],
    ["vs fair price", (o) => (o.price_delta_pct == null ? "—" : `${o.price_delta_pct > 0 ? "+" : ""}${Math.round(o.price_delta_pct * 100)}%`), (o) => o.price_delta_pct, "min"],
    ["Requirement fit", (o) => pct(o.fit_score), (o) => o.fit_score, "max"],
    ["Supplier risk", (o) => levelChip(o.risk?.level), (o) => RISK_N[o.risk?.level] ?? null, "min"],
    ["Claims verified", (o) => `${(o.claims || []).filter((c) => c.status === "verified").length} / ${(o.claims || []).length}`, (o) => (o.claims || []).filter((c) => c.status === "verified").length, "max"],
    ["Findings", (o) => esc((o.flags || []).filter((f) => f.severity !== "info" && f.severity !== "low").map((f) => f.title).join("; ") || "None")],
    ["Warranty", (o) => esc(o.warranty.raw || "not stated")],
    ["Stock", (o) => esc(o.stock || "—")],
    ["Seller · condition", (o) => esc(`${o.seller || o.vendor} · ${o.condition}`)],
    ...attrs.map((a) => [LABELS[a] || a, (o) => esc(o.attributes[a] ?? "—"),
      BETTER[a] ? (o) => (typeof o.attributes[a] === "number" ? o.attributes[a] : null) : null, BETTER[a]]),
    ["Failed must-haves", (o) => (o.failed_constraints.length ? o.failed_constraints.map(esc).join("<br>") : "None")],
  ];
  const head = `<tr><th></th>${list.map((o) => `<th><div class="pcell">${tile(o.vendor, o.url)}<div>
    <a class="pname" href="${safeUrl(o.url)}" target="_blank" rel="noopener noreferrer">${esc(o.name)}</a>
    <div class="pvendor">${esc(o.vendor)}${o.id === rid ? ' · <span class="flag rec">Recommended</span>' : ""}</div></div></div></th>`).join("")}</tr>`;
  const body = rows.map(([label, render, val, dir]) => {
    const nums = val ? list.map(val) : [];
    const valid = nums.filter((x) => x != null && isFinite(x));
    const win = valid.length >= 2 && new Set(valid).size > 1 ? (dir === "max" ? Math.max(...valid) : Math.min(...valid)) : null;
    return `<tr><th scope="row">${esc(label)}</th>${list.map((o, i) => `<td class="${win != null && nums[i] === win ? "top" : ""}">${render(o)}</td>`).join("")}</tr>`;
  }).join("");
  const acts = MISSION.decision ? "" : `<tr><th></th>${list.map((o) => `<td>${o.rank && o.id !== SELECTED
    ? `<button class="btn btn-primary btn-sm" data-act="choose" data-oid="${esc(o.id)}">Choose this offer</button>`
    : o.id === SELECTED ? '<span class="act-note">✓ Selected</span>' : ""}</td>`).join("")}</tr>`;
  $("#cmp-body").innerHTML = `<table class="cmp"><thead>${head}</thead><tbody>${body}${acts}</tbody></table><p class="hint">Green marks the best value in each row.${SIM ? " Values reflect the active scenario." : ""}</p>`;
  $("#cmp-body").onclick = (ev) => { const b = ev.target.closest("[data-act=choose]"); if (b) { choose(b.dataset.oid); $("#dlg-compare").close(); } };
  $("#dlg-compare").showModal();
}

/* ── quote request draft ──────────────────────────────── */
async function openQuote(offerId) {
  try {
    const q = await api(`/api/missions/${MISSION.id}/offers/${encodeURIComponent(offerId)}/quote-request`);
    $("#q-title").textContent = `Quote request · ${q.vendor}`;
    $("#q-subject").value = q.subject; $("#q-body").value = q.body;
    $("#q-copy").textContent = "Copy message";
    updateMailto();
    $("#dlg-quote").showModal();
  } catch (e) { toast("Could not draft the quote request: " + e.message); }
}
function updateMailto() {
  $("#q-mail").href = `mailto:?subject=${encodeURIComponent($("#q-subject").value)}&body=${encodeURIComponent($("#q-body").value)}`;
}
async function copyQuote() {
  const text = `Subject: ${$("#q-subject").value}\n\n${$("#q-body").value}`;
  try { await navigator.clipboard.writeText(text); $("#q-copy").textContent = "Copied ✓"; }
  catch { $("#q-body").select(); $("#q-copy").textContent = "Press ⌘C to copy"; }
}

/* ── sign-off ─────────────────────────────────────────── */
function choose(id) {
  const o = viewOffers().find((x) => x.id === id);
  if (MISSION.decision || !o?.rank) return;
  SELECTED = id;
  refreshTable(); renderSim(); renderDecision();
}

function renderDecision() {
  const d = MISSION.decision, bar = $("#decision"), rep = MISSION.report, req = MISSION.requirements;
  const po = `/missions/${MISSION.id}/po`;
  if (d) {
    setStage(6, "Complete");
    const o = offerById(d.offer_id), what = o ? `${req.quantity} × ${trunc(o.name, 56)} · ${money(landedOf(o))}` : "";
    $("#po-link").href = po;
    bar.className = "decide " + (d.decision === "approve" ? "approved" : "rejected");
    const sc = d.scenario?.changes?.length ? `<span class="why">Under revised requirements: ${esc(d.scenario.changes.join("; "))}</span>` : "";
    bar.innerHTML = d.decision === "approve"
      ? `<div class="what"><small style="color:#fff">Approved${d.override ? " · buyer override" : ""}</small><span>Signed off by ${esc(d.approver || "buyer")} · ${esc(what)}</span>${d.override && d.note ? `<span class="why">Reason: ${esc(d.note)}</span>` : ""}${sc}</div><a class="btn btn-gold" target="_blank" href="${po}">Open purchase order →</a>`
      : `<div class="what"><small>Rejected</small><span>No purchase order issued.${d.note ? " " + esc(d.note) : ""}</span></div>`;
    return;
  }
  const o = SELECTED && viewOffers().find((x) => x.id === SELECTED);
  const override = !!o && SELECTED !== rep.recommended_offer_id && SELECTED !== SIM?.after?.offer_id;
  const approver = $("#approver")?.value || "", note = $("#note")?.value || "";
  $("#po-link").href = po + (SELECTED !== rep.recommended_offer_id ? `?offer=${encodeURIComponent(SELECTED)}` : "");
  bar.className = "decide" + (override ? " override" : "") + (SIM ? " scenario" : "");
  bar.innerHTML = `
    <div class="what"><small>${override ? "Buyer override · not the recommendation" : SIM ? "Ready for sign-off · under scenario" : "Ready for sign-off"}</small>
      <span>${o ? esc(`${SIM?.requirements?.quantity || req.quantity} × ${trunc(o.name, 60)} · ${money(landedOf(o))}`) : "Nothing to approve"}</span>
      ${override ? `<button class="btn btn-link" id="btn-unpick">Back to recommendation</button>` : ""}</div>
    ${override ? `<input id="note" placeholder="Why this offer? Required, printed on the PO" aria-label="Reason for choosing this offer (required)" maxlength="1000" required>` : ""}
    <input id="approver" placeholder="Approver name" aria-label="Approver name" maxlength="120">
    <button id="btn-reject" class="btn btn-ghost">Reject</button>
    <button id="btn-approve" class="btn btn-gold" ${o ? "" : "disabled"}>Approve purchase</button>`;
  $("#approver").value = approver;
  if ($("#note")) { $("#note").value = note; $("#note").oninput = () => $("#note").classList.remove("need"); }
  $("#btn-approve").onclick = () => decide("approve");
  $("#btn-reject").onclick = () => decide("reject");
  $("#btn-unpick")?.addEventListener("click", () => choose(recId() || rep.recommended_offer_id));
}

async function decide(decision) {
  const rep = MISSION.report, override = SELECTED !== rep.recommended_offer_id && SELECTED !== SIM?.after?.offer_id;
  const note = $("#note")?.value.trim() || "";
  const needReason = () => {
    const el = $("#note"); el.classList.add("need"); el.placeholder = `A reason is required (at least ${INFO.min_override_reason ?? 3} characters)`; el.focus();
  };
  if (decision === "approve" && override && note.length < (INFO.min_override_reason ?? 3)) { needReason(); return; }
  const btn = decision === "approve" ? $("#btn-approve") : $("#btn-reject"); btn.disabled = true;
  try {
    const sc = SIM ? { ...SIM.input } : null;
    MISSION = await api(`/api/missions/${MISSION.id}/decision`, { method: "POST", body: JSON.stringify({
      decision, offer_id: SELECTED || null, approver: $("#approver").value, note,
      weights: sc?.weights || null, scenario: sc && Object.keys(sc).some((k) => k !== "weights") ? sc : null,
    }) });
    renderDecision(); refreshTable(); renderSim(); renderOutcome(); loadAudit(); loadHistory();
    toast(decision === "approve" ? "Approved. The purchase order and decision report are ready; nothing was sent." : "Rejected and recorded.", "ok");
  } catch (e) {
    btn.disabled = false;
    if (/needs a reason/.test(e.message) && $("#note")) { needReason(); return; }  // server-side rule, same as above
    toast("Could not record decision: " + e.message);
  }
}

/* ── outcome & audit ──────────────────────────────────── */
async function renderOutcome() {
  const box = $("#outcome"), d = MISSION.decision;
  if (!d || d.decision !== "approve") {
    box.innerHTML = `<p class="muted">After approval, record what actually happened (delivery date, price paid). Outcomes build each supplier's track record in procurement memory.</p>`;
    return;
  }
  let rows = [];
  try { rows = await api(`/api/missions/${MISSION.id}/outcomes`); } catch { /* shown as none */ }
  box.innerHTML = (rows.length ? `<ul class="outs">${rows.map((r) => `<li><b>${esc(r.delivered_on || "date not given")}</b> · ${r.on_time == null ? "timeliness not recorded" : r.on_time ? "on time" : "late"}${r.actual_unit_price ? ` · paid ${money(r.actual_unit_price, 2)}/unit` : ""}${r.notes ? ` · ${esc(r.notes)}` : ""} <span class="muted">(${esc(r.recorded_by || "buyer")}, ${esc(String(r.at).slice(0, 10))})</span></li>`).join("")}</ul>` : "")
    + `<form id="out-form" class="outform"><label class="field">Delivered on<input type="date" name="delivered_on"></label>
      <label class="field">On time?<select name="on_time"><option value="">Not sure</option><option value="true">Yes</option><option value="false">No</option></select></label>
      <label class="field">Actual unit price<input type="number" step="0.01" min="0" name="actual_unit_price" placeholder="${esc(money(unitOf(offerById(d.offer_id)), 2))}"></label>
      <label class="field wide">Notes<input name="notes" maxlength="1000" placeholder="e.g. 2 units arrived damaged, replaced in 5 days"></label>
      <button class="btn btn-primary btn-sm">Record outcome</button></form>`;
  $("#out-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target), body = { notes: f.get("notes") || "", recorded_by: $("#approver")?.value || d.approver || "" };
    if (f.get("delivered_on")) body.delivered_on = f.get("delivered_on");
    if (f.get("on_time")) body.on_time = f.get("on_time") === "true";
    if (f.get("actual_unit_price")) body.actual_unit_price = parseFloat(f.get("actual_unit_price"));
    try { await api(`/api/missions/${MISSION.id}/outcome`, { method: "POST", body: JSON.stringify(body) }); toast("Outcome recorded.", "ok"); renderOutcome(); loadAudit(); loadHistory(); }
    catch (e) { toast("Could not record the outcome: " + e.message); }
  };
}

const AUDIT_LABEL = {
  "mission.created": "Mission created", "sourcing.completed": "Sourcing agent finished", "sourcing.failed": "Sourcing agent failed",
  "verification.completed": "Claims verified against product pages", "cost.landed": "Landed cost computed", "risk.assessed": "Risk radar and findings",
  "ranking.completed": "Offers ranked", "decision.recommended": "Decision policy applied", "memo.drafted": "Memo drafted",
  "decision.approve": "Approved", "decision.reject": "Rejected", "outcome.recorded": "Outcome recorded", "mission.failed": "Mission failed",
};
function auditSummary(e) {
  const d = e.data || {};
  switch (e.action) {
    case "mission.created": return `${d.vendors?.length || 0} vendors · ${d.mode} · planner ${d.planner}`;
    case "sourcing.completed": return `${d.vendor}: ${d.offers} offers (${d.provenance?.kind})`;
    case "verification.completed": return `${d.summary?.pages_fetched} pages · ${Object.entries(d.summary?.claims || {}).map(([k, v]) => `${v} ${k}`).join(", ")}`;
    case "risk.assessed": return `${(d.findings || []).length} medium/high findings`;
    case "decision.recommended": return d.recommended_offer_id ? `recommended ${d.recommended_offer_id}${d.provisional ? " (provisional)" : ""}, skipped ${(d.trace || []).filter((t) => t.outcome === "skipped").length}` : "no eligible offer";
    case "memo.drafted": return `by ${d.by}${d.grounding ? (d.grounding.ok ? ", figures grounded" : ", rejected: unsupported figures") : ""}`;
    case "decision.approve": case "decision.reject": return `${d.vendor} · ${trunc(d.name || "", 40)}${d.override ? " · override: " + d.note : ""}${d.scenario?.length ? " · scenario: " + d.scenario.join("; ") : ""}`;
    case "outcome.recorded": return `${d.delivered_on || "no date"} · ${d.on_time == null ? "" : d.on_time ? "on time" : "late"}`;
    default: return "";
  }
}
async function loadAudit() {
  try {
    const a = await api(`/api/missions/${MISSION.id}/audit`);
    $("#audit-meta").innerHTML = a.entries.length ? `${a.entries.length} hash-chained entries · <span class="cc ${a.chain.valid ? "confirmed" : "unknown"}">${a.chain.valid ? "chain intact" : "chain broken at #" + a.chain.broken_at}</span>` : "No audit entries (mission predates the audit trail)";
    $("#audit").innerHTML = a.entries.map((e) => `<li><span class="t">${esc(e.at.slice(11, 19))}</span><div><b>${esc(AUDIT_LABEL[e.action] || e.action)}</b> <span class="muted">${esc(e.actor)}</span><div class="small">${esc(auditSummary(e))}</div><code title="SHA-256 of this entry">${esc(e.hash.slice(0, 12))}…</code></div></li>`).join("");
  } catch (e) { $("#audit-meta").textContent = "Couldn't load the audit trail: " + e.message; }
}

const show = (s) => $(s).classList.remove("hidden");
const hide = (s) => $(s).classList.add("hidden");
init();
