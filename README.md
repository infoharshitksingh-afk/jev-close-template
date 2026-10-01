# Monthly usage close: API logs → SQL → Excel

> **Independent work sample, not affiliated with or endorsed by TypeSafe AI. All data in this repository is synthetic.**

A template for closing the month at a usage-based AI API company. Point it at your API logs and
business data; it runs a set of SQL queries, checks that usage, billing, contracts and partner
statements tie, forecasts demand, sizes GPU capacity, and fills an Excel board pack.

The Excel workbook is **formulas only**. Numbers enter through grey `Data_` tabs; every report
tab reads them. So any figure can be traced to a data row and from there to a query in `/sql`,
and a team without Python can paste query results into the `Data_` tabs by hand.

Built around TypeSafe's Jev API (`usage.input_tokens` billing, $ per million input tokens,
429/529 errors), but nothing in it is specific to one provider.

## Try it on example data (2 minutes)

```bash
pip install -r requirements.txt
python jevclose.py example
```

This generates synthetic data and runs a full close into `examples/outputs/`. Open
`Close_2027-02.xlsx`. The example data has three planted errors; the Checks tab finds them.

## Use it on your data

```bash
python jevclose.py setup      # asks where each source lives and maps your columns
python jevclose.py validate   # loads everything and reports what it found
python jevclose.py run        # closes the last complete month (or --as-of 2027-02)
```

`setup` walks through each source. For each one, give a **file path or glob** (CSV, JSON Lines,
Parquet, optionally gzipped), or a **database URL plus a SELECT query**. It reads the data,
suggests a column for each standard field, and writes `config.yaml`. Database passwords stay out
of the file: write `${WAREHOUSE_URL}` and set the environment variable.

### Sources

| Source | Needed? | What it unlocks |
|---|---|---|
| `usage` | **Required** | Everything. One row per API call (raw logs) or per day per account (a rollup). |
| `accounts` | **Required** | Segments, cohorts, free credit. |
| `contracts` | Optional | Enterprise tab, true-ups, enterprise forecast. |
| `pipeline` | Optional | Pipeline part of the forecast. |
| `billing_ledger` | Optional | Billing checks: tokens billed, prices, free credit, missing invoices. |
| `gateway_statements` | Optional | Reseller reconciliation. |
| `hourly` | Optional | Peak vs base-load sizing, if usage is a daily rollup. |
| `label_map` | Optional | Maps customer project labels to use cases. A starter file is in `reference/`. |

Field lists for each source are in `lib/contract.py`. Missing optional sources don't break
anything: their checks show as SKIP.

### Typical source queries

Raw call logs in a warehouse:

```sql
SELECT created_at, org_id, status_code, usage_input_tokens, request_id, source, model, project
FROM analytics.systemone_requests
WHERE created_at >= DATE '2025-09-01'
```

Accounts from the application database:

```sql
SELECT id, name, created_at, plan, signup_source, has_payment_method FROM orgs
```

Pull at least 12 months plus one (the report window, plus the prior close for the forecast check).

## What runs, in order

```
sources (files / database)
  → sql/10_usage_*.sql        roll calls up to days, in the reporting time zone
  → sql/20_use_case_rules.sql infer use case from traffic shape (edit for your traffic)
  → sql/30_core.sql           accounts, billable usage, expected revenue
  → sql/40_analytics.sql      cohorts, enterprise, concentration, hourly load
  → sql/50_checks.sql         12 reconciliation and data-quality checks
  → Data_ tabs in template/Jev_Close_Template.xlsx  →  outputs/Close_YYYY-MM.xlsx
```

Each run also writes `outputs/close_YYYY-MM.json` (checks and forecast, for alerts or an agent),
CSVs of every `Data_` tab, and `forecast_history.csv`, which next month's close uses to compare
actuals with this month's forecast.

## The workbook

| Tab | Answers |
|---|---|
| Summary | Six-month KPIs, close status, forecast, capacity |
| Checks | Do usage, billing, contracts and partner statements tie? |
| Monthly | Tokens and revenue by segment and channel |
| UseCases | Where usage comes from, and how reliable the inferred use cases are |
| Cohorts | How each signup month's usage grows and retains |
| Enterprise | Usage vs commitment by contract, open pipeline |
| Concentration | Dependence on the largest accounts |
| Forecast, Forecast_Ent | Next three months, low / base / high, plus last month's forecast accuracy |
| Capacity | GPUs to reserve vs rent, cost of each strategy, serving margin |

Blue cells are judgment inputs (forecast and capacity), seeded from `config.yaml`. Change them in
Excel to test scenarios.

## Before trusting the numbers

Read **[LOGIC_REVIEW.md](LOGIC_REVIEW.md)**. It lists every rule that changed from the original
demo, and the 14 assumptions the team must confirm: which tokens are billed, which statuses are
billed, how free credit and gateway revenue are booked, and more.

## Automating it

**[AGENT_BRIEF.md](AGENT_BRIEF.md)** is a brief for an AI agent running the close: it runs the
pipeline, investigates each flagged check with read-only SQL, drafts explanations in the workbook
and a memo, and stops for a person to review. It never edits source data or check results.

## Security

- `outputs/` holds customer names and revenue. It is in `.gitignore`; keep it there.
- Keep credentials in environment variables, never in `config.yaml`.
- The pipeline reads usage metadata only. It never needs customer request content.

## Tests

```bash
python tests/run_tests.py
```

Runs the example, a call-level close reading accounts from a SQL database, and a close with only
the two required sources. If LibreOffice is installed, it also checks that the Excel formulas
reproduce the Python forecast.

## License

MIT. See [LICENSE](LICENSE).
