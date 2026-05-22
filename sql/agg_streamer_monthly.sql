-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- agg_streamer_monthly — streamer × calendar month
-- Includes MoM % delta. 6-month trend is computed client-side from the row history.

CREATE OR REPLACE TABLE `nf-bifrost.reporting.agg_streamer_monthly`
PARTITION BY month_start
CLUSTER BY streamer_id
AS
WITH monthly AS (
  SELECT
    streamer_id,
    DATE_TRUNC(session_date, MONTH) AS month_start,
    COUNT(*) AS stream_count,
    COUNT(DISTINCT session_date) AS active_days,
    SUM(follow_streamer_bet_count)    AS follow_streamer_bet_count,
    SUM(follow_streamer_bet_turnover) AS follow_streamer_bet_turnover,
    SUM(donation_amount_total)        AS donation_amount_total,
    SUM(recommend_bet_count)          AS recommend_bet_count,
    SUM(tip_amount)                   AS tip_amount,
    SUM(watch_seconds_total)          AS watch_seconds_total,
    SUM(viewers)                      AS viewers
  FROM `nf-bifrost.reporting.agg_session_metrics`
  GROUP BY streamer_id, month_start
),

with_prev AS (
  SELECT
    *,
    LAG(follow_streamer_bet_count) OVER w AS follow_streamer_bet_count_prev,
    LAG(donation_amount_total)     OVER w AS donation_amount_total_prev,
    LAG(watch_seconds_total)       OVER w AS watch_seconds_total_prev
  FROM monthly
  WINDOW w AS (PARTITION BY streamer_id ORDER BY month_start)
)

SELECT
  CURRENT_DATE('Asia/Taipei') AS as_of_date,
  *,
  SAFE_DIVIDE(follow_streamer_bet_count - follow_streamer_bet_count_prev,
              NULLIF(follow_streamer_bet_count_prev, 0)) AS follow_streamer_bet_count_mom,
  SAFE_DIVIDE(donation_amount_total - donation_amount_total_prev,
              NULLIF(donation_amount_total_prev, 0)) AS donation_amount_total_mom,
  SAFE_DIVIDE(watch_seconds_total - watch_seconds_total_prev,
              NULLIF(watch_seconds_total_prev, 0)) AS watch_seconds_total_mom
FROM with_prev;
