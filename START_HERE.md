# START HERE — Doorstep with Claude Code, from zero

Written for macOS. Windows notes are marked **(Windows)**. Do the steps in order.

---

## Step 1 — Accounts you need (10 min)

- [ ] A **paid Claude plan** (Pro or Max) or an Anthropic Console account. The free plan does
      not include Claude Code.
- [ ] A **GitHub** account.
- [ ] Your hackathon keys (from BUILD_RUNBOOK.md prep list): PayPal sandbox app, Shippo test
      key, Anthropic API key, Render account. You can add these later in Step 6.

## Step 2 — Open the Terminal

1. Press **Cmd + Space**, type **Terminal**, press **Enter**.
2. A window with a blinking cursor opens. You type commands here and press **Enter** to run them.

**(Windows)** Install WSL (Ubuntu) and use the Ubuntu terminal for everything below.

## Step 3 — Install the tools the project needs (15 min)

Copy each line, paste it into Terminal, press Enter, wait until it finishes.

1. Homebrew (an app store for developer tools):
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
   At the end it may print 2–3 "Next steps" commands. Run them too.
2. Git, Python, uv and Node:
   ```bash
   brew install git python@3.12 uv node
   ```
3. Check they work (each prints a version number):
   ```bash
   git --version && python3.12 --version && uv --version && node --version
   ```

## Step 4 — Install Claude Code (5 min)

1. Run:
   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   ```
2. **Close the Terminal window and open a new one** (so it finds the new command).
3. Check:
   ```bash
   claude --version
   ```
   If you see `command not found`, close and reopen Terminal again, or run `source ~/.zshrc`.

**(Windows, PowerShell instead of WSL):** `irm https://claude.ai/install.ps1 | iex`

## Step 5 — Create the project folder and put the files in it (10 min)

1. Create the folder and go into it:
   ```bash
   mkdir -p ~/Projects/doorstep
   cd ~/Projects/doorstep
   ```
2. Open it in Finder so you can drag files in:
   ```bash
   open .
   ```
3. Unzip **doorstep-starter.zip** and drag its contents into this folder.
4. Open the Doorstep spec doc in Claude, download it as **Markdown**, rename it
   **SPEC.md**, and put it in the `docs` folder.

When you are done, the folder must look exactly like this:

```
doorstep/
├── CLAUDE.md            ← Claude Code reads this automatically every session
├── START_HERE.md        ← this guide
├── BUILD_RUNBOOK.md     ← the schedule and the phase prompts
├── KICKOFF.md           ← older 8-day prompts (reference only)
├── SUBMISSION.md        ← Devpost submission draft
├── .env.example         ← list of keys (no real values)
├── .gitignore           ← stops secrets from being uploaded
├── docs/
│   └── SPEC.md          ← the full spec (you download this)
└── specs/
    └── ACCEPTANCE.md    ← the testable checklist
```

Tip: files starting with a dot (`.env.example`, `.gitignore`) are hidden in Finder.
Press **Cmd + Shift + .** to show them.

## Step 6 — Add your keys safely (5 min)

1. Make your private key file from the template:
   ```bash
   cp .env.example .env
   open -e .env
   ```
2. Paste your **sandbox and test** keys after each `=` and save.
3. Never share `.env`, never paste live (real-money) keys. `.gitignore` keeps `.env` off GitHub.

## Step 6b — Set up the PayPal sandbox properly (15 min)

On developer.paypal.com → **Dashboard**, check the toggle at the top left says **Sandbox**.

1. **Testing tools → Sandbox accounts → Create account.** Make 4 accounts and give each a name
   (names show up on transactions, which makes the demo readable):
   - Business, United Kingdom: "Maria's Shop (UK)"; Personal, United Kingdom: "Sam (UK buyer)"
   - Business, United States: "US Shop"; Personal, United States: "US buyer"
   Keep each buyer in the same country as its seller (mixing countries causes currency confusion).
2. **Apps & Credentials → Create app** "Doorstep UK", linked to Maria's Shop (UK).
   Copy the client ID and secret into `.env`.
3. In the app's features, turn on **Transaction search**. Save. Changes can take ~10 minutes.
4. **Webhooks → Add webhook:** URL `<your-render-url>/webhooks/paypal`, tick **all events**
   (Doorstep ignores the ones it does not need). Copy the **webhook ID** into `.env`.
   For local testing, PayPal must reach your laptop: use a tunnel such as cloudflared or ngrok.
5. Where to debug later: **Event logs** → API calls, Webhook events (with Resend), Error logs.
   Always pick the right app in the filter first.

## Step 7 — Save the starting point with Git (5 min)

```bash
git init
git add -A
git commit -m "Start: spec, acceptance criteria, runbook"
```

Then on github.com: **New repository** → name it `doorstep` → **Private** → **Create**.
GitHub shows a box called "…push an existing repository from the command line".
Copy those 3 lines into Terminal and press Enter.

## Step 8 — Start Claude Code for the first time

1. Make sure you are in the project folder:
   ```bash
   cd ~/Projects/doorstep
   claude
   ```
2. First launch only: pick a colour theme, then a browser window opens to **log in**. Approve it.
3. If it asks whether you trust this folder, choose **Yes** (it is your own folder).
4. You now see a prompt box. You talk to Claude by typing in plain English and pressing Enter.

## Step 9 — Your first message (check it understood, change nothing)

Paste this:

```
Read CLAUDE.md, BUILD_RUNBOOK.md, docs/SPEC.md and specs/ACCEPTANCE.md.
Explain back to me in 10 short bullet points what we are building, the hard rules,
and the build loop. Do not create or change any files.
```

If the summary is wrong anywhere, correct it before going on.

## Step 10 — Install the helper tools inside Claude Code (20 min)

**A. PayPal AI Toolkit (from PayPal; the webinars strongly recommend it).** It teaches Claude
PayPal's recommended APIs, connects it to the PayPal sandbox MCP server, and adds commands.
1. Inside Claude Code type `/plugin` and add the toolkit from https://github.com/paypal/ai-toolkit
   (follow its README exactly).
2. Get a sandbox access token with the client-credentials `curl` command from the README, using
   your sandbox client ID and secret.
3. Put the token in Claude Code's `settings.json` under `env`, with the exact variable name the
   README gives.
4. **Fully quit Claude Code** (not just `/clear`), start it again, then run `/paypal:setup`.
5. Tokens expire about every 9 hours. If PayPal calls start failing with 401: `/paypal:setup refresh`.

Commands you will use: `/paypal:doctor` (scans your code for PayPal mistakes; run it at the end of
every phase), `/paypal:explain-error` (paste any PayPal error), `/paypal:sandbox`,
`/paypal:test-accounts`.

**B. APIMatic Context Plugin (sponsor prize).** Open the APIMatic page in the hackathon's
Resources, follow its "Resource info page" install command, and fill in the free-subscription
form on that page. It guides Claude when using the official PayPal Server SDK.

**C. Bryntum skill + MCP server.** Paste this into Claude Code:

```
Help me set up the Bryntum tools. Show each command and wait for my yes:
1. Install the Bryntum AI agent skill from https://github.com/bryntum/skills
2. Add the Bryntum MCP server (https://mcp.bryntum.com)
Then check that Python 3.12, uv, node and git work from this folder.
```

**D. AG Studio.** Get the free 45-day trial licence (AG Grid page in the hackathon's Resources)
and put the key in `.env` as `AG_GRID_LICENSE_KEY`. The PayPal + AG Studio boilerplate is
https://github.com/paypaldev/hackathon-paypal-ag-grid-boilerplate — Claude will use it on build day 2.

## Step 11 — Build, one phase at a time

Open **BUILD_RUNBOOK.md** and follow the schedule. For every phase:

1. Press **Shift + Tab** until you see **plan mode**. Claude will plan first and change nothing.
2. Paste the phase prompt from BUILD_RUNBOOK.md (A → B → C → D → E).
3. Read the plan. Reply with corrections, or "Approved, go ahead."
4. Claude asks permission before editing files or running commands. Read the request and
   answer **yes** or **no**. When in doubt, say no and ask why.
5. Before calling a phase green, run `/paypal:doctor` and fix what it reports.
6. After **phase B** (tests) and your approval, lock the tests:
   ```bash
   git add -A && git commit -m "Lock tests: core" && git tag tests-locked-core
   ```
   (Run this in a **second** Terminal tab: **Cmd + T**, then `cd ~/Projects/doorstep`.)
7. When a phase is green, type `/clear` to start the next phase with a fresh memory.
   Your files, CLAUDE.md and the specs keep all the important context.

Useful keys inside Claude Code:

| Key or command | What it does |
|---|---|
| Shift + Tab | Switch modes (plan mode = plan only) |
| Esc | Stop Claude right now |
| `/clear` | Start a fresh conversation in the same folder |
| `/login` | Log in again or switch account |
| `/paypal:doctor` | Check the PayPal integration for mistakes |
| `/paypal:setup refresh` | Fix 401 errors after the ~9-hour token expiry |

## Step 12 — Two Claude sessions at once (build day 2 only)

So the frontend and backend don't overwrite each other, give the second session its own copy:

```bash
cd ~/Projects/doorstep
git worktree add ../doorstep-web -b web
cd ../doorstep-web
claude
```

When both are green, ask one session to merge the `web` branch and run all tests.

## Step 13 — If something goes wrong

| Problem | Fix |
|---|---|
| `claude: command not found` | Close and reopen Terminal, or `source ~/.zshrc` |
| Login page doesn't open | Allow pop-ups for claude.ai, or copy the link it prints into your browser |
| Claude changed a locked test | `git checkout tests-locked-core -- tests/` then tell Claude it may not edit tests |
| Everything is broken | `git log --oneline` to find the last green commit, then ask Claude to return to it |
| PayPal returns 401 | Run `/paypal:setup refresh` (token expired) |
| A PayPal error you don't understand | `/paypal:explain-error` and paste it |
| PayPal or Shippo behaves strangely | Paste the error to Claude: "Compare with SPEC section 9 and propose the fallback from section 17" |

## Every day, in one line

Open Terminal → `cd ~/Projects/doorstep` → `claude` → plan mode → phase prompt → approve →
tests green → commit and tag → `/clear` → next phase → end-of-day prompt.
