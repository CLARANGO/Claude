-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- agg_streamer_monthly — streamer × calendar month
-- Includes MoM % delta. Reads from agg_session_metrics. USD-denominated.

CREATE OR REPLACE TABLE `nf-bifrost.reporting.agg_streamer_monthly`
PARTITION BY month_start
CLUSTER BY streamer_id
AS
WITH monthly AS (
  SELECT
    streamer_id,
    DATE_TRUNC(day, MONTH) AS month_start,
    FORMAT_DATE('%Y-%m', DATE_TRUNC(day, MONTH)) AS month_year,
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
  FROM `nf-bifrost.reporting.agg_session_metrics`
  GROUP BY streamer_id, month_start
),

with_prev AS (
  SELECT
    *,
    LAG(follow_streamer_bet_count) OVER w AS follow_streamer_bet_count_prev,
    LAG(donation_amount_usd)       OVER w AS donation_amount_usd_prev,
    LAG(bdw_turnover_usd)          OVER w AS bdw_turnover_usd_prev,
    LAG(watch_seconds_total)       OVER w AS watch_seconds_total_prev
  FROM monthly
  WINDOW w AS (PARTITION BY streamer_id ORDER BY month_start)
)

SELECT
  CURRENT_DATE('Asia/Taipei') AS as_of_date,
  *,
  SAFE_DIVIDE(follow_streamer_bet_count - follow_streamer_bet_count_prev,
              NULLIF(follow_streamer_bet_count_prev, 0)) AS follow_streamer_bet_count_mom,
  SAFE_DIVIDE(donation_amount_usd - donation_amount_usd_prev,
              NULLIF(donation_amount_usd_prev, 0))       AS donation_amount_usd_mom,
  SAFE_DIVIDE(bdw_turnover_usd - bdw_turnover_usd_prev,
              NULLIF(bdw_turnover_usd_prev, 0))          AS bdw_turnover_usd_mom,
  SAFE_DIVIDE(watch_seconds_total - watch_seconds_total_prev,
              NULLIF(watch_seconds_total_prev, 0))       AS watch_seconds_total_mom
FROM with_prev;
