-- 10_usage_daily.sql  (used when usage is already a daily rollup)
-- Standardize the rollup. Hourly load comes from the optional 'hourly' source instead.

CREATE OR REPLACE TABLE usage_events AS
SELECT u.date,
       u.account_id,
       lower(u.channel)                                 AS channel,
       u.model,
       u.project_label,
       u.status,
       u.calls,
       u.input_tokens,
       COALESCE(u.output_tokens, 0)                     AS output_tokens,
       u.avg_questions_per_call,
       u.max_choice_cardinality,
       u.offhours_call_share,
       u.p95_latency_ms
FROM src_usage u, p
WHERE u.date IS NOT NULL AND u.date <= p.as_of_end;

CREATE OR REPLACE TABLE usage_hourly AS
SELECT timezone(p.tz, h.hour_start) AS hour_start, SUM(h.tokens) AS tokens
FROM src_hourly h, p
GROUP BY 1;

-- No request IDs in a rollup, so the duplicate check is skipped.
CREATE OR REPLACE TABLE dq_duplicates AS SELECT NULL::BIGINT AS duplicate_rows, 0::BIGINT AS rows_with_id;
