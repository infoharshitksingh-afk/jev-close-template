-- 40_analytics.sql
-- Everything the report needs, inside the 12-month reporting window ending at the as-of month.

CREATE OR REPLACE TABLE months AS
WITH b AS (SELECT MIN(date) AS first_date FROM usage_events)
SELECT m::DATE                                                                 AS month,
       GREATEST(0, date_diff('day', GREATEST(m::DATE, b.first_date), LEAST(last_day(m::DATE), p.as_of_end)) + 1) AS days_with_data,
       day(last_day(m::DATE))                                                  AS calendar_days
FROM p, b, generate_series(p.window_start, p.as_of_month, INTERVAL 1 MONTH) t(m);

-- Days to divide by for "per day" figures: days the platform had data, so a launch month isn't understated.
CREATE OR REPLACE TABLE month_days AS
SELECT month, CASE WHEN days_with_data > 0 THEN days_with_data ELSE calendar_days END AS days, calendar_days
FROM months;

CREATE OR REPLACE TABLE use_case_month AS
SELECT month, use_case,
       SUM(billable_tokens) AS billable_tokens,
       SUM(CASE WHEN use_case_source = 'label' THEN billable_tokens ELSE 0 END) AS tokens_from_label
FROM usage_daily, p
WHERE month >= p.window_start
GROUP BY ALL;

-- Score the inference rules on traffic whose label IS mapped.
CREATE OR REPLACE TABLE inference_accuracy AS
SELECT use_case_from_label AS use_case,
       SUM(billable_tokens) AS labelled_tokens,
       SUM(CASE WHEN use_case_inferred = use_case_from_label THEN billable_tokens ELSE 0 END) AS inferred_correctly
FROM usage_daily
WHERE use_case_from_label IS NOT NULL AND use_case_inferred <> 'unclassified'
GROUP BY ALL;

-- Self-serve cohorts inside the window. Older cohorts are combined into one line.
CREATE OR REPLACE TABLE cohort_tokens AS
SELECT u.cohort,
       date_diff('month', u.cohort, u.month) AS k,
       SUM(u.billable_tokens)                AS billable_tokens,
       COUNT(DISTINCT u.account_id)          AS active_accounts
FROM usage_daily u, p
WHERE u.segment = 'self_serve' AND u.cohort >= p.window_start AND u.month >= u.cohort
GROUP BY ALL;

CREATE OR REPLACE TABLE earlier_cohorts AS
SELECT u.month, SUM(u.billable_tokens) AS billable_tokens
FROM usage_daily u, p
WHERE u.segment = 'self_serve' AND (u.cohort < p.window_start OR u.cohort IS NULL OR u.month < u.cohort)
  AND u.month >= p.window_start
GROUP BY ALL;

CREATE OR REPLACE TABLE cohort_signups AS
SELECT a.cohort, COUNT(*) AS signups,
       SUM(CASE WHEN a.payment_method_added THEN 1 ELSE 0 END) AS added_payment_method,
       COUNT(a.payment_method_added) AS payment_flag_known
FROM accounts a, p
WHERE a.segment = 'self_serve' AND a.cohort BETWEEN p.window_start AND p.as_of_month
GROUP BY ALL;

-- Enterprise contracts live at the close, with the last 7 days' run-rate.
CREATE OR REPLACE TABLE contracts_live AS
SELECT c.customer, c.account_id, c.start_date, c.end_date, c.monthly_commit_usd, c.unit_price_per_mtok,
       COALESCE(c.discount_pct, 1 - c.unit_price_per_mtok / NULLIF((SELECT price_per_mtok FROM prices ORDER BY effective_from DESC LIMIT 1), 0)) AS discount_pct,
       (SELECT COALESCE(SUM(billable_tokens), 0) FROM usage_daily u
         WHERE u.account_id = c.account_id AND u.date BETWEEN p.as_of_end - INTERVAL 6 DAY AND p.as_of_end) / 7 / 1e9
                                                                        AS last7_btok_per_day
FROM src_contracts c, p
WHERE c.start_date <= p.as_of_end AND (c.end_date IS NULL OR c.end_date >= p.as_of_month)
UNION ALL
-- Enterprise accounts using the API with no live contract on file: forecast them at run-rate and
-- list price, as if mature (start date set a year back), so they aren't dropped from the forecast.
SELECT a.display_name || ' (no contract on file)', a.account_id, CAST(p.as_of_end - INTERVAL 365 DAY AS DATE), NULL::DATE,
       0.0, pr.price_per_mtok, 0.0, x.last7
FROM accounts a, p,
     (SELECT price_per_mtok FROM prices ORDER BY effective_from DESC LIMIT 1) pr,
     LATERAL (SELECT COALESCE(SUM(billable_tokens), 0) / 7 / 1e9 AS last7 FROM usage_daily u
              WHERE u.account_id = a.account_id AND u.date BETWEEN p.as_of_end - INTERVAL 6 DAY AND p.as_of_end) x
WHERE a.segment = 'enterprise' AND x.last7 > 0
  AND a.account_id NOT IN (SELECT account_id FROM src_contracts c2, p p2
                           WHERE c2.start_date <= p2.as_of_end AND (c2.end_date IS NULL OR c2.end_date >= p2.as_of_month))
ORDER BY 3;

CREATE OR REPLACE TABLE enterprise_month AS
SELECT k.customer, r.month, r.billable_tokens, r.usage_revenue, r.commitment, r.true_up
FROM revenue_expected r
JOIN (SELECT DISTINCT contract_id, customer FROM contract_months) k USING (contract_id), p
WHERE r.month >= p.window_start;

CREATE OR REPLACE TABLE pipeline_open AS
SELECT deal_id, customer, stage,
       CASE WHEN probability > 1 THEN probability / 100 ELSE probability END AS probability,
       expected_monthly_commit_usd, expected_start,
       COALESCE(CASE WHEN expected_discount_pct > 1 THEN expected_discount_pct / 100 ELSE expected_discount_pct END, 0) AS expected_discount_pct,
       use_cases
FROM src_pipeline
WHERE deal_id IS NOT NULL
ORDER BY expected_start;

CREATE OR REPLACE TABLE concentration AS
WITH x AS (
    SELECT r.account_id, COALESCE(a.display_name, r.account_id) AS display_name,
           MAX(r.segment) AS segment, MAX(r.channel) AS channel,
           SUM(r.billable_tokens) AS billable_tokens, SUM(r.net_revenue) AS net_revenue
    FROM revenue_expected r LEFT JOIN accounts a USING (account_id), p
    WHERE r.month = p.as_of_month
    GROUP BY r.account_id, a.display_name
)
SELECT row_number() OVER (ORDER BY net_revenue DESC, billable_tokens DESC) AS rank, *,
       net_revenue / NULLIF(SUM(net_revenue) OVER (), 0) AS share_of_revenue
FROM x;

CREATE OR REPLACE TABLE reliability AS
SELECT m.month,
       COALESCE((SELECT SUM(calls) FROM usage_daily u WHERE u.month = m.month), 0) AS calls_ok,
       COALESCE((SELECT SUM(calls) FROM errors_daily e WHERE e.month = m.month), 0) AS calls_failed
FROM months m;

-- Hourly load for the last 7 days of the as-of month (reporting time zone), zero-filled.
CREATE OR REPLACE TABLE hourly_last7 AS
WITH grid AS (
    SELECT unnest(generate_series(CAST(p.as_of_end - INTERVAL 6 DAY AS TIMESTAMP),
                                  CAST(p.as_of_end AS TIMESTAMP) + INTERVAL 23 HOUR, INTERVAL 1 HOUR)) AS hour_start
    FROM p
)
SELECT g.hour_start, COALESCE(h.tokens, 0) AS tokens
FROM grid g LEFT JOIN usage_hourly h ON CAST(h.hour_start AS TIMESTAMP) = g.hour_start
WHERE (SELECT COUNT(*) FROM usage_hourly) > 0
ORDER BY 1;

CREATE OR REPLACE TABLE lists AS
WITH ch AS (
    SELECT channel, row_number() OVER (ORDER BY SUM(billable_tokens) DESC) AS n
    FROM usage_daily, p WHERE month >= p.window_start GROUP BY channel
), uc AS (
    SELECT use_case, row_number() OVER (ORDER BY SUM(billable_tokens) DESC) AS n
    FROM use_case_month GROUP BY use_case
), ns AS (SELECT n FROM ch UNION SELECT n FROM uc)
SELECT ns.n, ch.channel, uc.use_case
FROM ns LEFT JOIN ch USING (n) LEFT JOIN uc USING (n)
ORDER BY ns.n;
