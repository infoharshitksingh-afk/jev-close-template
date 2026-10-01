-- 30_core.sql
-- Accounts, billable usage with a use case on every row, and what each account SHOULD have been
-- charged, rebuilt independently from usage. The billing system is checked against this in 50_checks.
--
-- Revenue rules (each one is a config setting; see LOGIC_REVIEW.md):
--   - Only calls whose status is in billable_statuses are billed.
--   - Price per token comes from the price list in config, by date (handles price changes).
--   - Free credit: an account's first $X of list-price usage is free (X = free_credit_usd).
--   - Enterprise: tokens at the contract's price, plus a true-up to the monthly commitment when
--     usage falls short. Contract months with zero usage still owe the commitment.
--     First and last months can be prorated.
--   - Gateways: revenue_treatment 'net' books the gateway fee as a reduction of revenue (the
--     gateway is the customer); 'gross' books full revenue and the fee as a cost.

-- ---------------------------------------------------------------- accounts
CREATE OR REPLACE TABLE accounts AS
SELECT a.account_id,
       COALESCE(a.display_name, a.account_id)                       AS display_name,
       COALESCE(lower(a.channel), 'direct')                         AS channel,
       a.signup_date,
       date_trunc('month', a.signup_date)::DATE                     AS cohort,
       CASE
           WHEN lower(a.segment) IN ('enterprise', 'self_serve', 'gateway_aggregate') THEN lower(a.segment)
           WHEN a.account_id IN (SELECT account_id FROM src_contracts)              THEN 'enterprise'
           WHEN COALESCE(lower(a.channel), 'direct') IN (SELECT channel FROM gateways WHERE aggregate) THEN 'gateway_aggregate'
           ELSE 'self_serve'
       END                                                          AS segment,
       COALESCE(a.free_credit_usd,
                CASE WHEN COALESCE(lower(a.channel), 'direct') IN (SELECT channel FROM free_credit_channels)
                     THEN p.free_credit_usd ELSE 0 END)             AS free_credit_usd,
       a.payment_method_added
FROM src_accounts a, p
WHERE a.account_id IS NOT NULL
QUALIFY row_number() OVER (PARTITION BY a.account_id ORDER BY a.signup_date NULLS LAST) = 1;

CREATE OR REPLACE TABLE label_map AS
SELECT DISTINCT lower(trim(project_label)) AS label_key, NULLIF(trim(use_case), '') AS use_case
FROM src_label_map
WHERE project_label IS NOT NULL;

-- ---------------------------------------------------------------- billable usage
CREATE OR REPLACE TABLE usage_daily AS
WITH e AS (
    SELECT e.*, e.input_tokens * 1.0 / NULLIF(e.calls, 0) AS tokens_per_call
    FROM usage_events e
    WHERE e.status IN (SELECT status FROM billable_status)
)
SELECT e.date,
       date_trunc('month', e.date)::DATE                           AS month,
       e.account_id,
       COALESCE(a.segment, 'unmatched')                            AS segment,
       COALESCE(e.channel, a.channel, 'direct')                    AS channel,
       a.cohort,
       e.model,
       e.project_label,
       lm.use_case                                                 AS use_case_from_label,
       infer_use_case(e.tokens_per_call, e.avg_questions_per_call,
                      e.max_choice_cardinality, e.offhours_call_share) AS use_case_inferred,
       COALESCE(lm.use_case, infer_use_case(e.tokens_per_call, e.avg_questions_per_call,
                      e.max_choice_cardinality, e.offhours_call_share)) AS use_case,
       CASE WHEN lm.use_case IS NOT NULL THEN 'label' ELSE 'inferred' END AS use_case_source,
       e.calls,
       e.input_tokens                                              AS billable_tokens,
       pr.price_per_mtok                                           AS list_price,
       e.input_tokens / 1e6 * pr.price_per_mtok                    AS list_value,
       e.p95_latency_ms
FROM e
ASOF LEFT JOIN prices pr ON e.date >= pr.effective_from
LEFT JOIN accounts a USING (account_id)
LEFT JOIN label_map lm ON lm.label_key = lower(trim(e.project_label));

CREATE OR REPLACE TABLE errors_daily AS
SELECT date, date_trunc('month', date)::DATE AS month, account_id, status,
       SUM(calls) AS calls, SUM(input_tokens) AS attempted_tokens
FROM usage_events
WHERE status NOT IN (SELECT status FROM billable_status)
GROUP BY ALL;

CREATE OR REPLACE TABLE account_month AS
SELECT month, account_id, segment, channel, cohort,
       SUM(billable_tokens) AS billable_tokens,
       SUM(calls)           AS calls,
       SUM(list_value)      AS list_value
FROM usage_daily
GROUP BY ALL;

-- ---------------------------------------------------------------- contracts by month
CREATE OR REPLACE TABLE contract_months AS
WITH k AS (
    SELECT c.*, COALESCE(c.contract_id, c.account_id || ':' || CAST(c.start_date AS VARCHAR)) AS cid
    FROM src_contracts c
    WHERE c.account_id IS NOT NULL AND c.start_date IS NOT NULL
),
m AS (
    SELECT k.*, unnest(generate_series(date_trunc('month', k.start_date),
                                       LEAST(date_trunc('month', COALESCE(k.end_date, p.as_of_end)), p.as_of_month),
                                       INTERVAL 1 MONTH))::DATE AS month
    FROM k, p
    WHERE k.start_date <= p.as_of_end
)
SELECT m.cid AS contract_id, m.account_id, m.customer, m.start_date, m.end_date, m.month,
       m.unit_price_per_mtok, m.monthly_commit_usd,
       m.monthly_commit_usd
         * CASE WHEN p.prorate_partial_months THEN
               (date_diff('day', GREATEST(m.month, m.start_date),
                                 LEAST(last_day(m.month), COALESCE(m.end_date, last_day(m.month)))) + 1)
               * 1.0 / day(last_day(m.month))
           ELSE 1 END                                               AS commitment
FROM m, p;

-- ---------------------------------------------------------------- expected revenue
CREATE OR REPLACE TABLE revenue_expected AS
WITH cum AS (
    SELECT am.*,
           SUM(list_value) OVER (PARTITION BY account_id ORDER BY month) AS cum_list
    FROM account_month am
),
non_contract AS (
    SELECT c.month, c.account_id, c.segment, c.channel, c.billable_tokens, c.list_value,
           CASE WHEN COALESCE(a.free_credit_usd, 0) > 0 AND c.segment <> 'enterprise'
                THEN LEAST(c.cum_list, a.free_credit_usd) - LEAST(c.cum_list - c.list_value, a.free_credit_usd)
                ELSE 0 END                                          AS free_credit_used_usd,
           pr.price_per_mtok                                        AS unit_price,
           0.0                                                      AS commitment,
           NULL::VARCHAR                                            AS contract_id
    FROM cum c
    LEFT JOIN accounts a USING (account_id)
    ASOF LEFT JOIN prices pr ON last_day(c.month) >= pr.effective_from
    WHERE NOT EXISTS (SELECT 1 FROM contract_months k WHERE k.account_id = c.account_id AND k.month = c.month)
),
contract AS (
    SELECT k.month, k.account_id, 'enterprise' AS segment,
           COALESCE(am.channel, a.channel, 'direct')               AS channel,
           COALESCE(am.billable_tokens, 0)                          AS billable_tokens,
           COALESCE(am.list_value, 0)                               AS list_value,
           0.0                                                      AS free_credit_used_usd,
           k.unit_price_per_mtok                                    AS unit_price,
           k.commitment,
           k.contract_id
    FROM contract_months k
    LEFT JOIN account_month am ON am.account_id = k.account_id AND am.month = k.month
    LEFT JOIN accounts a ON a.account_id = k.account_id
),
base AS (
    SELECT *,
           CASE WHEN contract_id IS NOT NULL THEN billable_tokens / 1e6 * unit_price
                ELSE list_value - free_credit_used_usd END          AS usage_revenue
    FROM (SELECT * FROM non_contract UNION ALL SELECT * FROM contract)
)
SELECT b.month, b.account_id, b.segment, b.channel, b.contract_id,
       b.billable_tokens,
       b.list_value,
       b.free_credit_used_usd,
       CASE WHEN b.list_value > 0 THEN b.billable_tokens * b.free_credit_used_usd / b.list_value ELSE 0 END AS free_credit_tokens,
       b.billable_tokens - CASE WHEN b.list_value > 0 THEN b.billable_tokens * b.free_credit_used_usd / b.list_value ELSE 0 END AS paid_tokens,
       b.unit_price,
       b.usage_revenue,
       b.commitment,
       GREATEST(0, b.commitment - b.usage_revenue)                 AS true_up,
       COALESCE(g.fee_pct, 0) * b.usage_revenue                    AS gateway_fee,
       b.usage_revenue + GREATEST(0, b.commitment - b.usage_revenue)
         - CASE WHEN p.gateway_revenue_treatment = 'net' THEN COALESCE(g.fee_pct, 0) * b.usage_revenue ELSE 0 END
                                                                   AS net_revenue
FROM base b
LEFT JOIN gateways g ON g.channel = b.channel, p;

CREATE OR REPLACE TABLE monthly_summary AS
SELECT month, segment, channel,
       COUNT(DISTINCT CASE WHEN billable_tokens > 0 THEN account_id END)          AS active_accounts,
       COUNT(DISTINCT CASE WHEN usage_revenue + true_up > 0 THEN account_id END)  AS paying_accounts,
       SUM(billable_tokens)        AS billable_tokens,
       SUM(free_credit_tokens)     AS free_credit_tokens,
       SUM(free_credit_used_usd)   AS free_credit_usd,
       SUM(list_value)             AS list_value,
       SUM(usage_revenue)          AS usage_revenue,
       SUM(true_up)                AS true_up,
       SUM(net_revenue)            AS net_revenue,
       SUM(gateway_fee)            AS gateway_fees
FROM revenue_expected
GROUP BY ALL;
