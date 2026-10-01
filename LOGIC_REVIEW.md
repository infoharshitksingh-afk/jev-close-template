# Logic review

This template started as a demo built on synthetic data. Before handing it to a team with real
data, every rule was re-checked against one question: *does it still hold when the data is real?*
This file records what changed, and what the team has to confirm before trusting the numbers.

## What changed from the demo, and why

| # | Demo did | Template does | Why it matters |
|---|---|---|---|
| 1 | Hard-coded $0.042, $5 credit, gateway fees, status 200 | All in `config.yaml` | Prices and terms change; nothing should need a code edit. |
| 2 | One price forever | Price list with effective dates | A price change mid-year would otherwise restate all history. |
| 3 | Free credit counted in tokens | Counted in dollars | Token-based credit breaks the moment the price changes. |
| 4 | Enterprise revenue only in months with usage | Every contract month owes its commitment, even at zero usage; last month can be prorated | The demo would understate revenue for a customer who stopped using the API. |
| 5 | Usage joined to accounts with an inner join | Unknown accounts kept as `unmatched` and flagged (check 3) | The demo silently dropped tokens from accounts missing in the accounts table. |
| 6 | Enterprise forecast covered contracts only | Enterprise accounts with no contract on file are forecast at run-rate | In testing without a contracts export, the forecast came out about 3.5x too low. |
| 7 | Launch month hard-coded as 16 days | Days counted from the data | Works for any start date and for data that begins mid-month. |
| 8 | Named gateways (Vercel, OpenRouter, DigitalOcean) | Any gateway list; `passes_customer_ids` decides whether its users get cohorts | New resellers need only a config line. |
| 9 | All cohorts assumed to fit the window | Cohorts older than 12 months combined and projected with the tail factor | After a year, the oldest customers would otherwise vanish from the forecast. |
| 10 | Variance recomputed last month's forecast | Each close stores its forecast; next close compares against it | A recomputed forecast isn't what leadership actually saw. The first close reconstructs the prior one (without pipeline). |
| 11 | Raw-log rollup check on one sample | Rollup done by the pipeline; new checks for duplicate request IDs, missing days, unknown accounts, revenue without an invoice; SKIP when a source isn't connected | Checks now test the real failure modes of a logging pipeline. |
| 12 | UTC dates | Calls converted to the reporting time zone | A month boundary in UTC isn't the business's month. |
| 13 | Use-case rules inline | Rules in `sql/20_use_case_rules.sql`, scored on labelled traffic | The thresholds were fit to synthetic data and must be recalibrated. |
| 14 | Workbook values written by Python | Report tabs are formulas only; numbers enter through `Data_` tabs | Anyone can audit a number, and query results can be pasted by hand. |

Regression check: on the example data the template reproduces the demo's forecast exactly
(248.6B tokens/day base case for March) and catches the same three planted errors.

## What the team must confirm

These are assumptions the code can't verify. Each one has a config setting or a file to edit.

**Billing**
1. **The token field in the logs is the billed number.** Use the API's `usage.input_tokens`, not
   the request length. A live test found Jev bills roughly 2x the request size (about 180 tokens
   per call plus 1.6x the content), so request length would understate revenue by half.
2. **Which statuses are billed** (`billing.billable_statuses`). The default bills only 200.
   Check timeouts, partial failures and validation errors with whoever owns billing.
3. **Price history** (`billing.prices`). Mid-month price changes: the invoice-price check
   compares against the month-end price, so a mid-month change can raise a false FLAG.
4. **Free credit**: grant size, which channels get it, and whether it expires. Expiry
   (breakage) isn't modelled.

**Accounting** (these change reported revenue; confirm with the accountants)
5. **Revenue = usage in the month.** This fits usage-based billing under ASC 606, but prepaid
   credits, deferred revenue and cash timing aren't modelled. Runway needs cash, not revenue.
6. **Free credit is shown as zero revenue** on the usage it covers. Some companies book it at
   list value with an offsetting marketing expense instead.
7. **Commitments are monthly.** Annual commitments with a year-end true-up are not supported;
   they would be recognized differently.
8. **Gateways: net or gross** (`gateway_revenue_treatment`). Net treats the gateway as the
   customer. The right answer depends on each gateway agreement.

**Data**
9. **Accounts table**: one row per account, with a reliable signup date. The segment column is
   optional; without it, accounts with a contract are enterprise and the rest self-serve.
10. **Channel names** must match between usage, accounts and the `gateways` block. `validate`
    lists channels that will be treated as direct sales.
11. **Hourly load** comes from call-level timestamps, or the optional `hourly` source when usage
    is a daily rollup. Without either, Capacity uses the override ratios and can't price
    strategies.

**Forecast and capacity**
12. **Chain-ladder needs history.** With fewer than about six monthly cohorts, the development
    factors rest on a handful of points. Read the low-high range, not the base line.
13. **Use-case rules** in `sql/20_use_case_rules.sql` were set on synthetic data. Recalibrate them
    against the accuracy table on the UseCases tab before quoting use-case shares.
14. **Serving throughput** (`capacity.gpu_tokens_per_sec`) is a placeholder from an outside-in
    model. Engineering should supply measured tokens per second per GPU, per model version.

## Limits of the template

- 12-month window; up to 40 contracts, 40 pipeline deals, 8 channels, 15 use cases, 150 check
  rows. Change the constants in `lib/schema.py` and run `python jevclose.py template` to raise them.
- One reporting currency (USD).
- Inference compute only on the Capacity tab: training, research, networking and staff are excluded.
