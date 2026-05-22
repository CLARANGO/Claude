-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- agg_streamer_weekly — streamer × ISO week (Mon–Sun)
-- Includes 4-week rolling median + WoW % delta. Reads from agg_session_metrics (already filtered).
-- USD-denominated metrics (alias *_usd). stream_count uses paired anchor_id+stream_id key.

CREATE OR REPLACE TABLE `nf-muses.reporting.agg_streamer_weekly`
PARTITION BY iso_week_start
CLUSTER BY streamer_id
AS
WITH weekly AS (
  SELECT
    streamer_id,
    DATE_TRUNC(day, ISOWEEK) AS iso_week_start,
    COUNT(DISTINCT CASE WHEN streamer_id != 0
      THEN CONCAT(CAST(streamer_id AS STRING), '-', CAST(stream_id AS STRING))
    END) AS stream_count,
    COUNT(DISTINCT day) AS active_days,
    SUM(follow_streamer_bet_count)         AS follow_streamer_bet_count,
    SUM(follow_streamer_bet_turnover_usd)  AS follow_streamer_bet_turnover_usd,
    SUM(donation_amount_usd)               AS donation_amount_usd,
    SUM(recommend_bet_count)               AS recommend_bet_count,
    SUM(tip_amount_usd)                    AS tip_amount_usd,
    SUM(tip_count)                         AS tip_count,
    SUM(bdw_bet_count)                     AS bdw_bet_count,
    SUM(bdw_turnover_usd)                  AS bdw_turnover_usd,
    SUM(watch_seconds_total)               AS watch_seconds_total,
    SUM(viewers)                           AS viewers
  FROM `nf-muses.reporting.agg_session_metrics`
  GROUP BY streamer_id, iso_week_start
),

with_rolling AS (
  SELECT
    *,
    PERCENTILE_CONT(follow_streamer_bet_count, 0.5) OVER w4 AS follow_streamer_bet_count_med4,
    PERCENTILE_CONT(donation_amount_usd,       0.5) OVER w4 AS donation_amount_usd_med4,
    PERCENTILE_CONT(bdw_turnover_usd,          0.5) OVER w4 AS bdw_turnover_usd_med4,
    PERCENTILE_CONT(watch_seconds_total,       0.5) OVER w4 AS watch_seconds_total_med4,
    LAG(follow_streamer_bet_count) OVER w1 AS follow_streamer_bet_count_prev,
    LAG(donation_amount_usd)       OVER w1 AS donation_amount_usd_prev,
    LAG(stream_count)              OVER w1 AS stream_count_prev
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
  SAFE_DIVIDE(donation_amount_usd - donation_amount_usd_prev,
              NULLIF(donation_amount_usd_prev, 0))       AS donation_amount_usd_wow,
  SAFE_DIVIDE(stream_count - stream_count_prev,
              NULLIF(stream_count_prev, 0))              AS stream_count_wow
FROM with_rolling;
