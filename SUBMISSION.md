# Doorstep — Devpost submission draft

Fill every `[TODO]` with real numbers from the eval report. Never submit a number you did not
measure. Official deadline (Devpost page): **Nov 12, 2026, 12:00pm PST = 11pm GMT+3**.
Our target: submitted by the afternoon of Nov 12 (GMT+3).

## Required fields

**Project name:** Doorstep

**Elevator pitch (one or two sentences):**
Doorstep is an AI delivery agent for small PayPal sellers and their buyers: it prevents failed
deliveries at checkout, proves every handoff, and settles what still goes wrong fairly through
PayPal, in minutes instead of weeks.

**Public GitHub repo:** [TODO link] — must show the MIT `LICENSE` file at the root.

**Demo video (under 3 minutes, showing the product actually working):** [TODO link]

**Live demo + judge's path:** [TODO Render URL] — the README opens with the 60-second table.

**New or existing project:** New, built during the hackathon (Nov 2026).

## Project story (Devpost sections)

### Inspiration
In the UK, a record 15 million people had a problem with their latest parcel delivery. In the US,
nearly 1 in 3 households had a package stolen last year, and 24% of retailers refused
responsibility. In the UK and EU the law says the seller carries the risk until the buyer
physically holds the package, so small sellers pay for failures they cannot see or prove.
[TODO: one line from a real seller you spoke to.]

### What it does
- **Prevent:** fixes incomplete addresses and offers home, locker or signature inside PayPal
  checkout, recommended by a risk model.
- **Prove:** checks the packed item with vision AI, registers tracking on the PayPal payment, and
  watches carrier events.
- **Resolve:** when a buyer taps "Not here", it names the most likely failure point, applies the
  UK/EU or US rule, and settles through PayPal: automatically under the seller's limit, with one
  tap above it.
- **Prove it was fair:** every money decision gets a signed receipt anyone can verify.

### How we built it
Spec-first with Claude Code: a written spec, then numbered acceptance criteria, then tests that
cover every criterion, then code until the tests passed. Python/FastAPI on Render, Postgres,
PayPal Orders, Payments, Shipment Tracking, Disputes and Webhooks; a bounded-autonomy agent on
PayPal's Agent Toolkit; Claude for vision and explanations; XGBoost + SHAP for risk; AG Studio for
the seller's Control Room; Bryntum Scheduler for the deadline radar.
[TODO: test count, eval accuracy, red-team result.]

### Challenges we ran into
[TODO: real ones, e.g. a sandbox behaviour that differed from the docs and how you handled it.]

### Accomplishments that we're proud of
[TODO: e.g. "0 unsafe money actions across N property tests and 40 attack prompts".]

### What we learned
[TODO]

### What's next
More regions (EU rule set is ready), real locker networks, carrier claim filing, and the same
engine for high-value and perishable goods.

## Built with (tags)
python, fastapi, postgresql, pgvector, paypal, paypal-agent-toolkit, paypal-ai-toolkit,
claude, anthropic, langchain, xgboost, shap, react, ag-studio, ag-grid, bryntum, render, postman,
apimatic, shippo, langfuse, deepeval, ragas, promptfoo, hypothesis, playwright

## Tools and how we used them

| Tool | How we used it |
|---|---|
| PayPal Orders v2 | Delivery choice (home / locker / signature) as shipping options in checkout |
| PayPal Payments | Captures and full or partial refunds, only through the policy service |
| PayPal Shipment Tracking | Proof of shipment on every payment |
| PayPal Disputes | Evidence submission when a dispute is already open |
| PayPal Webhooks | Verified, deduplicated events drive the whole workflow |
| PayPal Agent Toolkit | The runtime agent's PayPal tools, with an action allowlist |
| PayPal AI Toolkit | Claude Code plugin during development; `/paypal:doctor` on every phase |
| Claude (Anthropic) | Packing and damage photo checks; plain-English explanations; drafts |
| XGBoost + SHAP | Delivery-risk score with reasons |
| AG Studio + AG Grid | Seller Control Room: theme, custom widgets, custom agent with Studio's agents |
| Bryntum Scheduler | Deadline radar for dispute and carrier-claim windows |
| Render | Web service, worker, cron, Postgres, one-click deploy |
| Postman | Public collection of the full loop with tests, run in CI |
| APIMatic Context Plugin | Guided Claude Code when integrating the PayPal Server SDK |
| Shippo (test mode) | Address validation and realistic carrier tracking events |

**APIMatic plug-in question (answer honestly):** [TODO: what you used it for, e.g. "Claude Code
used the APIMatic Context Plugin to build our Orders and Payments integration with the PayPal
Server SDK, including retries and error handling."] Also send the free-subscription form.

## Prize categories to select
Grand prize; Most impactful; Best demo delivery; Best use of PayPal + AI; Best use of agentic
commerce; Best use of AG Grid; Best use of APIMatic; Best use of Bryntum; Best use of Render.

## Honest limits (also in README and video)
Lockers and rerouting are simulated; carrier claims are drafted, not filed; the risk model is
trained on synthetic data; rules are information, not legal advice.

## Pre-submit checklist
- [ ] Repo public, MIT `LICENSE` present, no secrets (gitleaks clean)
- [ ] Video under 3:00, real clicks, refund shown from merchant and buyer sides, sources captioned
- [ ] Live URL works in a private browser window; judge's path table tested by someone else
- [ ] Postman collection link works; CI badge green
- [ ] `/paypal:doctor` clean on the final commit
- [ ] Every number in this file comes from `evals/report.md`
- [ ] All tools listed with how they were used; APIMatic question answered
- [ ] Submitted, and the confirmation page saved
