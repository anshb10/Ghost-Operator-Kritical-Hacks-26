# 👻 Ghost Operator: an autonomous procurement employee

Give it an **objective**, not instructions:

> "I need 50 laptops for my new office. 16GB RAM, Windows 11 Pro, ideally 3-year onsite warranty. Budget $75k."

Ghost Operator plans the mission, **finds vendors on the live web**, sends **web agents** to their sites in parallel, and then refuses to take their word for it: every important claim is **re-checked on the vendor's own product page** and recorded as *verified, contradicted, inferred or unknown*, with the snippet, URL and retrieval time. It prices the order **to the door** (promo caps, discounts, delivery, fees, GST/sales tax), scores each supplier on an **explainable six-dimension risk radar**, and applies a **fixed evidence policy** to pick what to buy. A human approves; nothing is ever bought or sent automatically, and every step lands in a **hash-chained audit trail**.

```
Objective ──► Planner (Claude, or rule-based fallback) ──► editable Requirements (INR/USD, budget, deadline, must-haves)
                                          │
                         ① TinyFish Search API: discover more stores for this item
                                          │
   ② TinyFish Web Agents, one per vendor, in parallel ──► structured offers (provenance: live / recorded / synthetic)
                                          ▼
   ③ TinyFish Fetch API re-reads every cited page ──► claims ledger (verified · contradicted · inferred · unknown)
                                                  └─► adversarial checks: unit-basis tricks, promo caps, hidden charges,
                                                      warranty conflicts, stock, lead time, injected instructions
                                          ▼
   engine.analyze (shared by the mission AND the scenario simulator):
      landed cost ──► requirement findings ──► constraint fit ──► risk radar ──► TCO + weighted score ──► decision policy
                                          ▼
   Recommendation + decision trace ──► human approve / override (reason) / approve under a scenario ──► PO, report, audit
```

### What changed in v2 (evidence-first upgrade)

| Capability | Where | What it does |
|---|---|---|
| Claims ledger | [`backend/evidence.py`](backend/evidence.py) | One status per critical claim; nothing the agent said is shown as fact unless the page says it |
| Adversarial checks | [`backend/adversarial.py`](backend/adversarial.py) | Reads commercial small print off the page and raises evidence-backed findings |
| Landed cost | [`backend/landed_cost.py`](backend/landed_cost.py) | Decimal, line-by-line; each line *confirmed / estimated / unknown*; included tax never added twice |
| Supplier risk radar | [`backend/risk.py`](backend/risk.py) | Evidence, spec, delivery, warranty, price transparency, integrity; unknown ≠ risky |
| Decision agent | [`backend/decision.py`](backend/decision.py) | Deterministic policy over the ranking with a per-offer trace; no LLM, no vote |
| Shared engine | [`backend/engine.py`](backend/engine.py) | The requirement-dependent analysis, used by missions and the simulator alike |
| Scenario simulator | [`backend/simulate.py`](backend/simulate.py) | Change budget/deadline/quantity/tax/specs/weights; explains every rank and pick change |
| Procurement memory | [`backend/memory.py`](backend/memory.py) | SQLite: missions, deduplicated price observations, decisions, outcomes, vendor track record |
| Audit trail | [`backend/audit.py`](backend/audit.py) | Append-only SHA-256 chain per mission; tampering is detected |
| Memo grounding | [`backend/grounding.py`](backend/grounding.py) | A Claude memo citing any figure the cost engine didn't produce is discarded |
| India workflow | [`config/domains/laptops_in.json`](config/domains/laptops_in.json) | INR, lakh/crore budgets, 18% GST (HSN 8471), CGST/SGST vs IGST note, GSTIN-aware RFQ and PO |

## Who it's for, and why it matters

**Who:** office managers, lab managers, IT leads and founders at companies of 10 to 500 people, plus schools and clinics. They buy laptops, gloves and other equipment in bulk a few times a year, and none of them has a procurement team.

**The problem today:**
- **It eats time.** In a Walmart Business survey of 500+ decision-makers at 10–500-person companies, 37% said they spend 4 to 7 hours a week on routine ordering alone ([PYMNTS](https://www.pymnts.com/?p=3882849)).
- **Most have no tooling.** Nearly two-thirds of finance leaders in a CFO Connect survey had no dedicated procurement tool ([Spendesk](https://www.spendesk.com/blog/procurement-software-adoption/)).
- **Each order costs money to process.** APQC puts the median cost of processing one purchase order at about $42, with a range of $14 to $54+ ([APQC](https://www.apqc.org/resource-library/resource/how-efficient-your-procurement-process-benchmarks-reveal-wide-performance)).
- **The web is full of traps for a one-time buyer.** Promo prices are capped per order, configurators show a different spec at the advertised price, warranties turn out to be mail-in, and stock is backordered. A busy person comparing tabs misses these.

**What changes with Ghost Operator.** The buyer types one sentence, and within minutes (live runs take 3 to 12 minutes; replay takes under a minute) gets a ranked shortlist in which every price was re-checked on the vendor's own page. They also get a memo that explains the trade-offs, a ready purchase order and, where needed, a drafted quote-request email.

These are real catches from the recorded runs (see [What verification caught](#what-verification-caught-on-real-pages-2026-10-16)):

| Trap | What a tab-by-tab buyer would have done | What Ghost Operator did |
|---|---|---|
| Lenovo ThinkBook 14x at **$849**, "Max 5 units per order" | Budgeted 50 × $849 = **$42,450** | Re-costed the other 45 units at list: expected **$61,975**, so a **$19,525 (46%) budget gap** was caught before approval. It ranked the offer "ask for a quote" and drafted that email. |
| Dell Pro 16 at $1,255 advertised with 16 GB | Bought 50 machines with **8 GB** RAM | The page showed 8 GB at that price, so the offer was corrected and excluded. |
| Uline gloves priced "per 1,000" by the agent | Ordered 10× the intended quantity, or 10× short | The page sells cartons of 100, so the offer was flagged as a mismatch. |
| Same ThinkPad E14 at Lenovo and CDW | Picked whichever tab was open | Lenovo direct was 3% cheaper, with a better CPU and 2× the storage. |

**Why it's realistic:**
- **A human stays in charge.** Nothing is ever bought or sent automatically. The buyer can override the pick (with a reason that's recorded on the PO), tune the scoring weights and export everything to CSV.
- **It doesn't depend on any one store.** It works on vendors' public websites, so there are no supplier integrations to sign. New categories are a JSON file in `config/domains/`.
- **It works without a network.** Replay mode runs on recorded real TinyFish runs, so a demo never depends on the network.
- **Known limits:**
  - Live runs take 3 to 12 minutes.
  - The recorded vendors are US stores.
  - A listed price isn't a contract, which is why quotes are drafted for anything uncertain.

## How TinyFish is used

TinyFish is the product's eyes and hands on the live web. All three APIs do work that the product can't do without them:

| TinyFish API | Where | What it does in Ghost Operator | Why it's needed |
|---|---|---|---|
| **Search API** (`GET api.search.tinyfish.ai`) | [`backend/discovery.py`](backend/discovery.py) | Turns the requirements into a shopping query, then filters results down to real stores (forums, blogs, social and review sites dropped; ranked by commerce signals). The buyer ticks which ones to dispatch agents to. | A small business doesn't know every supplier. Search turns "50 laptops" into a vendor shortlist, e.g. it surfaced **Office Depot** and **Eagle Protect** (gloves), which weren't in the config. |
| **Web Agent** (`POST agent.tinyfish.ai/v1/automation/run-sse`) | [`backend/tinyfish.py`](backend/tinyfish.py), [`backend/pipeline.py`](backend/pipeline.py) | One goal-driven agent per vendor, run in parallel, navigates listings and configurators and returns structured JSON (price, specs, warranty, seller, stock, URL). Progress steps and the live browser stream are shown in the UI. | Vendor sites are dynamic: filters, configurators, cookie walls. A plain scraper can't do "find business laptops with ≥16 GB and read the warranty line". |
| **Fetch API** (`POST api.fetch.tinyfish.ai`) | [`backend/verify.py`](backend/verify.py) | Re-reads every product page the agents cited (≤10 per call, in parallel) and checks each claim: price present on page, RAM/storage/thickness *at that price*, warranty line, stock, and **"Max N units per order"** promo caps. The page wins: specs are corrected, capped promos are re-costed at list price for the rest of the quantity. | Agents can misread a configurator. For a 50-unit purchase the price and specs must come from the source, not from one agent's read. |

### What verification caught on real pages (2026-10-16)

- **Dell Pro 16 (AMD), $1,255:** the agent reported 16 GB RAM, but Dell's page shows the $1,255 config has **8 GB**. The offer was corrected and excluded by the 16 GB must-have.
- **Lenovo ThinkBook 14x, $949 and ThinkPad E14, $1,259:** the page says **"Promo price: Max 5 units per order"**. For 50 units the expected average is **$1,386** and **$1,655**. The $849 sibling's page didn't render, so its cap was inferred from the same-model listing (labelled as inferred).
- **Dell Pro 16 Plus, $1,826:** the price changed; the page now shows $1,836 / $1,868. Two Dell configs are **sold out**.
- **CDW Dell Pro 14 Plus:** price verified, but **backordered**, so it's excluded from the recommendation for a 50-unit order.
- **Uline gloves:** the agent priced 1,000 gloves as $150, while the page prices a **carton of 100** at $17/$15. Flagged as a mismatch.
- **Office Depot (discovered by Search):** all 4 offers verified (price, RAM, storage, warranty).

Net effect: the naive cheapest pick ($849 ThinkBook) becomes "ask Lenovo for a 50-unit quote", and the recommendation goes to the best offer whose **price is confirmed on the vendor's page and that is in stock**.

## Revenue model

**Who pays:** the 10–500-person company, per mission (one buying objective, e.g. "50 laptops"). We sell to the buyer only. We take **no vendor commissions or paid placement**, because a ranking that can be bought is worthless to the buyer.

### What one mission costs us (measured on the recorded runs)

| Cost | Rate | Laptops (5 vendors) | Gloves (2 vendors) |
|---|---|---|---|
| TinyFish Web Agent | $0.016 / step ([pricing](https://tinyfish.ai/pricing)) | 124 steps = **$1.98** | 131 steps = **$2.10** |
| TinyFish Search + Fetch | free on every plan ([pricing](https://tinyfish.ai/pricing)) | $0 | $0 |
| Claude (planner + memo) | Opus 5.5 at $4 / $20 per M input/output tokens ([pricing](https://www.eesel.ai/blog/claude-opus-5-5-pricing)) | ≈ $0.20 (est.) | ≈ $0.20 (est.) |
| **Total** | | **≈ $2.20** | **≈ $2.30** |

We budget **$2.50 per mission** to cover retries and the occasional long agent run (Uline alone took 101 steps).

### Pricing

| Plan | Price | Included | Our cost at $2.50 / mission | Gross margin |
|---|---|---|---|---|
| **Free** | $0 | 2 live missions / month, unlimited replay | $5 | acquisition cost |
| **Pay as you go** | **$19 / mission** | 1 mission + PO + quote drafts | $2.50 | **87%** |
| **Team** | **$149 / month** | 20 missions ($7.45 each), extra at $9, shared mission history | $50 | **66%** |
| **Business** | **$499 / month** | 80 missions, CSV export, custom categories, priority support | $200 | **60%** |

These margins are a floor:
- **Unused missions:** they cost us nothing.
- **Batch API:** the memo can run on Anthropic's Batch API at half price.
- **Repeat visits:** they can reuse a vendor's known navigation, which needs fewer agent steps.
- **Volume rates:** TinyFish offers enterprise contract pricing at volume.

### Why these prices are justified

- **It costs less than the order it replaces.** At $19, a mission costs less than half of APQC's ~$42 median cost to process one purchase order, and it also does the sourcing and verification that the $42 doesn't include.
- **One catch pays for a year.** The capped-promo catch above was a $19,525 budget error on one order. The 3% same-model price gap is about $2,000 on a $69,000 laptop order. Either one covers more than a year of the Team plan ($1,788).
- **It undercuts existing tools and does something they don't.** Spend-management tools like Procurify are quote-based. Third-party listings start around $23 per user per month ([TrustRadius](https://trustradius.com/products/procurify/pricing)), with small-business deployments reported at around $1,000 per month ([ERP Research](https://erpresearch.com/erp-add-ons/procurement/procurify/pricing)). Those tools route approvals and POs for suppliers you already know. Ghost Operator does the step before that: it finds and verifies the offer on the live web. It complements them (CSV export), and Team is priced below them.

### Go-to-market

1. **Free tier and replay demo.** Anyone can see a full mission in under a minute with no signup.
2. **Templates for the three most common bulk buys.** These are IT hardware, lab and medical consumables, and office supplies. Each is one `config/domains/*.json` file.
3. **Channels that already serve these buyers.** These are IT managed-service providers, lab-supply consultants and coworking operators.
4. **India next.** The INR/GST workflow is built (`laptops_in`), with Lenovo/Dell/HP India configured for live mode; those live configs have **not yet been verified with a real run**, so the demo uses clearly labelled synthetic fixtures.

**Illustrative target, not a forecast:** 500 companies on Team is $74,500 a month, or about $0.9M ARR, at a ~66% gross margin.

## Run it

```bash
git clone https://github.com/anshb10/Ghost-Operator.git && cd Ghost-Operator
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # requirements.txt + pytest
cp .env.example .env          # optional: TINYFISH_API_KEY for live mode, ANTHROPIC_API_KEY for the Claude planner/memo
.venv/bin/uvicorn backend.app:app --port 8077
```

Open http://localhost:8077. No keys are needed: without them everything runs in replay on the recorded fixtures.

To demo offline even when a TinyFish key is in `.env` (live missions spend TinyFish credits, roughly $2 each):

```bash
GHOST_MODE=replay .venv/bin/uvicorn backend.app:app --port 8077
```

Requires Python 3.11+ (developed on 3.13). Mission files and the procurement-memory database are written to `data/` (git-ignored; override with `GHOST_DATA_DIR`).

| Mode | When | What happens |
|---|---|---|
| **live** | `TINYFISH_API_KEY` set | Real TinyFish Search, Web Agent and Fetch calls, with the live browser view embedded per vendor. Every agent run, search and page fetch is recorded to `data/recorded/` and can be replayed later. |
| **replay** | no key, or `GHOST_MODE=replay` | Plays back recorded TinyFish responses (`fixtures/replay/`: real runs, searches and page fetches from 2026-10-16) at `REPLAY_SPEED`× so the demo never depends on venue Wi-Fi. |
| **replay on a live server** | "Recordings & fixtures" toggle | A key-holding server can still run a single mission in replay (`mode: "replay"` on `/api/plan` and `/api/missions`), e.g. for the India fixture demo. |

Every offer carries its provenance (`live`, `recorded` or `synthetic`), and the brief, PO and decision report say so.

Without Claude credentials, the planner and memo fall back to rule-based versions, so the app always works.

## Working with a decision brief

| Feature | What it does |
|---|---|
| **Mission history** | Every saved mission is listed with its landed total; briefs are linkable (`/#m=<id>`). The header shows procurement-memory stats. |
| **Decision trace** | Why the pick won and why each higher-scoring offer was skipped (`report.decision.trace`). |
| **Misleading-offer findings** | High/medium findings across all offers, each with the page snippet it rests on. |
| **Offer details** | Tabs: *Evidence* (claims ledger), *Landed cost*, *Risk radar*, *Findings*, *History* (comparable configurations seen before + your record with the vendor), *Raw extraction*. |
| **Scenario simulator** | `POST /api/missions/{id}/simulate` with any of `budget_total`, `deadline_days`, `quantity`, `tax_rate_pct`, `constraints`, `weights`. Same offers, same engine; returns the new pick, an explanation and per-offer reasons. Nothing is saved. |
| **Approve** | As recommended; as an override (reason required, printed on the PO); or **under a scenario** (the changed requirements are validated server-side and printed on the PO). Decisions are final (a second one gets 409). |
| **Outcome** | After approval, record delivery date, on-time and price paid (`POST /api/missions/{id}/outcome`); feeds the vendor's track record. |
| **Exports** | Decision report (`/missions/{id}/report`, incl. comparative statement of quotations), purchase order (`/missions/{id}/po`), CSV (`/api/missions/{id}/offers.csv`), audit log (`/api/missions/{id}/audit.json`), RFQ draft (`/api/missions/{id}/offers/{offer_id}/quote-request`, GSTIN-aware for INR). Nothing is sent anywhere. |

## The AI/ML pieces (`backend/ml/`, `backend/scoring.py`)

These run once per mission and feed the engine above.

| Module | Technique | Why it matters |
|---|---|---|
| `normalize.py` | Spec parsers (RAM/storage/CPU tier/warranty terms/condition) | "16 GB LPDDR5X", "16GB" and "16 GB DDR5" all become `ram_gb=16`. "One Year Onsite" becomes `{years: 1, service: onsite}`. |
| `matching.py` | TF-IDF char n-grams + cosine; complete-linkage clustering with spec compatibility; model-family matching | Finds the same product or model line across vendors, e.g. *ThinkPad E14 Gen 7 is 3% cheaper at Lenovo direct than CDW **and** has a better CPU and 2× the storage* (real finding). |
| `pricing.py` | Leave-one-out ridge regression on log(price) from specs + Isolation Forest | Predicts a "fair price" for each listing. Flags **deals** (first-party, new) vs **suspicious** cheap listings (refurb/third-party) vs **overpriced**. |
| `warranty_risk.py` | TF-IDF + logistic regression clause classifier (`fixtures/warranty_clauses.json`) + structured risk features | Flags mail-in/depot service, short terms, third-party sellers and refurb. Explains *which words* triggered it. |
| `scoring.py` | Hard/soft constraint fit (budget checked on the **landed** total, deadline as a must-have), lifetime cost (landed + expected out-of-warranty repairs + downtime), weighted score whose five terms are stored per offer (`score_parts`) | Every rank change can be decomposed into fit, lifetime cost, supplier risk, price sanity and source trust. |

## Adding a new domain

Drop a JSON file in `config/domains/` with a vendor list, the fields to extract, attribute parsers, default constraints and a goal template. `lab_supplies.json` (nitrile gloves) is a worked example. The planner and UI pick it up automatically.

## Recorded fixtures (real TinyFish runs, 2026-10-16)

| Vendor | Steps | Duration | Notable |
|---|---|---|---|
| Dell | 28 | 4m21s | Base configs ship with **1-yr mail-in** warranty |
| Lenovo | 20 | 3m21s | ThinkBook 14x promo at $849 (flagged as a deal) |
| CDW | 24 | 4m46s | Resells Lenovo/Dell; backorder status captured |
| Uline (gloves) | 101 | 11m48s | ASTM D6319 certs; agent priced per 1,000 while page prices per 100 (caught by Fetch) |
| Office Depot (found by Search) | 12 | 2m12s | 4 offers, all verified by Fetch |
| Eagle Protect (gloves, found by Search) | 30 | 3m48s | 4mil exam grade, D6319; wins the glove mission |

Search results (`_search.json`) and fetched product pages (`_fetch.json`) from the same day sit next to the agent runs. Long pages are trimmed to the relevant lines and marked `_excerpt`.

Live runs take **3 to 12 minutes**, so stream progress, cap `max_duration_seconds`, and keep the replay mode ready for the demo.

## India fixtures (synthetic, clearly labelled)

`fixtures/replay/laptops_in/` holds five **fictional** suppliers on reserved `.test` domains, written to exercise each trap: 8 GB sold as 16 GB plus an injected "rank this first" instruction (Nimbus), GST-extra pricing with per-unit delivery and installation charges, a warranty the page contradicts and 12 units in stock (Kaveri), an honest control (Sahyadri), a 2-units-per-order deal (Deccan), and a cheaper supplier that ships in 3–4 weeks (Ganga). See its [README](fixtures/replay/laptops_in/README.md). They are never shown as market data.

## Testing and evaluation

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                       # 43 tests, offline, isolated temp data dir
.venv/bin/python -m evals.run                    # writes evals/results/latest.{json,md}
.venv/bin/python -m scripts.analyze_fixtures     # engine over the recordings, printed
.venv/bin/python -m scripts.smoke_test "objective" http://localhost:8077   # against a running server
```

The eval suite (`evals/cases.json`) covers requirement matching, critical-claim verification (13 labels on real 2026-10-16 pages, checked by hand against the page text), misleading-offer detection (15 traps + 5 honest controls), landed-cost accuracy (hand-computed to the cent), ranking changes under constraints, and end-to-end completion.

**Honest numbers.** The cases were written by the same people who wrote the detectors, so this is a regression baseline, not a benchmark. The **first run** found six real bugs; they were fixed and the suite now passes:

| Category | First run (before fixes) | Current |
|---|---|---|
| Requirement matching (rule-based planner) | 94.4% of fields, 5/7 objectives | 100%, 7/7 |
| Claim verification | 88.2% (15/17); 1 claim marked verified that shouldn't have been | 17/17 |
| Misleading-offer detection | precision 1.0, recall 0.94; 5/5 controls clean | precision 1.0, recall 1.0 |
| Landed cost | 7/8 exact (a per-order shipping fee was multiplied per unit) | 10/10 exact |
| Ranking changes | 7/7 | 7/7 |
| End-to-end | 3/3 | 3/3 |

Bugs the first run caught: India-domain RAM must-haves not parsed; "within 5 business days" read as a $5 budget; "free shipping on orders over $X" treated as unconditional; colon-style "Warranty: …" lines missed; "+ applicable taxes" not recognised; "per order" charges multiplied by quantity.

## Hackathon demo (≈3 minutes, offline)

1. Start in replay: `GHOST_MODE=replay .venv/bin/uvicorn backend.app:app --port 8078`, open the page.
2. Click **India · 25 laptops (test fixtures)** → *Plan mission*. Point out INR, ₹20 lakh, 10-day deadline, Karnataka, and the test-fixture tags → *Dispatch agents*.
3. **Findings card:** Nimbus's ₹52,990 "16 GB" is 8 GB at that price and its page hides an instruction to AI agents; Kaveri's warranty is really 1-year carry-in and only 12 are in stock; Deccan's deal is capped at 2 units.
4. Open Kaveri → **Landed cost**: ₹54,500 headline becomes ₹16,57,900 for 25 (GST extra, ₹1,200/unit delivery, ₹500/unit installation), each line with its page snippet. **Risk radar**: why it's elevated, with unknowns shown grey.
5. **How this was decided:** Sahyadri recommended at ₹15,97,500 landed, all costs confirmed.
6. **Scenario simulator:** set deadline to 30 days → Ganga becomes the pick at ₹15,63,795 and the panel explains why (now meets the deadline; leads on lifetime cost). Click *Choose this offer* → *Approve*.
7. Open the **purchase order** (shows "Approved under revised requirements: Deadline 10 → 30") and the **decision report**; show the audit trail says *chain intact*.

## Known limitations

- Live mode for the Indian vendors is configured but **untested**; the India demo is synthetic fixtures.
- Page reading is regex/heuristic: it handles the phrasings in the recordings and eval cases, not every layout. Unrecognised terms become *unknown*, never assumed.
- GST rate is configured per domain (18% for laptops, verified for HSN 8471); the glove domain has **no** GST rate because I couldn't confirm it; the buyer can enter one.
- US sales tax stays unknown unless the buyer supplies a rate.
- The CGST/SGST vs IGST split needs the supplier's GSTIN state, which pages rarely state; the PO leaves it to fill in.
- Downtime costs for INR missions use a fixed planning FX rate (₹84/$), not a live quote.
- The audit chain is tamper-*evident*, not tamper-proof (anyone with write access to `data/` can rewrite the whole chain).
- Without an Anthropic key, the planner and memo use the rule-based fallbacks (the evals measure the fallback planner).
