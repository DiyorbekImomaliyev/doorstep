# Design in detail

The diagrams are at the [top](../README.md#design-at-a-glance). People reach Doorstep through signed links,
buyers' AI assistants through MCP, and PayPal and Shippo through verified webhooks. Open a section
for the detail.

<details>
<summary><b>One order, end to end</b></summary>

1. **Checkout.** The buyer picks home, locker or signature; Doorstep creates the PayPal order with
   those shipping options. This is one of two synchronous PayPal writes, because the buyer is
   waiting on PayPal's page.
2. **Address check.** After approval, Shippo validates the address; the buyer adds a missing flat
   number with one tap; Doorstep PATCHes the order before capture. The country PayPal returns
   picks the legal rule set.
3. **Capture.** A worker job captures the payment. Every PayPal write from here on is an outbox
   job with a `PayPal-Request-Id`.
4. **Pack and ship.** A vision check compares the packing photo with the order. Worker jobs add
   tracking to the PayPal capture and register the parcel with Shippo.
5. **Watch.** Shippo and PayPal events are verified, stored in the inbox, acknowledged, then
   processed once by the worker. Carrier events drive the order state machine.
6. **"Not here".** The buyer reports from a link, the seal page or an AI assistant. The engine
   names the failure point, the scan audit checks the carrier's data, the rule file decides who
   is liable, and the policy picks the route: automatic refund under the seller's limit, the
   seller's one-tap approval, a person's review, or dispute evidence. The decision, the money
   action, its job and a signed receipt commit in **one transaction**.
7. **Refund.** The worker re-checks live state with PayPal, then sends the refund. A retry reuses
   the same key, so PayPal returns the first result instead of refunding twice.
8. **Recover.** For a carrier fault, the seller gets a claim pack, submits it on the carrier's
   site, and marks it paid or refused.

</details>

<details>
<summary><b>Design decisions: the problem, the choice, what it replaced and why</b></summary>

| Problem | Choice | Instead of | Why |
| --- | --- | --- | --- |
| A refund must happen exactly once | Transactional outbox, one `PayPal-Request-Id` per money action, our id in PayPal's `custom_id` | Calling PayPal inside the request; two-phase commit | A timeout in the request leaves no way to know whether money moved, and PayPal cannot join our transaction. Exactly-once *effect* comes from at-least-once retries plus idempotency. |
| Webhooks arrive late, twice or out of order | Verify, store in `webhook_inbox` (unique `event_id`), return 2xx, process in the worker | Processing inside the webhook request | A crash mid-way would lose the event; a duplicate becomes a no-op. |
| Webhooks can be missing when money moves | Ask PayPal live for the capture and for disputes right before a refund | Trusting the tables webhooks fill | A late dispute webhook could let a refund out during a dispute. A failed read sends nothing. |
| Work must commit with the state that causes it | A Postgres `jobs` table claimed with `FOR UPDATE SKIP LOCKED` | Redis, RabbitMQ or SQS | The job commits in the same transaction as its business row; one system to run; parallel workers stay safe. |
| Two writers can race on one order | Row locks, with unique constraints as the last line | Optimistic locking with retries | A lost race can mean money. The database refuses a second refund row even if the code above has a bug. |
| Buyers will not create accounts | HMAC-signed links, scoped to one order or seller, with an expiry | Accounts; server sessions | No friction for an upset buyer; a stateless web tier scales by adding copies. |
| History must be tamper-evident | Ed25519-signed receipts on one hash chain | A plain audit table; a blockchain | Editing any receipt breaks verification. Consensus would solve a trust problem one service does not have. |
| Law changes; code should not | Rules as versioned YAML with `id`, `version` and `source` | Rules in code or in prompts | Every decision is replayable; a rule change is a reviewed pull request; no model can skip a rule. |
| AI must be useful but never decide money | A coded state machine with narrow AI steps, each with a schema and a fallback | A free-roaming agent | Predictable, testable and cheap. The worst a prompt injection can do is ask for the refund the policy would allow anyway. |
| One person runs it | A modular monolith | Microservices | One local transaction covers a state change and its work; one blueprint deploys everything. |

</details>

<details>
<summary><b>System design: data model, state machines, failure handling, scale, health</b></summary>

**Data model.** One Postgres is the source of truth; constraints do the coordination.

| Group | Tables | The constraint that matters |
| --- | --- | --- |
| Orders | `sellers`, `orders`, `shipments`, `tracking_events`, `evidence` | state changes only through `app/orders/state.py`, under a row lock |
| Cases | `cases`, `decisions`, `approvals`, `disputes`, `recoveries` | one open case per order; `paypal_dispute_id` unique |
| Money | `money_actions`, `jobs` | **unique (decision_id, kind)**: one refund per decision, ever |
| Events | `webhook_inbox` | **unique `event_id`**: a replayed webhook is a no-op |
| Proof | `receipts`, `audit_log` | receipts chain by unique `seq`; audit rows are append-only |
| Cost | `model_usage` | one row per day and provider, incremented under a row lock |

All money is integer minor units plus an ISO 4217 code (GBP or USD), never floats.

**State machines.** Illegal transitions raise; every transition writes an audit row in the same
transaction.

- **Order:** `CREATED → PAID → PACKED → SHIPPED → IN_TRANSIT → DELIVERED / PICKED_UP → CONFIRMED`,
  with `EXCEPTION`, and `CASE_OPEN → RESOLVED`.
- **Case:** `OPEN → GATHERING_EVIDENCE → DECIDED → AUTO_REFUND | AWAITING_SELLER | HOLD_FOR_REVIEW |
  DISPUTE_EVIDENCE → SETTLED | CLOSED`. A case never settles without a recorded decision,
  approval and, for a refund, PayPal's COMPLETED. Only a person closes a held case without a
  refund; PayPal's resolution closes a disputed one.

**Failure handling.**

| What fails | What happens |
| --- | --- |
| PayPal times out, or returns 5xx or 429 | Retried with the same `PayPal-Request-Id`: 30 s, doubling, 5 attempts |
| PayPal refunded but our commit was lost | The next attempt finds our `custom_id` on the capture, records it and sends nothing |
| A dispute opens while a refund waits | The live re-check blocks the refund and moves the case to dispute evidence |
| Evidence changes after the decision | The fingerprint differs; the refund is blocked for a person |
| A refund fails for good | Marked failed, never retried with a new key; the case goes to a person |
| A refund marked failed, that PayPal made after all | PayPal's REFUNDED webhook finds it by its custom id: the record becomes completed with PayPal's refund id and a held case settles; a case a person closed stays closed |
| PayPal answers a refund as PENDING | Counted as on its way, so it is never sent twice; the case settles when PayPal confirms it (webhook, or the worker's 5-minute sweep reads PayPal) |
| PayPal resolves a dispute | The case that waited on it closes; PayPal's decision stands |
| A forged PayPal webhook | Signature check fails; nothing is stored |
| The worker crashes mid-job | The row lock is released; the job is claimed again; the key makes it safe |
| The main model is down or over budget | Gemini once, then a template; the page says which |

**Scale.** Sized for 10,000 sellers at 1,000 orders a month: about 115 tracking webhooks a second
at peak, and under one refund a second. So the money path is tuned for correctness and the
intake for throughput. The next steps, each with its trigger:

1. Adds web instances (the tier is stateless) and workers (`SKIP LOCKED` makes parallel claims
   safe).
2. Puts PgBouncer in front of Postgres, then sends Control Room reads to a replica.
3. Partitions `tracking_events` and `webhook_inbox` by month (about 90 GB a month each).
4. Splits the receipt chain per seller, anchored in a global chain.
5. Shards by `seller_id`: every query is already scoped to one seller.

**Health.** `/healthz` reports pipeline signals with no personal data: webhooks waiting, jobs late,
failed writes in the last hour, money held for a person, and paid model calls against the daily
cap.

</details>

<details>
<summary><b>AI components and guardrails</b></summary>

| Component | Model | Returns (validated) | Checked in code by | Fallback |
| --- | --- | --- | --- | --- |
| Packing check | Vision model | match, item, colour, size seen, confidence | below a confidence floor it is "check", never a pass | "Please check" |
| Explainer | LLM | summary, buyer message and claim letter | must match the rule outcome; no accusation, no new amount, no echo of or answer to the buyer's note, no promise or claim of a refund before it happens | template from the decision |
| Claim pack | LLM | sentences, each citing fact ids | unknown fact or a number not in its facts → sentence dropped | template built from facts |
| Case agent | LLM planner + PayPal Agent Toolkit | one read-only step at a time; each read becomes a typed observation | tool allowlist; code reacts to what PayPal showed (a listed dispute is recorded and routed to evidence, missing tracking re-queues the tracking job, an unreceived payment holds the case); the refund tool asks the policy only when PayPal agrees with Doorstep | Doorstep's own plan, step by step on the same observations |
| Policy check | LLM | misleading lines in the seller's policy | quotes must be word for word; numbers must be in the facts | "unchecked" |
| Policy reader | BM25, then LLM | answer with cited passages | every cited id must be one retrieved | the rule file quoted |
| Ask Doorstep | LLM | a chart plan from a fixed menu; no data rows sent | the browser computes every number from the seller's rows | keyword match on the menu |
| Risk score | XGBoost with monotone constraints, trained on synthetic data ([model card](MODEL_CARD_risk.md)) | calibrated probability and reasons (TreeSHAP) | a locker or signature can never raise risk | rule of thumb |

**Guardrails, in the order a call meets them:**

- **Builds prompts from facts in the database.** The rule outcome is decided before any model
  runs.
- **Sends the minimum.** Removes emails and phone numbers; cuts UK postcodes to the district and
  US ZIP+4 to ZIP5.
- **Keeps no photos.** Stores only a packing photo's SHA-256 fingerprint, so there is nothing to
  leak or delete later.
- **Treats buyer text as data.** Puts it in an escaped, delimited block that is never an
  instruction.
- **Calls models through one adapter** (`app/ai/llm.py`): structured output, a 20 s bound, a daily
  paid-call cap shared by both processes, and one fallback to the other provider (Claude Sonnet
  or Gemini). Opus models are refused.
- **Retries invalid output once,** then uses the component's fallback.

</details>

<details>
<summary><b>Project structure</b></summary>

```
app/
  *.py            app factory, ASGI entry, settings, database, money and clock
  api/            FastAPI routes: journey (checkout, buyer, seller, seal, demo), views, webhooks, agent, insights, autopilot, policy_check, proof
  orders/         tables, enums and the two state machines (state.py)
  checkout/       address check, delivery options, risk rule of thumb
  shipping/       Shippo client, tracking events, packing step
  cases/          failure-point engine, delivered-scan audit, case pipeline, recovery
  policy/         rule loader and money decisions: the only path to a refund
  paypal_client/  thin PayPal client: OAuth, reads, writes built from SDK models
  worker/         job runner, webhook inbox, job handlers, sweep
  ai/             model adapter, explainer, claim pack, packing check, policy reader, risk model
  agent/          case agent on the PayPal Agent Toolkit (read-only)
  mcp/            MCP server for buyers' AI assistants
  proof/          signed receipts, signed delivery proof, possession seal, Seller Protection status
  security/       signed links, rate limits
  ops/            pipeline health signals for /healthz, seller economics
  demo/           seeded demo and per-visitor scratch shops
rules/            UK, EU and US liability rules, money limits, carrier claim terms, prices (YAML)
tuning/           engine weights: the only file an automated tuning loop may change
web/              React + Vite app: Control Room (AG Grid), Insights (AG Studio), buyer pages
tests/            unit, property, integration (Postgres, recorded PayPal), browser; fixtures
evals/            failure-point, red-team and RAG evals with their datasets
scripts/          demo seed, sandbox smoke test, risk-model training, mutation and lock gates
docs/             architecture diagrams, screenshots, risk model card
.github/          CI on every push; mutation testing whenever app/policy changes
render.yaml       Render blueprint: web service, worker, Postgres
```

</details>

