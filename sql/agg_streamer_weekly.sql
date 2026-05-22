-- agg_streamer_weekly — streamer × ISO week (Mon–Sun)
-- Includes 4-week rolling median and WoW % delta.

CREATE OR REPLACE TABLE `__PROJECT__.reporting.agg_streamer_weekly`
PARTITION BY iso_week_start
CLUSTER BY streamer_id
AS
WITH weekly AS (
  SELECT
    streamer_id,
    DATE_TRUNC(session_date, ISOWEEK) AS iso_week_start,
    COUNT(*) AS stream_count,
    COUNT(DISTINCT session_date) AS active_days,
    SUM(follow_streamer_bet_count)    AS follow_streamer_bet_count,
    SUM(follow_streamer_bet_turnover) AS follow_streamer_bet_turnover,
    SUM(donation_amount_total)        AS donation_amount_total,
    SUM(recommend_bet_count)          AS recommend_bet_count,
    SUM(tip_amount)                   AS tip_amount,
    SUM(watch_seconds_total)          AS watch_seconds_total,
    SUM(viewers)                      AS viewers
  FROM `__PROJECT__.reporting.agg_session_metrics`
  GROUP BY streamer_id, iso_week_start
),

with_rolling AS (
  SELECT
    *,
    -- 4-week rolling median (excludes current week)
    PERCENTILE_CONT(follow_streamer_bet_count, 0.5) OVER w4 AS follow_streamer_bet_count_med4,
    PERCENTILE_CONT(donation_amount_total,    0.5) OVER w4 AS donation_amount_total_med4,
    PERCENTILE_CONT(watch_seconds_total,      0.5) OVER w4 AS watch_seconds_total_med4,
    LAG(follow_streamer_bet_count) OVER w1 AS follow_streamer_bet_count_prev,
    LAG(donation_amount_total)     OVER w1 AS donation_amount_total_prev
  FROM weekly
  WINDOW
    w4 AS (PARTITION BY streamer_id ORDER BY iso_week_start
           ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING),
    w1 AS (PARTITION BY streamer_id ORDER BY iso_week_start)
)

SELECT
  CURRENT_DATE('Asia/Taipei') AS as_of_date,
  *,
  SAFE_DIVIDE(follow_streamer_bet_count - follow_streamer_bet_count_prev,
              NULLIF(follow_streamer_bet_count_prev, 0)) AS follow_streamer_bet_count_wow,
  SAFE_DIVIDE(donation_amount_total - donation_amount_total_prev,
              NULLIF(donation_amount_total_prev, 0)) AS donation_amount_total_wow
FROM with_rolling;
