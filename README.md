# Doorstep

**An AI delivery agent for small PayPal sellers.** Refunds the buyer in minutes when a parcel goes
missing, proves who really had the parcel, and drafts a fact-checked claim so the seller can
recover the loss from the carrier.

[![CI](https://github.com/DiyorbekImomaliyev/doorstep/actions/workflows/ci.yml/badge.svg)](https://github.com/DiyorbekImomaliyev/doorstep/actions/workflows/ci.yml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![Live app](https://img.shields.io/badge/live-doorstep--ojmg.onrender.com-7a1f2b.svg)](https://doorstep-ojmg.onrender.com)

**Live app:** https://doorstep-ojmg.onrender.com · PayPal sandbox and Shippo test mode · UK and US
orders

## Try it in 60 seconds

1. Open the [live app](https://doorstep-ojmg.onrender.com) and press **Open my demo shop**. You get
   a private shop with 16 orders that only you can see.
2. **Be Sam.** The courier says "Delivered", but the jacket never arrived. Tap **Not here**: Doorstep
   answers in seconds with the rule it applied and what happens next.
3. **Open Maria's Control Room**, open the **Black jacket** case and **Approve** the refund. The
   money row follows it to "Simulated" (a demo order), and Sam's page says it was sent and simulated.
4. Press **verify** next to the signed receipts: a public page re-checks every receipt in the chain.

**Buy with PayPal** runs a real sandbox checkout. It needs a PayPal sandbox personal account: sign
in at [developer.paypal.com](https://developer.paypal.com/dashboard/accounts), open **Sandbox
accounts**, and use the personal account's email and password (or create one there).

> **Real or simulated?**
> - **Real, in the PayPal sandbox:** Orders v2, capture, tracking, refunds and dispute calls on
>   "Buy with PayPal" checkouts; verified PayPal and Shippo webhooks; the AI models (Claude Sonnet,
>   with Gemini as fallback).
> - **Simulated in the demo shop:** refunds and disputes on the seeded orders (ids starting `SIM-`),
>   which every screen labels "simulated"; locker booking and rerouting; the carrier's scan
>   locations. Doorstep prepares carrier claims, and the seller files them on the carrier's site.
>   The delivery-risk model is trained on synthetic data ([model card](docs/MODEL_CARD_risk.md)).

![The Delivery Control Room: KPI cards, a prevention tip and the orders that need the seller](docs/img/screens/control-room.png)

## Overview

A courier's "Delivered" scan wins every argument today. The buyer is told to wait, the honest
seller pays for the parcel, and the carrier that lost it walks away. UK law already says the risk
stays with the seller until the buyer **physically holds** the parcel
([Consumer Rights Act 2015, s.29](https://www.legislation.gov.uk/ukpga/2015/15/section/29)), but
nobody can cheaply prove possession.

Doorstep closes that gap inside PayPal. It runs every order through five stages (prevent, prove,
resolve, recover, learn) and settles the money through PayPal's own APIs when something still goes wrong.

## Why it matters

Parcel problems are common in every market Doorstep covers, and they end in disputes that the
seller pays for.

| | Figure | Source |
| --- | --- | --- |
| **UK** | 15 million people (37%) had a problem with their latest delivery; 29% said the courier left before they reached the door | [Citizens Advice, Nov 2025](https://www.citizensadvice.org.uk/about-us/media-centre/press-releases/parcel-problems-reach-record-as-15-million-people-are-let-down-on-the/) |
| **EU** | 35% of online shoppers hit a problem buying online; a late delivery, for 1 in 5, was the most common | [Eurostat, Mar 2026](https://ec.europa.eu/eurostat/web/products-eurostat-news/w/ddn-20260311-1) |
| **US** | 3 in 10 adults, about 80 million people, have had a package stolen; an estimated $7.7 billion of parcels were stolen in the past year | [Security.org, Oct 2026](https://www.security.org/package-theft/annual-report/) |
| **Disputes** | "Item not received" is 1 in 5 chargebacks in a normal week; over Black Friday 2025 it was 1 in 3 | [Chargeflow, about 3 million disputes, Oct 2026](https://www.chargeflow.io/data-lab/peak-season-part-1) |

## How it works

| Stage | What Doorstep does | PayPal APIs |
| --- | --- | --- |
| **Prevent** | Checks the address after PayPal approval and patches the fix before capture. Offers home, locker or signature as PayPal shipping options, guided by the order's risk score. | Orders v2: shipping options, PATCH, capture |
| **Prove** | Checks the packing photo against the order. Prints a possession seal for the box. Adds tracking to the PayPal payment. | Shipment tracking |
| **Resolve** | Names the likely failure point, audits the "Delivered" scan, applies the regional rule. Then refunds, offers a refund inside a dispute, or sends dispute evidence. | Refunds, Disputes, Webhooks |
| **Recover** | Builds a carrier claim pack from checked facts, with the deadline from the carrier's own terms, and tracks it to paid or refused. | — |
| **Learn** | Turns the seller's repeated approvals into a rule the seller can switch on. | — |

## PayPal integration

| Stage | PayPal capability | How Doorstep uses it |
| --- | --- | --- |
| Checkout | Orders v2 | Creates the order with Home / Locker / Signature shipping options; PATCHes the fixed address before capture |
| Payment | Capture | Captures through the outbox; shows PayPal's Seller Protection verdict, never uses it as a money input |
| Shipping | Order tracking | Adds the carrier and tracking number to the capture through the outbox |
| Settlement | Refunds | Full or partial refund with `PayPal-Request-Id` and our id in `custom_id` |
| Disputes | Disputes API | Reads disputes live before every refund; sends `provide-evidence`; makes refund offers in the inquiry stage |
| Events | Webhooks | `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.REFUNDED` (also confirms a pending refund), `CUSTOMER.DISPUTE.CREATED / UPDATED / RESOLVED` (a resolved dispute closes its case), each verified with `verify-webhook-signature` |
| Agent | PayPal Agent Toolkit | Read-only allowlist: orders get, shipment get, disputes list and get |

Orders and Payments use the official PayPal Server SDK models, generated by APIMatic; tracking,
disputes and webhook verification use REST through one thin client (`app/paypal_client`).

**Proof it runs on PayPal.**

- The live app's [`/healthz`](https://doorstep-ojmg.onrender.com/healthz) counts the verified
  webhooks it has processed: 21 from PayPal (the latest a `PAYMENT.CAPTURE.REFUNDED`) and 54 from
  Shippo, as of Nov 9, 2026.
- `make smoke` runs the backend loop against the real sandbox: it pays an order with PayPal's test
  card, ships it, replays the "Delivered" event, reports "Not here" and refunds through the outbox.
  The Nov 9 run refunded capture `23K84665PL4219118` with PayPal refund `1HF202669T516105X`
  (COMPLETED) and verified the receipt chain.

## Product tour

### Order grid

![The order grid with one row's detail open: journey, proof on file, delivery risk and case](docs/img/screens/grid-detail.png)

| Column | What it shows |
| --- | --- |
| **Order** | Names the item, its category, the order id and the postcode district. |
| **Journey** | Tracks the order through five steps and marks a problem in red. |
| **Risk** | Scores the chance of a delivery failure before shipping; hover shows the reasons. |
| **Proof** | Counts the proof on file: address check, packing photo, tracking, delivered scan, possession. |
| **Value** | Shows the order value and the money still at stake. |
| **Delivery** | Shows the method the buyer chose: home, locker or signature. |
| **Next** | Starts the one action the order needs now: pack, review, approve or send evidence. |

The KPI cards above the grid count the orders that need the seller, parcels in transit, money at
stake and money recovered; a click filters the grid. Each row opens a detail panel without leaving
the table.

### Case

<img src="docs/img/screens/case.png" alt="A case: likely cause, rule, delivered-scan audit and carrier recovery" width="440" align="right">

- **Decision** states the likely cause, the confidence and the refund, with the rule file and
  version it used.
- **What would have changed this** names the proof that would have moved the risk to the buyer.
- **Courier's "Delivered" scan** shows the audit. Here the scan came from another district, in
  the same minute as two other parcels.
- **Recover from the carrier** shows the claim pack, its fact chips and its deadline.
- **Money** lists every PayPal write with its id and status.
- **Timeline** records each step and links to the signed receipts.

<br clear="right">

### Buyer's page

<p>
  <img src="docs/img/screens/buyer-report.png" alt="The buyer reports a problem with taps" width="260">
  &nbsp;
  <img src="docs/img/screens/buyer-answer.png" alt="The buyer gets an answer within seconds" width="260">
</p>

- **Reports** a problem with taps; a note is optional.
- **Answers** within seconds with the regional rule and the buyer's rights, and shows each next
  step.
- **Opens** from a signed link, with no account and no login.

Buyers' AI assistants use the same process through the MCP server at `/agent/mcp`: one tool reads
the delivery status, one reports a problem. Neither can move money.

### Insights

![Insights on AG Studio: orders, case rate, prevention rate, net delivery loss and where the money went](docs/img/screens/insights.png)

- **Reports** orders, case rate, prevention rate and net delivery loss, week by week, on AG Studio.
- **Breaks down** where deliveries failed, where the money went and which delivery method keeps
  proof on file.
- **Ask Doorstep** turns a plain question into a chart. The model picks the measure; the browser
  computes every number.

### Signed receipts

<img src="docs/img/screens/receipts.png" alt="Public receipt check: every receipt in the chain verified" width="440">

- **Signs** every money decision with Ed25519 and chains it to the previous receipt by hash.
- **Re-checks** the whole chain on a public page, `/verify/{case}`. Any edit breaks it.

## What makes it different

- **Proves possession with the buyer's own phone.** A QR seal inside the box records "I have it"
  in one tap: no app, no login, no tracker hardware. A scan proves possession only after the
  carrier's own "Delivered" scan, and a scan from the seller's own browser never counts.
- **Audits the carrier's "Delivered" scan with the carrier's own data.** Checks the scan's
  location, its order in the tracking history and batch scans across the seller's parcels, in
  deterministic code.
- **Prepares the carrier claim.** Drafts a claim pack where every sentence cites a checked fact,
  with the deadline from the carrier's own terms. Code drops any sentence that cites an unknown
  fact or adds a number; the seller files it.
- **Reads the law from versioned files.** Applies the UK, EU or US rule from `rules/*.yaml`; every
  decision stores the rule id and version, so it can be replayed.
- **Moves money exactly once.** Sends every PayPal write through a transactional outbox with one
  idempotency key, and asks PayPal live for the capture and open disputes right before a refund.
  A refund counts as sent only when PayPal says COMPLETED; a pending one waits for PayPal.
- **Signs every money decision.** Chains Ed25519-signed receipts by hash; a public page re-checks
  the whole chain.
- **Keeps AI away from money by construction.** Models write words and plans; code decides
  liability and limits. Import contracts in CI prove no AI code can reach a PayPal write.

## Quality

| Check | Result | Measured |
| --- | --- | --- |
| Unit and property tests (`make test`) | 1,710 passed | every push, in CI |
| Branch coverage (`make cov`) | 100% on `app/policy` and `app/orders/state.py`; CI fails below 100% | every push, in CI |
| Mutation testing on `app/policy` (`make mutmut-gate`) | 0 surviving logic mutants out of 2,974; only wording changes may survive | full local run, Nov 6; CI re-runs it on every change to `app/policy`, and new money code is mutation-tested before it is committed |
| Postgres concurrency (`make test-pg`) | 12 passed on Postgres 16: row locks, `SKIP LOCKED`, the refund-offer lease | every push, in CI |
| API fuzzing, Schemathesis (`make fuzz`) | 46 API operations fuzzed from the OpenAPI schema, 0 failures | every push, in CI |
| Browser tests, Playwright (`make e2e`) | 18 passed in Chromium | locally, against a running server |
| Failure-point engine (`make evals`) | 96.7% agreement with the expected cause on 60 hand-written scenarios (target 85%); 95.0% on a 20-case hold-out. This measures rule consistency, not accuracy on real deliveries | recorded run, Nov 7 |
| Policy reader, Ragas (`make evals`) | faithfulness 0.91 (target 0.90) and context recall 0.97 on 30 questions over six seller policies | recorded run, Nov 7 |
| Prompt-injection red team, Promptfoo (`make redteam`) | 64 probes from 40 attacks in 8 kinds: code checks caught 21 unsafe drafts, the other 43 answers were safe, 0 unsafe answers reached a buyer. With a stand-in model that obeys every attack, the refund still never changes | replays of recorded Claude Sonnet and Gemini answers, Nov 7 |

- **Names tests by requirement**, for example `test_PO_05_…`, so every rule in the code maps to
  the tests that prove it.
- **Locks the tests before the code.** CI fails if a locked test changes.
- **Runs the property tests with a fixed seed** (Hypothesis), so a gate never depends on luck.
- **Counts database queries in tests.** The Control Room went from 158 queries to 11 for 16
  orders, and a test fails if it grows again.

CI runs lint, types, import contracts, tests with coverage, the locked-test check, Postgres tests,
API fuzzing, a secrets scan and the web build on every push
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

## Where it goes next

The live app runs Doorstep's own PayPal checkout (the first row). Any small seller who takes
PayPal and ships parcels could use the same core: one policy gate for money, one outbox for PayPal
writes, one verified webhook inbox. Only the way orders arrive would change from one platform to
the next; the other rows are connectors that are not built yet.

| Where the seller sells | How they use Doorstep | Market |
| --- | --- | --- |
| **Own shop with PayPal Checkout** (live) | Doorstep's checkout creates the PayPal order, as in the live demo, and runs all five stages end to end | 439 million active PayPal accounts in about 200 markets can pay there ([PayPal 10-K](https://www.sec.gov/Archives/edgar/data/1633917/000163391726000024/pypl-20251231.htm)) |
| **Shopify** | Would install the Doorstep app and connect PayPal once. Orders and tracking would arrive from Shopify; the seal would print with the packing slip; the buyer would report a problem from the shipping email; refunds would go through Shopify's refund API, so the Shopify order and the PayPal capture stay in step | Millions of merchants in more than 175 countries, 44% of them in the US; $378.4 billion sold in 2025 ([Shopify 10-K](https://www.sec.gov/Archives/edgar/data/1594805/000159480526000007/shop-20251231.htm)) |
| **WooCommerce, Wix, Swappa** | Would connect with PayPal in one consent, dispute permission included, and follow captures and disputes on the seller's PayPal account | Every shop on these platforms that takes PayPal |
| **eBay, Etsy, Amazon** | The marketplace holds the money and decides the case, so Doorstep would supply the proof: tracking and signature answers to "not received" inquiries, and carrier claim packs | 135 million active eBay buyers ([eBay 10-K](https://www.sec.gov/Archives/edgar/data/1065088/000106508826000027/ebay-20251231.htm)) |

Each connector would sit on the same core: the platform's orders come through the verified inbox,
and its rules are one more file in [`rules/`](rules/).

## Run it locally

Needs Python 3.12 with [uv](https://docs.astral.sh/uv/) and Node 20 or newer. Postgres 16 is
optional: leave `DATABASE_URL` blank to use SQLite. With no keys the app still runs: checkout
answers 503, and explanations use templates instead of a model.

```bash
cp .env.example .env      # sandbox and test keys only; every key is optional
make setup                # Python dependencies (uv) and web dependencies (npm ci)
make web-build            # build the web app, so http://localhost:8000 serves it
make dev                  # API on :8000, worker, and the Vite dev server on :5173
make demo                 # in a second terminal: seed the demo shop and print its links
make lint test            # lint, types, import contracts, tests
```

**Deploy:** `render.yaml` creates the web service, the worker and Postgres in one step. Secrets are
set in the Render dashboard, never in the repository.

## Design

A modular monolith on Render: **one FastAPI web service, one background worker, one Postgres.**
People reach it through signed links, buyers' AI assistants through MCP, and PayPal and Shippo
through verified webhooks. Open a section for the detail.

<details>
<summary><b>Architecture and one order, end to end</b></summary>

![System architecture: web service, worker and Postgres on Render, with PayPal, Shippo and two model providers](docs/img/architecture.svg)

**One order, end to end:**

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

<details>
<summary><b>Money path</b>: from "Not here" to a PayPal refund</summary>

![Sequence diagram of the money path](docs/img/money-path.svg)

</details>

<details>
<summary><b>AI system</b>: what each model sees, returns and is checked against</summary>

![AI system design](docs/img/ai-system.svg)

</details>

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
| Risk score | XGBoost with monotone constraints, trained on synthetic data ([model card](docs/MODEL_CARD_risk.md)) | calibrated probability and reasons (TreeSHAP) | a locker or signature can never raise risk | rule of thumb |

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
  api/            FastAPI routes: checkout, buyer, seller, seal, webhooks, verify
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
  proof/          signed receipts, possession seal, Seller Protection status
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

## Stack

| Area | Tools |
| --- | --- |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Postgres 16 |
| PayPal | PayPal Server SDK (APIMatic), REST for tracking, disputes and webhooks, PayPal Agent Toolkit |
| AI | Claude Sonnet with Gemini fallback through one adapter; XGBoost with TreeSHAP; BM25; FastMCP |
| Frontend | React 19, Vite, AG Grid Enterprise, AG Studio |
| Quality | pytest, Hypothesis, Schemathesis, mutmut, import-linter, Playwright, Promptfoo, Ragas |
| Hosting | Render (web service, worker, Postgres), GitHub Actions |

## Licences

- **AG Grid Enterprise and AG Studio** read their licence keys from `AG_GRID_LICENSE_KEY` and
  `AG_STUDIO_LICENSE_KEY` at build time. The keys never enter the repository.
- **Ask Doorstep**, the Insights agent, is built into Doorstep: it runs on the app's model adapter, so it shares
  the daily call cap, the strict schemas and the fallbacks of every other AI step.

Doorstep itself is released under the [MIT licence](LICENSE).
