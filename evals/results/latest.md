# Ghost Operator evaluation results

Run 2026-10-20T12:52:16+00:00 · Python 3.13.9 · replay recordings + labelled synthetic pages · no network, no LLM.

> Regression cases written by the Ghost Operator authors, not an independent benchmark. Real-recording labels come from the vendor page text (fixtures/replay/*/_fetch.json, 2026-10-16) checked by hand; synthetic pages are fictional and labelled as such.

| Category | Result |
|---|---|
| Requirement matching (rule-based planner) | 100.0% of fields, 7/7 objectives fully right |
| Critical-claim verification | 100.0% of 17 labelled claims (13 on real pages); 0 marked verified without support |
| Misleading-offer detection | precision 1.0, recall 1.0; 20/20 cases exact; 5/5 honest controls clean |
| Landed-cost accuracy | 10/10 exact to the cent (max error 0.0) |
| Ranking changes under constraints | 7/7 |
| End-to-end completion | 3/3 missions |

## Failures
None.

## End-to-end missions

| Mission | Offers | Recommended | Provisional | Seconds |
|---|---|---|---|---|
| india | 6 | Sahyadri Office Tech (fixture) · Sahyadri WorkMate S14 Laptop | False | 2.03 |
| laptops | 20 | CDW · Lenovo ThinkPad E14 Gen 7 Laptop - Core 5 - 256GB  | False | 2.24 |
| gloves | 6 | eagleprotect.com · Sensitive 4mil Nitrile Exam Gloves | True | 1.06 |
