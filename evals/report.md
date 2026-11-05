# Eval report

Measured Nov 5, 2026 on the current commit. Re-run with `make evals` (no model calls; the engine
is deterministic, FE-10).

## FE-09 Failure-point accuracy

| Set | Cases | Accuracy | Target |
| --- | --- | --- | --- |
| Labelled set (30 UK, 30 US) | 60 | **96.7%** (58/60) | 85% |
| Hold-out (never used for tuning) | 20 | **95.0%** (19/20) | reported only |

**How the labels were made.** `scripts/build_fe09_dataset.py` writes each case as a scenario
with its label and a one-line reason taken from the failure-point table in SPEC section 7 (and
7a.2 for contradicted scans). Labels were written before the engine was run on them and were not
changed afterwards. Region changes who is liable, not the failure point, so every labelled
scenario appears once for the UK and once for the US.

**Misses (all on one ambiguous scenario: the seller cleared a packing-photo mismatch).**

| Case | Label | Engine | What happens |
| --- | --- | --- | --- |
| fe-GB-25, fe-US-25: wrong item, seller cleared the mismatch | WRONG_ITEM | CLAIM_CONTRADICTS_EVIDENCE, low confidence | Held for a person; no money moves automatically |
| ho-US-04: missing item, seller cleared the mismatch | WRONG_ITEM | CARRIER_LOST, low confidence | Low confidence goes to a person |

Every miss is low confidence, and low confidence always routes to a human (SPEC section 7), so no
miss leads to an automatic refund or denial. Zero unsafe money actions.

**Limits.** The cases were written by the team that wrote the engine, from the same spec. They
check that the engine does what the spec says; they do not prove it matches real-world outcomes.
That needs labelled cases from real sellers.
