# Doorstep — compressed build runbook ("Claude as a compiler")

The source of truth is the spec: `docs/SPEC.md` (what and why) and `specs/ACCEPTANCE.md`
(testable criteria). Tests are compiled from the acceptance criteria; code is compiled from
the tests. You review specs and test names; Claude writes and fixes the code until green.

## Honest time answer

| Plan | What you get |
|---|---|
| 1 build day | The core only: money loop, rules, engine, policy, tracking, buyer pages, basic grid, one end-to-end test. Everything else stays as documented `xfail` limits. |
| **2 build days (recommended minimum for full quality)** | Every acceptance criterion, evals, red-team, Bryntum, MCP, deploy. |
| + half a day before | Prep: accounts, keys, test photos, eval data (cannot be skipped). |
| + 1 day after | Real seller feedback, README polish, video, submit. |

Why not one day for everything: the slow parts are not code. They are sandbox surprises,
your review gates, labelling eval data, deploy issues and the video. AI does not speed those up.

## The 6 non-negotiable rules of the loop

1. **Two human gates per phase:** you approve the acceptance criteria, then the list of test
   names. You do not need to read the code.
2. **Tests are locked after your approval.** Tag the commit `tests-locked-<phase>`.
   During implementation Claude must not edit or delete a locked test; if a test looks wrong it
   stops and asks. `scripts/check_tests_locked.sh` fails if `tests/` changed since the tag.
3. **Fast offline loop:** all PayPal, Shippo and model calls are faked in unit and integration
   tests (respx or recorded responses). Live sandbox runs only at checkpoints.
4. **100% spec coverage:** `scripts/trace.py` fails if any acceptance ID has no test.
5. **Tests must catch real bugs:** 100% branch coverage on `app/policy` and
   `app/orders/state.py`; mutation testing (mutmut) on `app/policy` with zero surviving
   mutants or a written reason.
6. **Commit and tag every green run.** If a phase goes wrong, return to the last green tag.

## Before the build (half a day of prep: bring all of this)

- [ ] Claude Code installed; **PayPal AI Toolkit** installed and `/paypal:setup` passing;
      APIMatic Context Plugin installed (and free-subscription form sent); Bryntum skill + MCP server
- [ ] Repo created with `CLAUDE.md`, `docs/SPEC.md`, `specs/ACCEPTANCE.md`; pushed to GitHub
- [ ] Render: account linked to GitHub; an empty web service created so you have the public URL
- [ ] PayPal sandbox: 4 named accounts (UK business + UK personal, US business + US personal);
      app "Doorstep UK"; Transaction search on; webhook on `<render-url>/webhooks/paypal` with
      all events → webhook ID (see START_HERE step 6b)
- [ ] Shippo: test API key; webhook pointing at `<render-url>/webhooks/shippo/<token>`
- [ ] Anthropic API key with credit; Langfuse keys
- [ ] AG Studio 45-day trial key; the PayPal + AG Studio boilerplate cloned for reference;
      Bryntum trial is fine
- [ ] Questions posted in the PayPal Discord early: shipping options in sandbox Orders, the
      sandbox dispute flow, and anything `/paypal:doctor` flags that you don't understand
- [ ] 40 packing photos you took yourself (2–3 items; right and wrong colour/size), labelled
- [ ] 60 failure cases (30 UK, 30 US) drafted by Claude, every label checked by you
- [ ] 30 policy questions with reference answers; 2–3 sample seller policies
- [ ] Local: Python 3.12, uv, Node 20+, Playwright browsers, `.env` filled (never committed)

Optional: give Claude Code the **sandbox** buyer login so Playwright can approve payments
automatically (sandbox only, never a real account). Fallback: you click Approve.

## Schedule

### Build day 1 — the core (money, rules, engine)

| Time | Phase | You do | Done when |
|---|---|---|---|
| 08:00–08:30 | A. Spec review | Approve Claude's gap list for PP, WH, SM, RL, FE, PO | ACCEPTANCE.md updated |
| 08:30–09:30 | B. Tests first | Approve test names; tag `tests-locked-core` | All core tests exist and fail |
| 09:30–13:00 | C. Implement | Nothing (check in hourly) | Core tests green, trace + coverage pass |
| 13:00–14:00 | D. Live smoke | Approve sandbox payment if needed | Real sandbox loop works on Render |
| 14:00–14:30 | B. Tests first | Approve tests for CK, TR, BC, PK, EX; tag | Tests exist and fail |
| 14:30–18:30 | C. Implement | Nothing | Green; vision + explainer working |
| 18:30–19:30 | Evals | Check results look right | FE-09, PK-05, EX-02 measured |
| 19:30–20:30 | E. Limits review | Decide what to fix vs `xfail` | Limits list written; green tag |
| Night | F. Autoresearch | Sleep | Branch with kept improvements |

### Build day 2 — screens, agents, safety

| Time | Phase | You do | Done when |
|---|---|---|---|
| 08:00–08:30 | Review overnight branch | Merge only if the hold-out score improved | Merged or dropped |
| 08:30–09:00 | B. Tests first | Approve tests for AS, AT, BR, MC, RG, RK, SE, PR, SB, E2E; tag | Tests exist and fail |
| 09:00–14:00 | C. Implement (2 sessions) | Session 1: AG Studio Control Room (theme, custom widgets, custom agent) + Bryntum + pages. Session 2: bounded agent (Agent Toolkit), MCP, RAG, risk model | Green |
| 14:00–16:00 | Safety | Nothing | Red-team 100%, Schemathesis clean, mutmut clean |
| 16:00–18:00 | D. Live end to end | Watch the demo run | E2E-01..03 pass on Render |
| 18:00–20:00 | Docs | Read README once | README, Postman collection, eval report |

### Day 3 — proof and story

Real seller feedback (3–5 sellers), video, SUBMISSION.md filled in, final checklist, submit.
The video must show the product actually working: the real sandbox refund seen from both the
merchant and the buyer sandbox accounts, and the webhook in the sandbox event log.

## Phase prompts (copy and paste into Claude Code)

**A. Spec review**
```
Read CLAUDE.md, docs/SPEC.md and specs/ACCEPTANCE.md. For the modules <IDs>, list
every gap, contradiction or untestable criterion, and propose exact edits to
ACCEPTANCE.md. Do not write code. Wait for my approval.
```

**B. Tests first**
```
Write tests covering every acceptance criterion for <IDs>. Each test name contains its
ID. Use fakes for PayPal, Shippo and the model (no network). Use Hypothesis for PO-06,
PO-07 and SM-04. Add scripts/trace.py and the import-linter contract for PO-01.
Run them: they must fail for the right reason. Show me the list of test names only.
```
After approval: `git tag tests-locked-<phase>`.

**C. Implement until green**
```
Implement the code for <IDs> until all locked tests pass, trace.py passes, coverage
rules in BUILD_RUNBOOK.md pass and /paypal:doctor reports no problems. You may not edit or delete anything in tests/ since tag
tests-locked-<phase>; if a test looks wrong, stop and tell me why. Work in small steps,
run make lint test after each step, commit when green. Record design choices in
docs/DECISIONS.md.
```

**D. Live smoke**
```
Deploy to Render and run scripts/day1_loop.py against the real PayPal sandbox and Shippo
test mode. Record any difference from the spec in docs/DECISIONS.md, record the real
responses as fixtures in recordings/, add a contract test proving the fake server's responses
in tests match the recorded shapes, and propose the smallest fix.
```

**E. Limitations review**
```
List the limitations of what exists now: what is simulated, what is not generic, which
criteria are only partly met, what could fail in the demo. For each: fix now, or mark as
xfail with the acceptance ID and a one-line reason. Update docs/PROGRESS.md.
```

**F. Overnight autoresearch**
```
Follow evals/program.md. You may edit only tuning/engine_config.py. Run the case eval
(about 5 minutes per experiment), keep a change only if macro-F1 improves AND unsafe
money actions stay 0 AND the red-team pass rate stays 100%. Commit each kept change on
branch autoresearch/<date>. Never look at evals/datasets/holdout.
```

## If you truly have only one day

Build only: PP, WH, SM, RL, FE, PO, TR, BC, CK (simple), CR (basic grid), E2E-01.
Everything else stays as `xfail` tests with reasons. That is honest, demoable and safe,
but it will not be the full-quality project.
