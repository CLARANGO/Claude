-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- agg_session_metrics — one row per stream session
-- Source: nf-bifrost.livestream_dm.core_streaming_performance (cust_id × stream_id grain)
-- SUM over cust_id to collapse to one row per stream_id.
--
-- TODOs (resolve via Phase 0 probes):
--   [Q1] Donation composition — currently includes tip + box + wheel. Adjust if tip-only.
--   [Q3] World Cup filter — replace WHERE clause once exact League/LeagueGroup string is confirmed.
--   [Q5] Match join — using league_or_tag → match_info.League with time-window match. Replace with SabaMatchId if direct join exists.

CREATE OR REPLACE TABLE `nf-bifrost.reporting.agg_session_metrics`
PARTITION BY session_date
CLUSTER BY streamer_id, stream_id
AS
WITH
  -- Per-stream aggregation of customer-session rows
  by_stream AS (
    SELECT
      stream_id,
      ANY_VALUE(anchor_id)          AS streamer_id,
      ANY_VALUE(streamer)           AS streamer_name,
      ANY_VALUE(stream_type)        AS stream_type,
      ANY_VALUE(league_or_tag)      AS league_or_tag,
      ANY_VALUE(stream_name)        AS stream_name,
      ANY_VALUE(stream_start_time)  AS start_ts,
      ANY_VALUE(stream_end_time)    AS end_ts,
      DATE(ANY_VALUE(stream_start_time), 'Asia/Taipei') AS session_date,
      ANY_VALUE(country)            AS country,
      ANY_VALUE(site)               AS site,
      ANY_VALUE(currency)           AS currency,
      ANY_VALUE(supplier)           AS supplier,
      ANY_VALUE(self_owned)         AS self_owned,
      ANY_VALUE(is_shared)          AS is_shared,
      -- NS
      SUM(follow_bet_count)                                            AS follow_streamer_bet_count,
      SUM(IFNULL(tip_amount_rm,0) + IFNULL(box_amount_rm,0) + IFNULL(wheel_amount_rm,0)) AS donation_amount_total,
      -- L1
      SUM(follow_member_to)                                            AS follow_streamer_bet_turnover,
      COUNT(DISTINCT IF(follow_bet_count > 0, cust_id, NULL))          AS follow_user_count,
      COUNT(DISTINCT IF(if_tip = 1 OR if_box = 1 OR if_wheel = 1, cust_id, NULL)) AS donation_user_count,
      SUM(tip_amount_rm)            AS tip_amount,
      SUM(tip_count)                AS tip_count,
      COUNT(DISTINCT IF(if_tip = 1, cust_id, NULL)) AS tip_user_count,
      SUM(box_amount_rm)            AS box_amount,
      SUM(box_count)                AS box_count,
      SUM(wheel_amount_rm)          AS wheel_amount,
      SUM(wheel_count)              AS wheel_count,
      -- L2 — partial (Follow Streamer + Follow Player + totals; Self/System derived below)
      SUM(bet_count)                AS total_bet_count,
      SUM(member_to)                AS total_bet_turnover,
      SUM(follow_player_bet_count)  AS follow_user_bet_count,
      SUM(follow_player_member_to)  AS follow_user_bet_turnover,
      SUM(during_watch_bet_count)        AS bet_during_watch_count,
      SUM(during_watch_member_to)        AS bet_during_watch_turnover,
      SUM(watch_sec)                AS watch_seconds_total,
      COUNT(DISTINCT IF(if_watch = 1, cust_id, NULL)) AS viewers,
      COUNT(DISTINCT IF(if_chat = 1, cust_id, NULL))  AS chatters,
      SUM(message_count)            AS message_count
    FROM `nf-bifrost.livestream_dm.core_streaming_performance`
    WHERE is_cancelled = FALSE
      AND DATE(stream_start_time, 'Asia/Taipei') BETWEEN DATE '2026-06-01' AND DATE '2026-07-31'
    GROUP BY stream_id
  ),

  -- Per-stream "Follow System" bets — sourced from fact_live_bet (the only place follow_type lives)
  -- Attribution: anchor_id + match_id + trans_dt inside stream window
  -- TODO [Q2]: replace 'system' with the actual follow_type value once probe confirms.
  by_stream_follow_system AS (
    SELECT
      s.stream_id,
      COUNTIF(fb.follow_type = 'system')                        AS follow_system_bet_count,
      SUM(IF(fb.follow_type = 'system', fb.member_to, 0))       AS follow_system_bet_turnover
    FROM by_stream s
    LEFT JOIN `nf-bifrost.livestream_dm.fact_live_bet` fb
      ON fb.anchor_id = s.streamer_id
     AND fb.trans_dt BETWEEN s.start_ts AND s.end_ts
    GROUP BY s.stream_id
  ),

  -- Streamer recommendations (L1 Recommend Bet Count)
  by_stream_recommend AS (
    SELECT
      cr.AnchorId AS streamer_id,
      cr.SabaMatchId,
      SUM(cr.RecommendCount) AS recommend_bet_count
    FROM `nf-bifrost.LiveStreaming.chatroom_recommend` cr
    GROUP BY cr.AnchorId, cr.SabaMatchId
  ),

  -- Match dim
  match_dim AS (
    SELECT
      SabaMatchId,
      AnchorId,
      KickOffTime,
      League,
      LeagueGroup,
      HomeCnName,
      AwayCnName,
      CASE
        WHEN EXTRACT(HOUR FROM KickOffTime AT TIME ZONE 'Asia/Taipei') BETWEEN 20 AND 23 THEN 'prime'
        WHEN EXTRACT(HOUR FROM KickOffTime AT TIME ZONE 'Asia/Taipei') BETWEEN 0  AND 5  THEN 'late_night'
        WHEN EXTRACT(HOUR FROM KickOffTime AT TIME ZONE 'Asia/Taipei') BETWEEN 6  AND 11 THEN 'morning'
        ELSE 'afternoon'
      END AS time_slot_taipei,
      EXTRACT(DAYOFWEEK FROM DATE(KickOffTime, 'Asia/Taipei')) AS day_of_week
    FROM `nf-bifrost.LiveStreaming.match_info`
    WHERE isCancelled = FALSE
      -- TODO [Q3]: replace with confirmed World Cup string
      AND (LeagueGroup LIKE '%World Cup%' OR League LIKE '%World Cup%')
  )

SELECT
  CURRENT_DATE('Asia/Taipei') AS as_of_date,
  s.stream_id,
  s.streamer_id,
  s.streamer_name,
  s.session_date,
  s.start_ts,
  s.end_ts,
  s.stream_type,
  s.country,
  s.site,
  s.currency,
  -- Match info
  m.SabaMatchId,
  m.HomeCnName,
  m.AwayCnName,
  m.KickOffTime,
  m.time_slot_taipei,
  m.day_of_week,
  -- NS
  s.follow_streamer_bet_count,
  s.donation_amount_total,
  -- L1
  COALESCE(r.recommend_bet_count, 0) AS recommend_bet_count,
  s.follow_streamer_bet_turnover,
  s.follow_user_count,
  s.donation_user_count,
  s.tip_amount,
  s.tip_count,
  s.tip_user_count,
  -- L2 — bet by category
  -- Self = total − follow_streamer − follow_user − follow_system
  GREATEST(s.total_bet_count
           - s.follow_streamer_bet_count
           - s.follow_user_bet_count
           - COALESCE(fs.follow_system_bet_count, 0), 0) AS self_bet_count,
  s.follow_user_bet_count,
  COALESCE(fs.follow_system_bet_count, 0) AS follow_system_bet_count,
  -- Turnover by category
  GREATEST(s.total_bet_turnover
           - s.follow_streamer_bet_turnover
           - s.follow_user_bet_turnover
           - COALESCE(fs.follow_system_bet_turnover, 0), 0) AS self_bet_turnover,
  s.follow_user_bet_turnover,
  COALESCE(fs.follow_system_bet_turnover, 0) AS follow_system_bet_turnover,
  -- L2 — bet during watch + watch
  s.bet_during_watch_count,
  s.bet_during_watch_turnover,
  s.watch_seconds_total,
  s.viewers,
  SAFE_DIVIDE(s.watch_seconds_total, s.viewers) AS watch_seconds_per_viewer,
  -- Engagement extras
  s.chatters,
  s.message_count,
  s.box_amount,
  s.wheel_amount,
  -- Raw totals (handy for debug)
  s.total_bet_count,
  s.total_bet_turnover
FROM by_stream s
LEFT JOIN by_stream_follow_system fs ON s.stream_id = fs.stream_id
-- Match join: streamer (anchor) + match kickoff inside stream window
-- TODO [Q5]: if core_streaming_performance gets a SabaMatchId column, replace with direct equality
LEFT JOIN match_dim m
  ON m.AnchorId = s.streamer_id
 AND m.KickOffTime BETWEEN TIMESTAMP_SUB(s.start_ts, INTERVAL 1 HOUR) AND s.end_ts
LEFT JOIN by_stream_recommend r
  ON r.streamer_id = s.streamer_id
 AND r.SabaMatchId = m.SabaMatchId;
