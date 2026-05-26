-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- agg_streamer_monthly — streamer × calendar month, scoped to June + July 2026
-- Compares July rows to the same streamer's June totals.
-- Currency: turnover in RM; donation/tip/box/wheel amounts in USD.

CREATE OR REPLACE TABLE `nf-muses.worldcup.agg_streamer_monthly`
PARTITION BY month_start
CLUSTER BY streamer_id
AS
WITH monthly AS (
  SELECT
    streamer_id,
    DATE_TRUNC(day, MONTH) AS month_start,
    FORMAT_DATE('%Y-%m', DATE_TRUNC(day, MONTH)) AS month_year,
    CASE EXTRACT(MONTH FROM day) WHEN 6 THEN 'June' WHEN 7 THEN 'July' ELSE 'other' END AS period_label,
    COUNT(DISTINCT CASE WHEN streamer_id != 0
      THEN CONCAT(CAST(streamer_id AS STRING), '-', CAST(stream_id AS STRING))
    END) AS stream_count,
    COUNT(DISTINCT day) AS active_days,
    SUM(follow_streamer_bet_count)         AS follow_streamer_bet_count,
    SUM(follow_streamer_bet_turnover_rm)   AS follow_streamer_bet_turnover_rm,
    SUM(bdw_turnover_rm)                   AS bdw_turnover_rm,
    SUM(bdw_bet_count)                     AS bdw_bet_count,
    SUM(donation_amount_usd)               AS donation_amount_usd,
    SUM(donation_user_count)               AS donation_user_count,
    SUM(donation_count)                    AS donation_count,
    SUM(recommend_bet_count)               AS recommend_bet_count,
    SUM(tip_amount_usd)                    AS tip_amount_usd,
    SUM(tip_count)                         AS tip_count,
    SUM(box_amount_usd)                    AS box_amount_usd,
    SUM(box_count)                         AS box_count,
    SUM(wheel_amount_usd)                  AS wheel_amount_usd,
    SUM(wheel_count)                       AS wheel_count,
    SUM(watch_seconds_total)               AS watch_seconds_total,
    SUM(viewers)                           AS viewers
  FROM `nf-muses.worldcup.agg_session_metrics`
  WHERE EXTRACT(MONTH FROM day) IN (6, 7)
  GROUP BY streamer_id, month_start
),

-- July rows get a vs_june delta; June rows get NULL (LAG inside streamer partition).
with_vs_june AS (
  SELECT
    *,
    LAG(follow_streamer_bet_count)       OVER w AS follow_streamer_bet_count_june,
    LAG(follow_streamer_bet_turnover_rm) OVER w AS follow_streamer_bet_turnover_rm_june,
    LAG(bdw_turnover_rm)                 OVER w AS bdw_turnover_rm_june,
    LAG(donation_amount_usd)             OVER w AS donation_amount_usd_june,
    LAG(watch_seconds_total)             OVER w AS watch_seconds_total_june
  FROM monthly
  WINDOW w AS (PARTITION BY streamer_id ORDER BY month_start)
)

SELECT
  CURRENT_DATE('Asia/Taipei') AS as_of_date,
  *,
  SAFE_DIVIDE(follow_streamer_bet_count - follow_streamer_bet_count_june,
              NULLIF(follow_streamer_bet_count_june, 0))       AS follow_streamer_bet_count_vs_june,
  SAFE_DIVIDE(follow_streamer_bet_turnover_rm - follow_streamer_bet_turnover_rm_june,
              NULLIF(follow_streamer_bet_turnover_rm_june, 0)) AS follow_streamer_bet_turnover_rm_vs_june,
  SAFE_DIVIDE(bdw_turnover_rm - bdw_turnover_rm_june,
              NULLIF(bdw_turnover_rm_june, 0))                 AS bdw_turnover_rm_vs_june,
  SAFE_DIVIDE(donation_amount_usd - donation_amount_usd_june,
              NULLIF(donation_amount_usd_june, 0))             AS donation_amount_usd_vs_june,
  SAFE_DIVIDE(watch_seconds_total - watch_seconds_total_june,
              NULLIF(watch_seconds_total_june, 0))             AS watch_seconds_total_vs_june
FROM with_vs_june;
