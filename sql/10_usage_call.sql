-- 10_usage_call.sql  (used when usage is one row per API call)
-- Roll raw call logs up to one row per local day x account x channel x model x project x status,
-- keeping the traffic-shape features the use-case rules need. Also builds hourly load.
-- Times are converted to the reporting time zone in config (p.tz).

CREATE OR REPLACE TABLE usage_calls AS
SELECT timezone(p.tz, u.ts)                            AS local_ts,
       u.account_id,
       lower(u.channel)                                 AS channel,
       u.model,
       u.project_label,
       u.status,
       u.request_id,
       u.input_tokens,
       COALESCE(u.output_tokens, 0)                     AS output_tokens,
       u.questions,
       u.max_choice_cardinality,
       u.latency_ms
FROM src_usage u, p
WHERE u.ts IS NOT NULL
  AND CAST(timezone(p.tz, u.ts) AS DATE) <= p.as_of_end;

CREATE OR REPLACE TABLE usage_events AS
SELECT CAST(local_ts AS DATE)                           AS date,
       account_id, channel, model, project_label, status,
       COUNT(*)                                         AS calls,
       SUM(input_tokens)                                AS input_tokens,
       SUM(output_tokens)                               AS output_tokens,
       AVG(questions)                                   AS avg_questions_per_call,
       MAX(max_choice_cardinality)                      AS max_choice_cardinality,
       AVG(CASE WHEN hour(local_ts) < (SELECT offhours_end_hour FROM p) THEN 1.0 ELSE 0.0 END) AS offhours_call_share,
       quantile_cont(latency_ms, 0.95)                  AS p95_latency_ms
FROM usage_calls
GROUP BY ALL;

CREATE OR REPLACE TABLE usage_hourly AS
SELECT date_trunc('hour', local_ts)                    AS hour_start,
       SUM(input_tokens)                                AS tokens
FROM usage_calls
WHERE status IN (SELECT status FROM billable_status)
GROUP BY 1;

-- Data-quality inputs for the checks.
CREATE OR REPLACE TABLE dq_duplicates AS
SELECT COUNT(*) - COUNT(DISTINCT request_id)            AS duplicate_rows,
       COUNT(request_id)                                AS rows_with_id
FROM usage_calls;
