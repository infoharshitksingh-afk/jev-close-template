# Agent brief: monthly close

You help the finance team close the month. The pipeline does the arithmetic; your job is to run
it, investigate what it flags, and draft explanations a person will review.

## Rules

1. **Never edit source data or `config.yaml`.** If a source is wrong, say so and stop.
2. **Never change a check's status.** Checks in `sql/50_checks.sql` are fixed rules. You explain
   a FLAG; you don't decide whether it passed.
3. **Never change blue input cells** on Forecast or Capacity without saying which and why.
4. **Usage numbers and metadata only.** Customer request content (`state`) must never be
   requested, read or stored.
5. **Nothing leaves the outputs folder without a person's sign-off.** You draft; finance approves.

## Steps

1. Run `python jevclose.py validate`. If a required source fails or shows warnings, stop and report.
2. Run `python jevclose.py run`. Note the as-of month and the workbook path it prints.
3. Read `outputs/close_<YYYY-MM>.json`. For every check with status `FLAG`:
   - Investigate in `outputs/warehouse.duckdb` (read-only): which accounts, days, prices.
     Useful tables: `usage_daily`, `errors_daily`, `revenue_expected`, `src_billing_ledger`,
     `src_gateway_statements`, `contract_months`, `accounts`.
   - In the workbook's Checks tab, fill column K (Explanation) on that row: two sentences, what
     happened and the evidence. Suggest an owner in column I and a next step in column J.
   - Leave column L (Reviewed by) empty.
4. For each `INFO` row, add one line in column K on whether it needs action.
5. On the Forecast tab, read the variance table. For any `FLAG`, explain the gap from its
   components and inputs.
6. Write `outputs/close_memo_<YYYY-MM>_DRAFT.md`:
   - Tokens per day, net revenue and run-rate, with the change from last month.
   - Anything that changes revenue or cash: flagged checks with $ impact, true-ups.
   - The forecast range for next month and what drives the spread.
   - The capacity recommendation and the serving margin it implies.
   - Open questions, each naming who should answer it.
7. Stop. Report what you did and anything you couldn't explain.

## Example queries

```sql
-- Accounts billed for tokens that weren't billable usage in a month
SELECT l.account_id, l.tokens_billed - r.billable_tokens AS extra_tokens,
       (l.tokens_billed - r.billable_tokens) / 1e6 * l.unit_price_per_mtok AS overbilled_usd
FROM src_billing_ledger l
JOIN (SELECT account_id, month, SUM(billable_tokens) AS billable_tokens
      FROM revenue_expected GROUP BY ALL) r USING (account_id, month)
WHERE l.month = DATE '2026-12-01' AND l.tokens_billed <> r.billable_tokens
ORDER BY overbilled_usd DESC;

-- Did failed calls cluster on particular days? (a billing meter counting 529s is a common cause)
SELECT date, status, SUM(calls) FROM errors_daily
WHERE month = DATE '2026-12-01' GROUP BY ALL ORDER BY 1, 2;
```
