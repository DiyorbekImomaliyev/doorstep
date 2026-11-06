# Doorstep — Devpost submission draft

Only `[TODO]` items remain, and each needs the human (a link, a quote, a lived experience). Every
number below was measured on the current commit; re-run `make test trace` before submitting and
update them if they changed. Deadline (Devpost): **Nov 12, 2026, 12:00pm PST = 11pm GMT+3**.
Target: submitted by the afternoon of Nov 12 (GMT+3).

## Required fields

**Project name:** Doorstep

**Elevator pitch:**
When a parcel says "Delivered" but isn't there, Doorstep refunds the buyer through PayPal in
minutes, proves who really had the parcel, and helps the honest seller claim the money back from
the courier.

**Public GitHub repo:** https://github.com/DiyorbekImomaliyev/doorstep (MIT `LICENSE` at the root;
make it public after the secret scan in the checklist).

**Demo video (under 3 minutes):** [TODO link]

**Live demo:** https://doorstep-ojmg.onrender.com (the README opens with a 60-second judge's path).

**New or existing project:** New, built during the hackathon (Nov 2026).

## Project story

### Inspiration
In the UK, a record 15 million people had a problem with their latest delivery, and 43% of those
then had a further problem trying to resolve it. UK law already says the seller carries the risk
until the buyer physically holds the parcel (Consumer Rights Act 2015, s.29). A courier's
"Delivered" scan wins every argument, so the buyer and the honest seller fight while the courier
walks away. PayPal already refunds buyers; the unsolved part is that the wrong party pays.
[TODO: one sentence from a real seller you spoke to, with their permission.]

### What it does
- **Prevent:** fixes incomplete addresses inside PayPal checkout (PATCHed before capture) and
  suggests home, locker or signature delivery from a calibrated risk model.
- **Prove:** checks the packed item against the order, puts a possession seal (QR code) in the box
  so the buyer confirms with one scan, and registers tracking on the PayPal payment.
- **Resolve:** when the buyer taps "Not here", a deterministic engine names the likely failure
  point, audits the courier's own "Delivered" scan, applies the UK/EU or US rule, and settles
  through PayPal: automatically under the seller's limit, with one tap above it, never by accusing
  the buyer. If a PayPal dispute is open, it sends the facts as dispute evidence instead.
- **Recover:** for a courier fault it builds a claim pack in which every sentence cites a verified
  fact, with the deadline from the carrier's terms.
- **Prove it was fair:** every money decision gets an Ed25519-signed, hash-chained receipt that
  anyone can verify.

### How we built it
Spec first, with Claude Code: a written spec, then numbered acceptance criteria, then a test for
every criterion, then code until the tests passed. 1,377 unit and property tests pass; all 174
acceptance criteria have a test; branch coverage is 100% on the money policy and the order state
machine; the failure-point engine scores 96.7% on 60 labelled cases and 95% on a hold-out, and
every miss is low confidence, so it goes to a person; mutation testing kills every logic
mutant of the money path (2,402 mutants); 7 tests on a real Postgres prove the row locks;
Schemathesis finds no 500s; 18 Playwright tests run in a real browser. Python and FastAPI on
Render with Postgres; PayPal Orders v2 through the PayPal Server SDK; Shipment Tracking, Refunds,
Disputes and Webhooks over REST; a bounded agent on PayPal's Agent Toolkit; Claude for vision and explanations with
Gemini as fallback; XGBoost with TreeSHAP reasons; React and AG Grid for the Control Room.

### Challenges we ran into
- **Live differed from local.** The Render worker had its own env rows with another PayPal app's
  keys, so PayPal answered 404 on capture for orders the web service had created. We now check
  service rows first, and a failed capture shows "Payment failed" instead of hanging.
- **A seller could fake the strongest proof.** Opening the seal page from the seller's own browser
  would have counted as the buyer's possession scan. That scan is now recorded but never counted
  (ADR-042).
- **Tests that run code are not tests that check it.** Mutation testing found 75 untested logic
  paths in Autopilot: a bug that applied another seller's rule would have passed every test. New
  tests close every one; the money path now has zero surviving logic mutants.

### Accomplishments that we're proud of
Zero unsafe money actions: no code path outside the policy service can reach a PayPal write
(proved by import contracts in CI), every write is idempotent, and property tests show refunds
never exceed the capture.

### What we learned
- A "Delivered" scan is not possession. UK law turns on possession, so the product had to prove it.
- Money rules need the database, not only code: the daily refund cap held in unit tests and broke
  under two racing requests on real Postgres until a row lock fixed it.
- The model should word decisions, never make them: every money outcome is code we can test and
  replay.

### What's next
The EU rule set (same possession law), real locker networks, carrier claim filing where carriers
offer an API, and offering Doorstep to PayPal merchants as a native feature.

## Built with (tags)
python, fastapi, postgresql, sqlalchemy, pydantic, paypal, paypal-server-sdk,
paypal-agent-toolkit, paypal-ai-toolkit, apimatic, langchain, claude, anthropic, gemini, xgboost,
fastmcp, react, vite, ag-grid, shippo, render, hypothesis, schemathesis, playwright, mutmut

## Tools and how we used them

| Tool | How we used it |
|---|---|
| PayPal Orders v2 (Server SDK) | Delivery choice as shipping options; address PATCH before capture; capture |
| PayPal Payments | Full or partial refunds, only through the policy service, with idempotency keys |
| PayPal Shipment Tracking | Proof of shipment registered on every payment |
| PayPal Disputes | `provide-evidence` with the facts when a dispute is already open |
| PayPal Webhooks | Verified, deduplicated events drive capture, refunds and disputes |
| PayPal Seller Protection | PayPal's own verdict on each capture, shown to the seller |
| PayPal Agent Toolkit | The case agent's PayPal reads (LangChain adapter), with an action allowlist |
| PayPal AI Toolkit | Claude Code plugin during development; `/paypal:doctor` checks |
| APIMatic Context Plugin | Guided Claude Code through the APIMatic-generated PayPal Server SDK |
| Claude (Anthropic) | Packing photo checks, explanations, buyer messages, claim-pack sentences |
| Gemini | Automatic fallback when the main model is unavailable |
| XGBoost | Delivery-risk score with exact TreeSHAP reasons in plain words |
| AG Grid | The seller's Delivery Control Room |
| Render | Web service, worker and Postgres from one `render.yaml` |
| Shippo (test mode) | Address validation and carrier tracking events |

**APIMatic plug-in question:** Claude Code used the APIMatic Context Plugin to integrate PayPal
Orders and Payments with the APIMatic-generated PayPal Python SDK: its models build and decode
create, patch, capture, tracking and refund calls, while Doorstep's client keeps one token cache,
one retry on 401 and a PayPal-Request-Id on every write (ADR-014, ADR-015). Also send the free-subscription form.

## Prize categories to select
Grand prize; Most impactful; Best demo delivery; Best use of PayPal + AI; Best use of agentic
commerce; Best use of AG Grid; Best use of APIMatic; Best use of Render.
(Not Bryntum: the deadline radar was not built.)

## Honest limits (also in the README and the video)
Lockers and rerouting are simulated; carrier claim packs are prepared, not filed; carrier event
locations in the seeded demo are simulated; the risk model is trained on synthetic data; the
Bryntum radar, the Promptfoo red team and the Ragas run are not built, and AG Studio's own built-in
agents need a licence we do not have (our Insights page and its Ask Doorstep agent are built); rules
are information, not legal advice.

## Pre-submit checklist
- [ ] Full-history secret scan clean (`gitleaks detect` over all commits), then make the repo public
- [ ] Video under 3:00, real clicks, refund shown from the merchant and buyer sides, sources captioned
- [ ] Live URL works in a private browser window; judge's path tested by someone else
- [ ] CI green on the final commit
- [ ] `/paypal:doctor` clean on the final commit
- [ ] Every number in this file re-measured on the final commit
- [ ] All tools listed with how they were used; APIMatic question answered
- [ ] Submitted, and the confirmation page saved
