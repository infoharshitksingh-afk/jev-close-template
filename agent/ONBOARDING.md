# Close agent: onboarding

The close agent lives in your finance Slack channel and answers questions about the month-end
close workbook. When the data can't settle a question, it tags the person who owns that area. It
logs every question: the problem, what it answered, who it asked, what they said, and whether it
worked. The log exports to Excel.

Setup takes about an hour with whoever administers Slack. Nothing in this repo knows your Slack
workspace, people or tools yet. Everything specific to you goes in one file, `agent/agent.yaml`.

```
Slack thread ──► agent ──► close warehouse (read-only)   ──► answer with the query it used
                   │
                   ├──► agent.yaml: who owns what      ──► tags the owner when it can't answer
                   │
                   └──► question log (SQLite)           ──► Close_Questions_Log.xlsx
```

---

## Step 0: See it work first (5 minutes, no Slack)

```bash
pip install -r requirements.txt -r agent/requirements.txt
python jevclose.py example        # builds the synthetic example close
python -m agent replay            # plays a scripted Slack conversation against it
```

The replay shows five questions on the example close:

1. One the agent answers from the data alone.
2. A mismatch between an invoice and a contract. The agent tags both owners.
3. An owner's answer that doesn't fit the data. The asker marks it ❌, and the agent asks again with
   the evidence.
4. A billing policy question. It goes to the billing owner.
5. A repeat of question 2. The agent answers from the log instead of tagging anyone again.

The log is written to `examples/outputs/agent/Close_Questions_Log.xlsx`.

To try it live in the terminal with Claude making the decisions (needs an Anthropic API key):

```bash
export ANTHROPIC_API_KEY=...       # never paste keys into files or Slack
python -m agent chat
```

---

## Step 1: Fill in who owns what (15 minutes)

```bash
python -m agent setup
```

This walks through each area and asks for an owner and an optional backup. It writes
`agent/agent.yaml`, which is kept out of git.

| Area | Covers | Typical owner |
|---|---|---|
| `usage_data` | Logs, missing days, account IDs, time zones (checks 1-3) | Data or platform engineer |
| `billing` | Invoices, prices, free credit, billed statuses (checks 4-7) | Whoever owns Stripe or billing |
| `revenue_accounting` | ASC 606, gross vs net, deferred revenue (assumptions 5-8) | Controller or outside accountant |
| `enterprise_contracts` | Commitments, true-ups, discounts, pipeline (check 10) | GTM / account owner |
| `gateways` | OpenRouter, Vercel and other reseller statements (checks 8-9) | Partnerships |
| `forecast` | Cohorts, scenarios, forecast vs actual | Finance lead |
| `capacity` | GPUs, throughput, serving cost | Inference or infra lead |
| `use_cases` | Project labels and inferred use cases | Product / developer platform |
| close lead | Signs off the close, approves anything that changes numbers, gets areas with no owner | Head of finance |

**Finding a Slack ID.** Click the person's name, then View full profile, then ⋯, then Copy member
ID. It starts with `U`. To tag a team (for example `@billing`), use the user group ID, which starts
with `S`; a Slack admin can find it.

If an area has no owner yet, leave its ID empty. Questions for that area go to the close lead.

---

## Step 2: Connect it to Slack (pick one route)

| | Route A: our Slack app | Route B: your existing Slack agent |
|---|---|---|
| Use when | You don't have a Slack agent yet, or want this one separate | You already run an agent in Slack that can use MCP tools |
| Who answers | Claude, through the Anthropic API key you provide | Your agent, using this repo's tools |
| Setup | Create an app from the manifest, set 3 tokens, run one command | Run the MCP server, add it to your agent, give it `RULES.md` |
| Where it runs | Any machine that can see `outputs/` (start on a laptop, then a small always-on server) | Same |

### Route A: the Slack app (about 30 minutes)

1. Go to https://api.slack.com/apps, click **Create New App**, then **From an app manifest**. Pick
   the workspace and paste [`slack_manifest.yaml`](slack_manifest.yaml). It uses Socket Mode, so
   you don't need a public URL or an open port.
2. Click **Install to Workspace**. Copy the **Bot User OAuth Token** (`xoxb-...`).
3. Under **Basic Information**, go to **App-Level Tokens**, click **Generate**, and add the
   `connections:write` scope. Copy the token (`xapp-...`).
4. On the machine that runs the close, set the three tokens as environment variables:
   ```bash
   export SLACK_BOT_TOKEN=xoxb-...
   export SLACK_APP_TOKEN=xapp-...
   export ANTHROPIC_API_KEY=...
   ```
5. In Slack, invite the bot to your channel: `/invite @close-agent` in `#finance-close`.
6. Check everything:
   ```bash
   python -m agent doctor
   ```
   It confirms the close is there, every Slack ID belongs to a real person, the bot is in the
   channel, and the keys are set.
7. Start it:
   ```bash
   python -m agent slack
   ```

To keep it running, put that command under whatever you already use for long-running jobs:
systemd, a container, or a small VM. It has to run where it can read `outputs/`, because that's
where `python jevclose.py run` writes the close.

### Route B: your existing agent, over MCP

```bash
python -m agent mcp            # stdio, for an MCP client on the same machine
python -m agent mcp --http     # HTTP on 127.0.0.1:8765/mcp; put your own auth in front
```

1. Add the server to your agent's tools. Check with your Slack admin that custom MCP servers are
   allowed, and limit the agent to the finance channel.
2. Give the agent [`RULES.md`](RULES.md) as its instructions. The server also serves them as the
   `close_rules` prompt.
3. Your agent handles Slack itself, so it has to post the ✅/❌ follow-up and call
   `record_outcome`. Route A does this for you.

---

## Step 3: Run a pilot close (one month)

| When | What |
|---|---|
| Close day 1 | Run `python jevclose.py run`. The agent picks up the newest close on its own. |
| Close day 1 | The close lead asks the first 3-5 questions in the channel, so people see how it works. |
| During the close | Ask in the channel or a DM. Owners answer in the thread when tagged; no @mention needed. |
| After sign-off | Run `python -m agent export` and review `Close_Questions_Log.xlsx` together (about 20 minutes). |

What to look at after the pilot:
- **Solved rate** (Summary tab): of the answers someone rated, how many worked.
- **Agent answered alone vs needed an owner**: how much routine work it takes off people.
- **Didn't work** rows: each one is a wrong answer or a gap in the data. Fix the cause.
- **Decisions tab**: what owners settled. Move these into `config.yaml` or `LOGIC_REVIEW.md` with
  the close lead's approval, so next month starts from them.

---

## What it can and can't do

| It can | It can't |
|---|---|
| Query the close warehouse (read-only, 200 rows max) | Edit source data, `config.yaml`, the workbook, or any input cell |
| Trace a workbook cell back to its query and SQL file | Change a check's status |
| Read the 14 assumptions in `LOGIC_REVIEW.md` | Read customer request content (`state`, prompts): those queries are refused |
| Tag owners, backups and the close lead | Approve its own changes: anything that changes numbers goes to the close lead |
| Remind once if an owner hasn't answered in 24 hours | Answer outside the channels in `agent.yaml` |

**Data handling.** The warehouse and the question log stay on your machine. The thread text and
the query results the agent looks at are sent to Claude through your Anthropic API key, under your
organization's Anthropic terms. Keep the agent in finance channels, since answers name customers
and revenue. Keys stay in environment variables. `outputs/` and `agent/agent.yaml` are in
`.gitignore`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No close found" | Run `python jevclose.py run` first, or set `outputs_dir` in `agent.yaml`. |
| Bot doesn't reply | Is it invited to the channel? Is the channel listed under `slack.channels`? Run `doctor`. |
| Owner's reply is ignored | Owners must reply in the same thread, and their ID in `agent.yaml` must match. Otherwise @mention the bot. |
| "@Name (no Slack ID set)" in a message | Add that person's ID with `python -m agent setup`. |
| Wrong answer | Mark it ❌ and say what's wrong in the thread. It's logged as "didn't work", and the agent digs further or asks the owner. |

Questions about this kit: Harshit Singh.
