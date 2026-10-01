-- 50_checks.sql
-- Reconciliation and data-quality checks. Each returns rows with a status:
--   PASS  the numbers tie          FLAG  a person needs to look
--   INFO  not an error, but leadership should know      SKIP  the source isn't connected
-- These are fixed rules: an agent may explain a FLAG, never change one.

CREATE OR REPLACE TABLE checks AS
WITH win AS (SELECT month FROM months),
loaded AS (SELECT source FROM source_status WHERE loaded),
ledger AS (SELECT l.* FROM src_billing_ledger l WHERE l.month IN (SELECT month FROM win)),
exp_acct AS (
    SELECT month, account_id, SUM(billable_tokens) AS billable_tokens, SUM(usage_revenue) AS usage_revenue,
           MAX(unit_price) AS unit_price, MAX(segment) AS segment, MAX(channel) AS channel
    FROM revenue_expected GROUP BY ALL
),
c1 AS (
    SELECT 1 AS check_no, 'No duplicate request IDs in call logs' AS check_name, 'all calls to close' AS scope,
           0.0 AS expected, CAST(d.duplicate_rows AS DOUBLE) AS actual, 0.0 AS usd_impact,
           CASE WHEN d.rows_with_id = 0 THEN 'SKIP' WHEN d.duplicate_rows = 0 THEN 'PASS' ELSE 'FLAG' END AS status,
           CASE WHEN d.rows_with_id = 0 THEN 'Needs call-level logs with request_id'
                ELSE d.duplicate_rows || ' duplicate rows: the same call logged twice would be billed twice' END AS detail
    FROM dq_duplicates d
),
c2 AS (
    SELECT 2, 'No missing days in usage data', MIN(date) || ' to ' || (SELECT as_of_end FROM p),
           CAST(date_diff('day', MIN(date), (SELECT as_of_end FROM p)) + 1 AS DOUBLE), CAST(COUNT(DISTINCT date) AS DOUBLE), 0.0,
           CASE WHEN COUNT(DISTINCT date) = date_diff('day', MIN(date), (SELECT as_of_end FROM p)) + 1 THEN 'PASS' ELSE 'FLAG' END,
           'Days with usage vs days in period. A gap usually means a failed export, not a quiet day.'
    FROM usage_events
),
c3 AS (
    SELECT 3, 'All usage belongs to a known account', 'reporting window',
           0.0, COALESCE(SUM(billable_tokens), 0), COALESCE(SUM(list_value), 0),
           CASE WHEN COALESCE(SUM(billable_tokens), 0) = 0 THEN 'PASS' ELSE 'FLAG' END,
           COUNT(DISTINCT account_id) || ' account IDs in usage are missing from the accounts source'
    FROM usage_daily, p WHERE segment = 'unmatched' AND month >= p.window_start
),
c4 AS (
    SELECT 4, 'Billed tokens tie to billable usage', strftime(l.month, '%Y-%m'),
           SUM(COALESCE(e.billable_tokens, 0)), SUM(l.tokens_billed),
           SUM((l.tokens_billed - COALESCE(e.billable_tokens, 0)) / 1e6 * l.unit_price_per_mtok),
           CASE WHEN ABS(SUM(l.tokens_billed) - SUM(COALESCE(e.billable_tokens, 0)))
                     <= (SELECT tol_billed_pct FROM p) * GREATEST(SUM(COALESCE(e.billable_tokens, 0)), 1)
                THEN 'PASS' ELSE 'FLAG' END,
           COUNT(CASE WHEN l.tokens_billed <> COALESCE(e.billable_tokens, 0) THEN 1 END) || ' accounts billed for a different number of tokens than billable usage'
    FROM ledger l LEFT JOIN exp_acct e USING (account_id, month)
    GROUP BY l.month
),
c5 AS (
    SELECT 5, 'Every account with revenue has an invoice', strftime(e.month, '%Y-%m'),
           0.0, CAST(COUNT(*) AS DOUBLE), SUM(e.usage_revenue),
           CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FLAG' END,
           COUNT(*) || ' accounts with chargeable usage and no invoice (gateway accounts excluded)'
    FROM exp_acct e
    WHERE e.month IN (SELECT month FROM win) AND e.usage_revenue > 0.005
      AND e.segment <> 'gateway_aggregate' AND e.channel NOT IN (SELECT channel FROM gateways)
      AND NOT EXISTS (SELECT 1 FROM src_billing_ledger l WHERE l.account_id = e.account_id AND l.month = e.month)
      AND 'billing_ledger' IN (SELECT source FROM loaded)
    GROUP BY e.month
),
c6 AS (
    SELECT 6, 'Invoices use the right unit price', strftime(l.month, '%Y-%m'),
           SUM(e.unit_price * COALESCE(l.paid_tokens, l.tokens_billed) / 1e6), SUM(l.usage_charge_usd),
           SUM(l.usage_charge_usd - e.unit_price * COALESCE(l.paid_tokens, l.tokens_billed) / 1e6),
           CASE WHEN COUNT(CASE WHEN ABS(l.unit_price_per_mtok - e.unit_price) > (SELECT tol_price FROM p) THEN 1 END) = 0
                THEN 'PASS' ELSE 'FLAG' END,
           COALESCE(string_agg(CASE WHEN ABS(l.unit_price_per_mtok - e.unit_price) > (SELECT tol_price FROM p)
                    THEN COALESCE(a.display_name, l.account_id) || ' at $' || round(l.unit_price_per_mtok, 5)
                         || '/M vs expected $' || round(e.unit_price, 5) || '/M' END, '; '),
                    'All invoices at the expected price')
    FROM ledger l JOIN exp_acct e USING (account_id, month) LEFT JOIN accounts a USING (account_id)
    GROUP BY l.month
),
c7 AS (
    SELECT 7, 'Free credit used stays within each account''s grant', 'all accounts',
           MAX(a.free_credit_usd), MAX(x.used_usd), 0.0,
           CASE WHEN COUNT(CASE WHEN x.used_usd > a.free_credit_usd + 0.01 THEN 1 END) = 0 THEN 'PASS' ELSE 'FLAG' END,
           COUNT(CASE WHEN x.used_usd > a.free_credit_usd + 0.01 THEN 1 END) || ' accounts used more free credit than granted'
    FROM (SELECT account_id, SUM(free_credit_tokens / 1e6 * (SELECT price_per_mtok FROM prices ORDER BY effective_from DESC LIMIT 1)) AS used_usd
          FROM src_billing_ledger WHERE free_credit_tokens IS NOT NULL GROUP BY 1) x
    JOIN accounts a USING (account_id)
    HAVING COUNT(*) > 0
),
gw_usage AS (SELECT channel, month, SUM(billable_tokens) AS tokens FROM usage_daily GROUP BY ALL),
c8 AS (
    SELECT 8, 'Gateway statement matches our logs', g.gateway || ' ' || strftime(g.month, '%Y-%m'),
           COALESCE(u.tokens, 0), g.tokens_reported,
           (g.tokens_reported - COALESCE(u.tokens, 0)) / 1e6 * pr.price_per_mtok * (1 - COALESCE(gw.fee_pct, 0)),
           CASE WHEN ABS(g.tokens_reported - COALESCE(u.tokens, 0)) <= (SELECT tol_gateway_pct FROM p) * GREATEST(COALESCE(u.tokens, 0), 1)
                THEN 'PASS' ELSE 'FLAG' END,
           'Statement is ' || round(100.0 * (g.tokens_reported - COALESCE(u.tokens, 0)) / GREATEST(COALESCE(u.tokens, 0), 1), 2) || '% vs our logs'
    FROM src_gateway_statements g
    LEFT JOIN gw_usage u ON u.channel = lower(g.gateway) AND u.month = g.month
    LEFT JOIN gateways gw ON gw.channel = lower(g.gateway)
    ASOF LEFT JOIN prices pr ON last_day(g.month) >= pr.effective_from
    WHERE g.month IN (SELECT month FROM win)
),
c9 AS (
    SELECT 9, 'Gateway remittance = gross less agreed fee', gateway || ' ' || strftime(s.month, '%Y-%m'),
           s.gross_usd * (1 - COALESCE(gw.fee_pct, s.fee_pct)), s.net_remitted_usd,
           s.net_remitted_usd - s.gross_usd * (1 - COALESCE(gw.fee_pct, s.fee_pct)),
           CASE WHEN ABS(s.net_remitted_usd - s.gross_usd * (1 - COALESCE(gw.fee_pct, s.fee_pct))) <= (SELECT tol_remit_usd FROM p)
                THEN 'PASS' ELSE 'FLAG' END,
           'Agreed fee ' || round(100 * COALESCE(gw.fee_pct, s.fee_pct), 2) || '% (config), statement fee ' || COALESCE(round(100 * s.fee_pct, 2) || '%', 'n/a')
    FROM src_gateway_statements s LEFT JOIN gateways gw ON gw.channel = lower(s.gateway)
    WHERE s.gross_usd IS NOT NULL AND s.net_remitted_usd IS NOT NULL AND s.month IN (SELECT month FROM win)
),
c10 AS (
    SELECT 10, 'Enterprise usage below commitment', k.customer || ' ' || strftime(r.month, '%Y-%m'),
           r.commitment, r.usage_revenue, r.true_up, 'INFO',
           'Usage at ' || round(100 * r.usage_revenue / NULLIF(r.commitment, 0)) || '% of commitment; true-up owed. Revenue is protected this month; renewal is the risk.'
    FROM revenue_expected r JOIN (SELECT DISTINCT contract_id, customer, start_date FROM contract_months) k USING (contract_id)
    WHERE r.true_up > 0.005 AND r.month IN (SELECT month FROM win) AND r.month > date_trunc('month', k.start_date)
),
c11 AS (
    SELECT 11, 'Top-3 customer concentration', strftime((SELECT as_of_month FROM p), '%Y-%m'),
           (SELECT concentration_threshold FROM p), COALESCE(SUM(share_of_revenue), 0), 0.0,
           CASE WHEN SUM(share_of_revenue) > (SELECT concentration_threshold FROM p) THEN 'INFO' ELSE 'PASS' END,
           'Top 3 accounts = ' || round(100 * COALESCE(SUM(share_of_revenue), 0)) || '% of net revenue'
    FROM concentration WHERE rank <= 3
),
c12 AS (
    SELECT 12, 'Use case known for most usage', strftime((SELECT as_of_month FROM p), '%Y-%m'),
           (SELECT unclassified_threshold FROM p),
           COALESCE(SUM(CASE WHEN use_case = 'unclassified' THEN billable_tokens END), 0) / NULLIF(SUM(billable_tokens), 0), 0.0,
           CASE WHEN COALESCE(SUM(CASE WHEN use_case = 'unclassified' THEN billable_tokens END), 0) / NULLIF(SUM(billable_tokens), 0)
                     > (SELECT unclassified_threshold FROM p) THEN 'INFO' ELSE 'PASS' END,
           'Share of tokens with no label and no inferred use case'
    FROM usage_daily WHERE month = (SELECT as_of_month FROM p)
),
skips AS (
    SELECT * FROM (VALUES
        (4, 'Billed tokens tie to billable usage', 'billing_ledger'),
        (5, 'Every account with revenue has an invoice', 'billing_ledger'),
        (6, 'Invoices use the right unit price', 'billing_ledger'),
        (7, 'Free credit used stays within each account''s grant', 'billing_ledger'),
        (8, 'Gateway statement matches our logs', 'gateway_statements'),
        (9, 'Gateway remittance = gross less agreed fee', 'gateway_statements'),
        (10, 'Enterprise usage below commitment', 'contracts')) t(no, name, src)
    WHERE src NOT IN (SELECT source FROM loaded)
)
SELECT * FROM c1 UNION ALL SELECT * FROM c2 UNION ALL SELECT * FROM c3 UNION ALL SELECT * FROM c4
UNION ALL SELECT * FROM c5 UNION ALL SELECT * FROM c6 UNION ALL SELECT * FROM c7 UNION ALL SELECT * FROM c8
UNION ALL SELECT * FROM c9 UNION ALL SELECT * FROM c10 UNION ALL SELECT * FROM c11 UNION ALL SELECT * FROM c12
UNION ALL
SELECT no, name, 'n/a', NULL, NULL, 0.0, 'SKIP', 'Source not connected: ' || src FROM skips;

-- Checks 5 with no rows when the ledger exists means PASS: add an explicit PASS row so it's visible.
INSERT INTO checks
SELECT 5, 'Every account with revenue has an invoice', 'reporting window', 0.0, 0.0, 0.0, 'PASS',
       'Every account with chargeable usage has an invoice'
WHERE 'billing_ledger' IN (SELECT source FROM source_status WHERE loaded)
  AND NOT EXISTS (SELECT 1 FROM checks WHERE check_no = 5);
