# Doorstep

**When a parcel says "Delivered" but isn't there, Doorstep refunds the buyer through PayPal in
minutes, proves who really had the parcel, and helps the seller claim the money back from the
courier.** Built for small PayPal sellers in the UK and US, in the PayPal sandbox and Shippo test
mode.

> In the UK, 15 million people had a problem with their last parcel (Citizens Advice, 2025). UK law
> puts the risk on the seller until the buyer physically holds the parcel (Consumer Rights Act 2015,
> s.29), so a courier's "Delivered" scan is not proof. Doorstep gets the proof.

**Live app:** https://doorstep-ojmg.onrender.com · MIT licence

## Judge's path (60 seconds)

| Step | What to do |
| --- | --- |
| 1 | Open the live app and press **Open my demo shop**. You get your own scratch copy: no login, and it never touches the filmed demo. |
| 2 | **Be Sam** (the buyer): tap **Not here**. The case is decided in seconds, with the rule it used and Sam's rights. |
| 3 | **Open Maria's Control Room** (the seller): open the case to see the delivered-scan audit, the carrier claim pack and **Approve refund**. |
| 4 | Open **signed receipts** on any case: every money decision is Ed25519-signed and hash-chained (`GET /cases/{id}/verify`). |
| What is simulated | `GET /healthz` lists it: lockers, rerouting, and refunds on seeded scratch orders. A real sandbox refund needs a real PayPal checkout (**Buy with PayPal**). |
| Offline | `make setup && make test && make demo` |

## How it works

| Stage | What happens | PayPal |
| --- | --- | --- |
| **Prevent** | The address is checked after PayPal approval; a missing flat number is fixed with one tap and PATCHed before capture. Home, locker or signature delivery is suggested by a calibrated risk model with plain-word reasons. | Orders v2 shipping options, PATCH, capture |
| **Prove** | A packing photo is checked against the order (the model describes, code decides, never auto-pass). A **possession seal** (QR code in the box) lets the buyer confirm with one scan. Tracking is registered on the payment. | Shipment tracking |
| **Resolve** | A deterministic engine names the likely failure point, audits the courier's own "Delivered" scan (location, sequence, batch scans), applies the regional rule, and a coded policy picks the route. The model only explains. | Refunds (idempotent), dispute evidence |
| **Recover** | For a courier fault, a claim pack is built from verified facts: every sentence cites a fact, and the deadline comes from the carrier's terms. The seller submits it and records the outcome. | — |

The case agent reads the PayPal order, tracking and disputes through **PayPal's Agent Toolkit**
(read-only allowlist). Its only money tool is `request_refund`, which asks the policy service and
stops at the seller's limit. Buyers' AI assistants can report a problem through the MCP server at
`/agent/mcp`.

## Architecture

![Doorstep system architecture: web service, worker and Postgres on Render, with PayPal, Shippo and two model providers](docs/img/architecture.svg)

A modular monolith on Render: one FastAPI web service, one worker, one Postgres. Four choices carry
the design.

- **Inbox and outbox in the same Postgres.** A webhook is verified and stored before it gets a 2xx.
  Every PayPal write is a job committed in the same transaction as the decision that caused it.
  Workers claim both with `FOR UPDATE SKIP LOCKED`.
- **Exactly-once effect, not exactly-once delivery.** Retries reuse one `PayPal-Request-Id`, a
  unique (decision, kind) row allows one refund per decision, and a re-check against the live
  capture runs right before money moves.
- **AI proposes, code decides.** Models word explanations, draft claims and plan read-only steps;
  a verifier in code drops any sentence that cites an unknown fact. Liability, limits and refunds
  are deterministic code.
- **Proof anyone can check.** Each money decision gets an Ed25519-signed receipt in a hash chain,
  public at `/cases/{id}/verify`.

The full design is in **[docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md)**: requirements, estimates,
the options weighed for each decision, failure modes and the scale path. It also has the
[money path as a sequence diagram](docs/img/money-path.svg) and the
[AI system design](docs/img/ai-system.svg).

## Safety by construction

- **Money moves only through `app/policy`.** Import contracts (import-linter, run in CI) prove the
  AI, agent and MCP code can never reach a PayPal write, even indirectly.
- **Every PayPal write carries a `PayPal-Request-Id`.** Capture, tracking, refunds and dispute
  evidence go through a jobs outbox, so retries never double-refund; creating the order and fixing
  its address happen in the buyer's own request, before any money moves. Every webhook is verified
  and stored in an inbox before it is processed.
- **Never accuse or deny automatically.** Contradictions, low confidence and repeat claims go to a
  person; outcomes that would blame the buyer go to the seller.
- **A seller's own scan never counts.** A seal scan from the seller's signed-in browser is recorded
  but never counted (signed HttpOnly cookie, SL-07). A seller could still scan from another device,
  so a scan is one signal among several, and an outcome that would blame the buyer always goes to a
  person, never to an automatic denial.
- **Rules live in versioned YAML** (`rules/*.yaml`); every decision stores the rule id and version.
- **One model adapter** (`app/ai/llm.py`): structured Pydantic output, one retry, template
  fallback, Claude with Gemini as fallback, a 20-second bound per call, and only the postcode
  district is sent, never the address.
- PayPal's own **Seller Protection verdict** on each capture is shown to the seller as information;
  it never changes a decision. Refunds show that PayPal keeps its original fee.

## Engineering evidence

All numbers measured on the current commit.

| Check | Result |
| --- | --- |
| Unit + property tests (`make test`) | 1,377 passed; 10 strict `xfail` for criteria not built, each naming why |
| Postgres concurrency (`make test-pg`) | 7 tests on a real Postgres: one refund per approval, no lost update, a duplicate webhook processed once, the daily cap holds under a race, no model call while a row is locked, one receipt per double tap, rule order without the index |
| Acceptance trace (`make trace`) | 174/174 acceptance IDs have a test |
| Branch coverage | 100% on `app/policy` and `app/orders/state.py` |
| Mutation testing (`make mutmut-gate`) | 2,402 mutants in `app/policy`: 2,341 killed, 0 logic survivors; the 61 survivors only change wording (`docs/mutation-report.md`) |
| Failure-point eval (`make evals`) | 96.7% on 60 labelled cases (30 UK, 30 US), 95% on a 20-case hold-out; every miss is low confidence and goes to a person (`evals/report.md`) |
| API fuzzing (`make fuzz`) | Schemathesis over 44 operations: no 500s, responses match the schema |
| Browser E2E (`make e2e`) | 18 Playwright tests in a real browser: report → approve → receipts verify; seal scan; scan audit and carrier recovery; dispute evidence; packing check; the agent stops at the limit; autopilot on and off; the case queue; Insights widgets and Ask Doorstep. 18/18 in two runs back to back; one earlier run timed out once under load (see limits) |
| CI | `.github/workflows/ci.yml`: lint, types, import contracts, tests, fuzz, trace, gitleaks, web build |

## Business model (proposed, not yet validated with sellers)

- **Free** under 100 orders a month.
- **A success fee on carrier recoveries**: Doorstep is paid only when the courier pays the seller
  back. Dispute-recovery services take about a quarter of what they recover; Doorstep would take
  less, because the claim pack is generated automatically.
- **The bigger path:** a PayPal-native feature for its merchants. Even 50,000 sellers would touch
  under 1% of US parcels, so the impact comes from a platform offering it.

## Run it

```bash
cp .env.example .env      # sandbox and test keys only; everything degrades honestly without them
make setup                # uv sync
make dev                  # API :8000 + worker + Vite :5173
make demo                 # seeds the filmed Sam and Maria scenario and prints private links
make lint test fuzz       # the gates
make web-build            # the API then serves the built app at :8000
```

**Deploy to Render** uses `render.yaml`: web service, worker and Postgres. Secrets are generated
or typed in the dashboard, never stored in the repo.

## Stack

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Postgres (SQLite locally) · PayPal Orders v2
(PayPal Server SDK), Payments, Shipment Tracking, Disputes, Webhooks, **PayPal Agent Toolkit**
(LangChain adapter) · Shippo test mode · Claude Sonnet with Gemini fallback · XGBoost with TreeSHAP
reasons · FastMCP · React 19, Vite, **AG Grid** · pytest, Hypothesis, Schemathesis, mutmut,
import-linter, Playwright · Render.

## Honest limits

- Lockers and rerouting are simulated and labelled. Carrier event locations in the seeded demo are
  simulated; for the Shippo test carrier the location check reports "unknown".
- Carrier claim packs are prepared and tracked; the seller files them on the carrier's site.
- A seal scan proves someone opened the parcel. Not scanning proves nothing and is never held
  against a buyer.
- The risk model is trained on synthetic data (`docs/MODEL_CARD_risk.md`). The policy reader uses
  BM25 retrieval, not embeddings (ADR-033).
- Not built (strict `xfail` tests say so): the Bryntum deadline radar, the Promptfoo red-team
  suite, the Ragas run, the labelled packing-photo set and dispute make-offer. The AG Studio
  Insights page and its Ask Doorstep agent are built; working beside Studio's own built-in agents
  needs a Pro-with-AI licence.
- A buyer's report waits for two model calls (the explanation and the claim-pack draft) before it
  answers. Usually a few seconds; each attempt is capped at 20 s (`LLM_TIMEOUT_S`). The decision
  and its template wording are saved first, so a slow model never blocks money or rows.
- Shippo's test tracking numbers are shared by every demo shop, so a test carrier event goes to the
  newest shipment with that number. Real tracking numbers belong to one parcel.
- Rules are information, not legal advice; the seller decides. In production, acting on a seller's
  PayPal account needs their explicit permission.

Design decisions: `docs/DECISIONS.md` (ADR-001 to ADR-044). Spec: `docs/SPEC.md`. Acceptance
criteria: `specs/ACCEPTANCE.md`.
