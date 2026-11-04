# Doorstep

**An AI delivery agent for small PayPal sellers and their buyers.** It prevents avoidable delivery
failures at checkout, proves every handoff, and settles what still goes wrong fairly through
PayPal: automatically under the seller's limit, with one tap above it, never by accusing a buyer.

> In the UK, 15 million people had a problem with their last parcel; in the US, nearly 1 in 3
> households had one stolen. In the UK and EU the seller carries the risk until the buyer holds
> the package. Doorstep proves who had it, and settles the money fairly with PayPal.

## Judge's path (60 seconds)

| What | How |
| --- | --- |
| Live app | `[Render URL]`: press **Open my demo shop**. You get your own scratch copy (no login; it never touches the filmed demo). |
| Buyer view | **Be Sam** → "Not here" → watch the case decide in seconds, with the rule it used. |
| Seller view | **Open Maria's Control Room** → open a case → **Run the agent** or **Approve**. |
| Proof | Every money decision has an Ed25519-signed, hash-chained receipt: open **verify ↗** on any case (`GET /cases/{id}/verify`). |
| What is simulated | `GET /healthz` lists it: locker list, rerouting, and refunds on seeded scratch orders. Real sandbox refunds come from a real PayPal checkout. |
| Offline | `make setup && make test && make demo` |
| AI agents | MCP server at `/agent/mcp` (`report_delivery_problem`, `get_delivery_status`); a buyer mints a token for one order on their tracking page. |

## How it works

| Stage | What happens | PayPal does |
| --- | --- | --- |
| **Prevent** | Address check after approval (missing flat number → one tap fix, PATCHed before capture); delivery choice (home / locker / signature) recommended from order value and risk. | Orders v2 with shipping options, PATCH, capture |
| **Prove** | Packing photo check (the model describes, code decides, never auto-pass); tracking registered on the payment through the outbox; Shippo events drive the state machine. | Shipment tracking on the capture |
| **Resolve** | Buyer taps "Not here" (or their AI agent reports it). A deterministic engine names the failure point; the regional rule (UK CRA 2015 s.29, EU, US seller policy) decides liability; a coded policy picks the route; the model only explains. | Refund (idempotent), dispute evidence |
| **Agent** | The case agent reads the PayPal order, tracking and disputes through **PayPal's Agent Toolkit** (read-only allowlist), then calls its only money tool, `request_refund`, which asks the policy and stops at the seller's limit. | Agent Toolkit reads |

## Safety by construction

- **Money moves only through `app/policy`.** Import contracts (import-linter) prove the AI, agent
  and MCP code can never reach a PayPal write, even indirectly.
- **Every PayPal write** goes through a jobs outbox with a `PayPal-Request-Id`; retries never
  double-refund. Every webhook is verified and stored in an inbox before processing.
- **Never accuse or deny automatically:** contradictions, low confidence and repeat claims go to
  `HOLD_FOR_REVIEW`; outcomes that would blame the buyer go to the seller.
- **Rules live in versioned YAML** (`rules/*.yaml`); every decision stores rule id and version.
- **One model adapter** (`app/ai/llm.py`): structured Pydantic output, one retry, template
  fallback, Claude main with Gemini fallback, personal data redacted (postcode district only).
- Buyer text and text in photos are data, never instructions.

## Engineering evidence

| Check | Result |
| --- | --- |
| Unit + property tests (`make test`) | 798 passed; 24 strict `xfail` for criteria not built yet (each names why) |
| Acceptance trace (`make trace`) | 129/129 acceptance IDs have a test |
| Branch coverage | 100% on `app/policy` and `app/orders/state.py` |
| Mutation testing (`make mutmut-gate`) | Zero surviving logic mutants in the money path (see `docs/mutation-report.md`) |
| API fuzzing (`make fuzz`) | Schemathesis: no 500s, responses match the schema (it found and we fixed one 500) |
| Browser E2E (`make e2e`) | Playwright: "Not here" → approve → receipts verify; packing check; agent stops at the limit |
| CI | `.github/workflows/ci.yml`: lint, types, contracts, tests, fuzz, trace, gitleaks, web build |

## Run it

```bash
cp .env.example .env      # sandbox + test keys only; everything degrades honestly without them
make setup                # uv sync
make dev                  # API :8000 + worker + Vite :5173
make demo                 # seeds the filmed Sam and Maria scenario and prints private links
make test lint fuzz       # the gates
make web-build            # the API then serves the built app at :8000
```

Deploy: **Deploy to Render** uses `render.yaml` (web + worker + Postgres; secrets are generated or
entered in the dashboard, never in the repo).

## Stack

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Postgres (SQLite locally) · PayPal Orders v2,
Payments, Tracking, Disputes, Webhooks, **PayPal Agent Toolkit** · Shippo test mode · Claude
(Sonnet) with Gemini fallback · FastMCP · React 19 + Vite + **AG Grid** · pytest, Hypothesis,
Schemathesis, mutmut, import-linter, Playwright · Render.

## Honest limits

- No live carrier rerouting or real locker booking; both are simulated and labelled.
- Carrier claim letters are drafted, not filed on carrier portals.
- Packing photos are not stored (only their fingerprint); a later dispute can't show the image.
- The risk suggestion at checkout is a rule-of-thumb model for now; the XGBoost + SHAP model, the
  RAG policy reader, AG Studio and the Bryntum deadline radar are not built (strict `xfail` tests).
- Rules are information, not legal advice; the seller decides.
- In production, acting on a seller's PayPal account needs their explicit permission.

Design decisions and trade-offs: `docs/DECISIONS.md` (ADR-001…031). Spec: `docs/SPEC.md`.

MIT licence.
