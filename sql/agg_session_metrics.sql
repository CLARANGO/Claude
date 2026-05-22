-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- agg_session_metrics — one row per stream session
-- Source: nf-bifrost.livestream_dm.core_streaming_performance (cust_id × stream_id grain)
--
-- Applies bq-filter-rules:
--   is_lic=1, is_shared IS TRUE, is_cancelled IS FALSE, site_id != 99 (chatroom),
--   streamer NOT IN ('Popo','GOKU','ID_0'), streamer != 'ID_N/A', streamer NOT LIKE 'ID_%'
-- Currency: MYR → USD via /4.2 (chatroom). Test currencies excluded.
-- Aliases: bdw_bet_count, bdw_turnover (per naming conventions).
--
-- TODOs (resolve via Phase 0 probes):
--   [Q3] World Cup filter string in match_info.League / LeagueGroup
--   [Q5] csp ↔ match_info join key — currently anchor + time window

CREATE OR REPLACE TABLE `nf-muses.reporting.agg_session_metrics`
PARTITION BY day
CLUSTER BY streamer_id, stream_id
AS
WITH
  -- Per-stream aggregation of customer-session rows
  by_stream AS (
    SELECT
      stream_id,
      ANY_VALUE(anchor_id)         AS streamer_id,
      ANY_VALUE(streamer)          AS streamer_name,
      ANY_VALUE(CASE
        WHEN stream_type LIKE 'Sport%' THEN 'sports'
        ELSE 'entertainment'
      END)                         AS stream_type,
      ANY_VALUE(league_or_tag)     AS league_or_tag,
      ANY_VALUE(stream_name)       AS stream_name,
      ANY_VALUE(stream_start_time) AS start_ts,
      ANY_VALUE(stream_end_time)   AS end_ts,
      DATE(ANY_VALUE(stream_start_time), 'Asia/Taipei') AS day,
      ANY_VALUE(country)           AS language,
      ANY_VALUE(site)              AS site,
      ANY_VALUE(currency)          AS currency,
      ANY_VALUE(supplier)          AS supplier,

      -- NS
      SUM(follow_bet_count) AS follow_streamer_bet_count,
      SUM(IFNULL(tip_amount_rm, 0)
        + IFNULL(box_amount_rm, 0)
        + IFNULL(wheel_amount_rm, 0)) / 4.2 AS donation_amount_usd,

      -- L1
      SUM(follow_member_to) / 4.2 AS follow_streamer_bet_turnover_usd,
      COUNT(DISTINCT IF(follow_bet_count > 0, cust_id, NULL)) AS follow_user_count,
      COUNT(DISTINCT IF(if_tip = 1 OR if_box = 1 OR if_wheel = 1, cust_id, NULL)) AS donation_user_count,
      SUM(tip_amount_rm)  / 4.2 AS tip_amount_usd,
      SUM(tip_count)            AS tip_count,
      COUNT(DISTINCT IF(if_tip = 1, cust_id, NULL)) AS tip_user_count,
      SUM(box_amount_rm)  / 4.2 AS box_amount_usd,
      SUM(box_count)            AS box_count,
      SUM(wheel_amount_rm)/ 4.2 AS wheel_amount_usd,
      SUM(wheel_count)          AS wheel_count,

      -- L2 totals
      SUM(bet_count)        AS total_bet_count,
      SUM(member_to) / 4.2  AS total_bet_turnover_usd,
      SUM(follow_player_bet_count) AS follow_user_bet_count,
      SUM(follow_player_member_to) / 4.2 AS follow_user_bet_turnover_usd,

      -- L2 bet during watch (aliased bdw_*)
      SUM(during_watch_bet_count)        AS bdw_bet_count,
      SUM(during_watch_member_to) / 4.2  AS bdw_turnover_usd,

      -- Watch
      SUM(watch_sec) AS watch_seconds_total,
      COUNT(DISTINCT IF(if_watch = 1, cust_id, NULL)) AS viewers,
      COUNT(DISTINCT CASE WHEN watch_sec >= 600 THEN cust_id END) AS watch_over_10min_user,
      COUNT(DISTINCT IF(if_chat = 1, cust_id, NULL)) AS chatters,
      SUM(message_count) AS message_count
    FROM `nf-bifrost.livestream_dm.core_streaming_performance`
    WHERE is_lic = 1
      AND is_shared IS TRUE
      AND is_cancelled IS FALSE
      AND site_id != 99                                   -- chatroom only
      AND streamer NOT IN ('Popo', 'GOKU', 'ID_0')
      AND streamer != 'ID_N/A'
      AND streamer NOT LIKE 'ID_%'
      AND currency != 'UUS' AND currency_id != 20
      AND DATE(stream_start_time, 'Asia/Taipei') BETWEEN DATE '2026-06-01' AND DATE '2026-07-31'
    GROUP BY stream_id
  ),

  -- Per-stream "Follow System" bets — from fact_live_bet (the only place follow_type lives)
  -- TODO [Q2]: replace 'system' with actual follow_type value after probe.
  by_stream_follow_system AS (
    SELECT
      s.stream_id,
      COUNTIF(fb.follow_type = 'system')                              AS follow_system_bet_count,
      SUM(IF(fb.follow_type = 'system', fb.member_to, 0)) / 4.2       AS follow_system_bet_turnover_usd
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
      -- TODO [Q3]: confirmed World Cup string
      AND (LeagueGroup LIKE '%World Cup%' OR League LIKE '%World Cup%')
  )

SELECT
  CURRENT_DATE('Asia/Taipei') AS as_of_date,
  s.stream_id,
  s.streamer_id,
  s.streamer_name,
  s.day,
  s.start_ts,
  s.end_ts,
  s.stream_type,
  s.language,
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
  s.donation_amount_usd,
  -- L1
  COALESCE(r.recommend_bet_count, 0) AS recommend_bet_count,
  s.follow_streamer_bet_turnover_usd,
  s.follow_user_count,
  s.donation_user_count,
  s.tip_amount_usd,
  s.tip_count,
  s.tip_user_count,
  -- L2 — Self = total − follow_streamer − follow_user − follow_system
  GREATEST(s.total_bet_count
           - s.follow_streamer_bet_count
           - s.follow_user_bet_count
           - COALESCE(fs.follow_system_bet_count, 0), 0) AS self_bet_count,
  s.follow_user_bet_count,
  COALESCE(fs.follow_system_bet_count, 0) AS follow_system_bet_count,
  GREATEST(s.total_bet_turnover_usd
           - s.follow_streamer_bet_turnover_usd
           - s.follow_user_bet_turnover_usd
           - COALESCE(fs.follow_system_bet_turnover_usd, 0), 0) AS self_bet_turnover_usd,
  s.follow_user_bet_turnover_usd,
  COALESCE(fs.follow_system_bet_turnover_usd, 0) AS follow_system_bet_turnover_usd,
  -- L2 — bet during watch + watch
  s.bdw_bet_count,
  s.bdw_turnover_usd,
  s.watch_seconds_total,
  s.viewers,
  s.watch_over_10min_user,
  SAFE_DIVIDE(s.watch_seconds_total, s.viewers) AS watch_seconds_per_viewer,
  -- Engagement extras
  s.chatters,
  s.message_count,
  s.box_amount_usd,
  s.wheel_amount_usd,
  -- Raw totals
  s.total_bet_count,
  s.total_bet_turnover_usd
FROM by_stream s
LEFT JOIN by_stream_follow_system fs ON s.stream_id = fs.stream_id
LEFT JOIN match_dim m
  ON m.AnchorId = s.streamer_id
 AND m.KickOffTime BETWEEN TIMESTAMP_SUB(s.start_ts, INTERVAL 1 HOUR) AND s.end_ts
LEFT JOIN by_stream_recommend r
  ON r.streamer_id = s.streamer_id
 AND r.SabaMatchId = m.SabaMatchId;
