-- agg_session_metrics — one row per stream session
-- Grain: stream_id (each live session a streamer ran covering a match)
-- Partitioned by as_of_date for idempotent daily rebuild.
--
-- Placeholders to fill after Phase 0 discovery:
--   __RAW_STREAMS__        e.g. project.dataset.streams
--   __RAW_BETS__           bets table (all bets — both stream-attributed and platform)
--   __RAW_RECOMMENDATIONS__streamer pick recommendations
--   __RAW_WATCH__          watch_sessions
--   __RAW_DONATIONS__      donations (with type col distinguishing tip)
--   __RAW_USERS__          users (bot/multi-account flags)
--   __DIM_MATCH__          dim_match (World Cup fixtures + tags)
--
-- Definitions:
--   Follow Streamer = bet.category = 'follow_streamer'
--   Bet During Watch = bet.placed_ts BETWEEN watch.start AND watch.end for same (user, stream)
--   Tip = donation.type = 'tip'; Donation total includes tips
--   Voided bets and bot users excluded throughout

CREATE OR REPLACE TABLE `__PROJECT__.reporting.agg_session_metrics`
PARTITION BY as_of_date
CLUSTER BY streamer_id, match_id
AS
WITH
  clean_bets AS (
    SELECT b.*
    FROM `__RAW_BETS__` b
    LEFT JOIN `__RAW_USERS__` u USING (user_id)
    WHERE b.status NOT IN ('voided', 'cancelled')
      AND COALESCE(u.is_bot_flag, FALSE) = FALSE
      AND COALESCE(u.multi_account_flag, FALSE) = FALSE
  ),

  -- Bet During Watch: bet placed inside the user's watch window on the same stream
  bet_during_watch AS (
    SELECT
      b.stream_id,
      b.bet_id,
      b.user_id,
      b.turnover
    FROM clean_bets b
    JOIN `__RAW_WATCH__` w
      ON b.user_id = w.user_id
     AND b.stream_id = w.stream_id
     AND b.placed_ts BETWEEN w.watch_start_ts AND w.watch_end_ts
  ),

  bets_by_session AS (
    SELECT
      stream_id,
      COUNTIF(category = 'follow_streamer')                    AS follow_streamer_bet_count,
      SUM(IF(category = 'follow_streamer', turnover, 0))       AS follow_streamer_bet_turnover,
      COUNT(DISTINCT IF(category = 'follow_streamer', user_id, NULL)) AS follow_user_count,
      COUNTIF(category = 'self')          AS self_bet_count,
      SUM(IF(category = 'self', turnover, 0))          AS self_bet_turnover,
      COUNTIF(category = 'follow_user')   AS follow_user_bet_count,
      SUM(IF(category = 'follow_user', turnover, 0))   AS follow_user_bet_turnover,
      COUNTIF(category = 'follow_system') AS follow_system_bet_count,
      SUM(IF(category = 'follow_system', turnover, 0)) AS follow_system_bet_turnover
    FROM clean_bets
    WHERE stream_id IS NOT NULL
    GROUP BY stream_id
  ),

  recommend_bets AS (
    -- Bets placed on a pick the streamer recommended during that session
    SELECT
      r.stream_id,
      COUNT(DISTINCT b.bet_id) AS recommend_bet_count
    FROM `__RAW_RECOMMENDATIONS__` r
    JOIN clean_bets b
      ON b.stream_id = r.stream_id
     AND b.pick = r.pick          -- adjust to actual linking column post-Phase-0
    GROUP BY r.stream_id
  ),

  watch_agg AS (
    SELECT
      stream_id,
      SUM(TIMESTAMP_DIFF(watch_end_ts, watch_start_ts, SECOND)) AS watch_seconds_total,
      COUNT(DISTINCT user_id) AS viewers
    FROM `__RAW_WATCH__`
    GROUP BY stream_id
  ),

  bdw_agg AS (
    SELECT
      stream_id,
      COUNT(*) AS bet_during_watch_count,
      SUM(turnover) AS bet_during_watch_turnover
    FROM bet_during_watch
    GROUP BY stream_id
  ),

  donations_agg AS (
    SELECT
      stream_id,
      SUM(amount) AS donation_amount_total,
      COUNT(DISTINCT user_id) AS donation_user_count,
      SUM(IF(type = 'tip', amount, 0)) AS tip_amount,
      COUNTIF(type = 'tip')           AS tip_count,
      COUNT(DISTINCT IF(type = 'tip', user_id, NULL)) AS tip_user_count
    FROM `__RAW_DONATIONS__`
    GROUP BY stream_id
  )

SELECT
  CURRENT_DATE('Asia/Taipei') AS as_of_date,
  s.stream_id,
  s.streamer_id,
  s.match_id,
  DATE(s.start_ts, 'Asia/Taipei') AS session_date,
  s.start_ts,
  s.end_ts,
  -- match tags
  m.stage,
  m.popularity_tier,
  m.time_slot_taipei,
  m.day_of_week,
  -- NS
  bs.follow_streamer_bet_count,
  COALESCE(da.donation_amount_total, 0) AS donation_amount_total,
  -- L1
  COALESCE(rb.recommend_bet_count, 0) AS recommend_bet_count,
  bs.follow_streamer_bet_turnover,
  bs.follow_user_count,
  COALESCE(da.donation_user_count, 0) AS donation_user_count,
  COALESCE(da.tip_amount, 0) AS tip_amount,
  COALESCE(da.tip_count, 0)  AS tip_count,
  COALESCE(da.tip_user_count, 0) AS tip_user_count,
  -- L2: bet count by category
  bs.self_bet_count,
  bs.follow_user_bet_count,
  bs.follow_system_bet_count,
  -- L2: bet turnover by category
  bs.self_bet_turnover,
  bs.follow_user_bet_turnover,
  bs.follow_system_bet_turnover,
  -- L2: bet during watch
  COALESCE(bdw.bet_during_watch_count, 0)    AS bet_during_watch_count,
  COALESCE(bdw.bet_during_watch_turnover, 0) AS bet_during_watch_turnover,
  -- L2: watch
  COALESCE(w.watch_seconds_total, 0) AS watch_seconds_total,
  COALESCE(w.viewers, 0) AS viewers,
  SAFE_DIVIDE(w.watch_seconds_total, w.viewers) AS watch_seconds_per_viewer
FROM `__RAW_STREAMS__` s
LEFT JOIN `__DIM_MATCH__` m   ON s.match_id = m.match_id
LEFT JOIN bets_by_session bs  ON s.stream_id = bs.stream_id
LEFT JOIN recommend_bets rb   ON s.stream_id = rb.stream_id
LEFT JOIN watch_agg w         ON s.stream_id = w.stream_id
LEFT JOIN bdw_agg bdw         ON s.stream_id = bdw.stream_id
LEFT JOIN donations_agg da    ON s.stream_id = da.stream_id
WHERE DATE(s.start_ts, 'Asia/Taipei') BETWEEN DATE '2026-06-01' AND DATE '2026-07-31';
