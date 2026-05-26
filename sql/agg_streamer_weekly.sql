-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- agg_streamer_weekly — streamer × ISO week (Mon–Sun)
-- Includes cumulative-prior-weeks average + WoW % delta. Reads from agg_session_metrics.
-- Currency: turnover in RM (raw member_to); donation/tip/box/wheel amounts in USD.
-- stream_count uses paired streamer_id+stream_id key.

CREATE OR REPLACE TABLE `nf-muses.worldcup.agg_streamer_weekly`
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
  GROUP BY streamer_id, iso_week_start
),

-- Cumulative-prior-weeks average: week N vs avg(weeks 1 … N-1) — replaces 4-week rolling median.
with_cumavg AS (
  SELECT
    *,
    AVG(follow_streamer_bet_count)       OVER cw AS follow_streamer_bet_count_cumavg_prior,
    AVG(follow_streamer_bet_turnover_rm) OVER cw AS follow_streamer_bet_turnover_rm_cumavg_prior,
    AVG(bdw_turnover_rm)                 OVER cw AS bdw_turnover_rm_cumavg_prior,
    AVG(bdw_bet_count)                   OVER cw AS bdw_bet_count_cumavg_prior,
    AVG(donation_amount_usd)             OVER cw AS donation_amount_usd_cumavg_prior,
    AVG(donation_user_count)             OVER cw AS donation_user_count_cumavg_prior,
    AVG(watch_seconds_total)             OVER cw AS watch_seconds_total_cumavg_prior,
    LAG(follow_streamer_bet_count) OVER w1 AS follow_streamer_bet_count_prev,
    LAG(donation_amount_usd)       OVER w1 AS donation_amount_usd_prev,
    LAG(stream_count)              OVER w1 AS stream_count_prev
  FROM weekly
  WINDOW
    cw AS (PARTITION BY streamer_id ORDER BY iso_week_start
           ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
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
FROM with_cumavg;
