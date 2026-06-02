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

CREATE OR REPLACE TABLE `nf-muses.worldcup.agg_session_metrics`
PARTITION BY day
CLUSTER BY anchor_id, stream_id
AS
-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- agg_session_metrics — one row per stream session
-- Source: nf-bifrost.livestream_dm.core_streaming_performance (cust_id × stream_id grain)
-- PCU source: nf-bifrost.livestream_dm.fact_stream_viewship (joined on stream_id / anchor_id)
--
-- Applies bq-filter-rules:
--   is_lic=1, is_shared IS TRUE, is_cancelled IS FALSE, site_id != 99 (chatroom),
--   streamer NOT IN ('Popo','GOKU','ID_0'), streamer != 'ID_N/A', streamer NOT LIKE 'ID_%'
-- Currency policy (worldcup dashboard):
--   • Turnover (member_to family) stays in RM — columns aliased *_turnover_rm.
--   • Donation / tip / box / wheel amounts convert to USD via /4.2.
-- Test currencies excluded.
-- Aliases: bdw_bet_count, bdw_turnover_rm (per naming conventions).
--
-- TODOs (resolve via Phase 0 probes):
--   [Q3] World Cup filter string in match_info.League (likely 'WORLD CUP')

WITH by_cust AS (
    SELECT
      csp.cust_id,
      csp.stream_id,
      -- we use ANY_VALUE here just to pass the stream details through the customer level
      csp.anchor_id,
      csp.streamer,
      CASE
        WHEN csp.stream_type LIKE 'Sport%' THEN 'sports'
        ELSE 'entertainment'
      END                             AS stream_type,
      csp.league_or_tag,
      csp.stream_name,
      csp.stream_start_time AS start_ts,
      csp.stream_end_time   AS end_ts,
      DATE(csp.stream_start_date) AS day,
      csp.country           AS language,
      csp.site,
      csp.currency,
      csp.supplier,

      -- Individual Flags
      MAX(IF(csp.follow_bet_count > 0, 1, 0)) AS is_follow_user,
      MAX(IF(csp.during_watch_bet_count > 0, 1, 0)) AS is_bdw_user,
      MAX(IF(csp.if_tip IS TRUE OR csp.if_box IS TRUE OR csp.if_wheel IS TRUE, 1, 0)) AS is_donation_user,
      MAX(IF(csp.if_watch IS TRUE, 1, 0))     AS is_viewer,
      MAX(IF(csp.watch_sec >= 600, 1, 0))     AS is_watch_over_10min_user,
      MAX(IF(csp.if_chat IS TRUE, 1, 0))      AS is_chat_user,

      -- Individual Metrics
      SUM(csp.follow_bet_count)        AS follow_streamer_bet_count,
      SUM(csp.during_watch_member_to)  AS bdw_turnover_rm,
      SUM(csp.follow_member_to)        AS follow_streamer_bet_turnover_rm,
      
      SUM(IFNULL(csp.tip_amount_rm, 0)
        + IFNULL(csp.box_amount_rm, 0)
        + IFNULL(csp.wheel_amount_rm, 0)) / 4.2 AS donation_amount_usd,
      
      SUM(IFNULL(csp.tip_count, 0) + IFNULL(csp.box_count, 0) + IFNULL(csp.wheel_count, 0)) AS donation_count,
      SUM(csp.tip_amount_rm)  / 4.2    AS tip_amount_usd,
      SUM(csp.tip_count)               AS tip_count,
      SUM(csp.box_amount_rm)  / 4.2    AS box_amount_usd,
      SUM(csp.box_count)               AS box_count,
      SUM(csp.wheel_amount_rm)/ 4.2    AS wheel_amount_usd,
      SUM(csp.wheel_count)             AS wheel_count,

      SUM(csp.bet_count)               AS total_bet_count,
      SUM(csp.member_to)               AS total_bet_turnover_rm,
      SUM(csp.follow_player_bet_count) AS follow_user_bet_count,
      SUM(csp.follow_player_member_to) AS follow_user_bet_turnover_rm,
      SUM(csp.during_watch_bet_count)  AS bdw_bet_count,
      SUM(csp.watch_sec)               AS watch_sec_total,
      SUM(csp.message_count)           AS message_count
    FROM `nf-bifrost.livestream_dm.core_streaming_performance` csp
    WHERE csp.is_lic = 1
      AND csp.is_shared IS TRUE
      AND csp.is_cancelled IS FALSE
      AND csp.site_id != 99
      AND csp.streamer NOT IN ('Popo', 'GOKU', 'ID_0')
      AND csp.streamer != 'ID_N/A'
      AND csp.streamer NOT LIKE 'ID_%'
      AND csp.currency != 'UUS' AND csp.currency_id != 20
      AND DATE(csp.stream_start_date) BETWEEN DATE '2026-06-01' AND DATE '2026-08-01'
    GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14
),

by_stream AS (
    SELECT
      bc.stream_id,
      bc.anchor_id,
      bc.streamer,
      bc.stream_type,
      bc.league_or_tag,
      bc.stream_name,
      bc.start_ts,
      bc.end_ts,
      bc.day,
      bc.language,
      bc.supplier,

      -- NS
      SUM(bc.follow_streamer_bet_count) AS follow_streamer_bet_count,
      SUM(bc.bdw_turnover_rm)           AS bdw_turnover_rm,
      SUM(bc.donation_amount_usd)       AS donation_amount_usd,

      -- L1
      SUM(bc.follow_streamer_bet_turnover_rm) AS follow_streamer_bet_turnover_rm,
      SUM(bc.is_follow_user)            AS follow_user_count,
      SUM(bc.is_bdw_user)               AS bdw_user_count,
      SUM(bc.is_donation_user)          AS donation_user_count,
      SUM(bc.donation_count)            AS donation_count,
      SUM(bc.tip_amount_usd)            AS tip_amount_usd,
      SUM(bc.tip_count)                 AS tip_count,
      SUM(bc.box_amount_usd)            AS box_amount_usd,
      SUM(bc.box_count)                 AS box_count,
      SUM(bc.wheel_amount_usd)          AS wheel_amount_usd,
      SUM(bc.wheel_count)               AS wheel_count,

      -- L2 totals
      SUM(bc.total_bet_count)               AS total_bet_count,
      SUM(bc.total_bet_turnover_rm)         AS total_bet_turnover_rm,
      SUM(bc.follow_user_bet_count)         AS follow_user_bet_count,
      SUM(bc.follow_user_bet_turnover_rm)   AS follow_user_bet_turnover_rm,
      SUM(bc.bdw_bet_count)                 AS bdw_bet_count,

      -- Watch
      SUM(bc.watch_sec_total)/60        AS watch_min_total,
      SUM(bc.is_viewer)                 AS viewers,
      SUM(bc.is_watch_over_10min_user)  AS watch_over_10min_user,
      SUM(bc.is_chat_user)              AS chat_user,
      SUM(bc.message_count)             AS message_count,
      
      -- PCU
      MAX(mcm.call_pcu)                 AS pcu
    FROM by_cust bc
    LEFT JOIN `nf-bifrost.livestream_dm.fact_stream_viewship` mcm
      ON  bc.stream_id = mcm.stream_id
      AND bc.anchor_id = mcm.anchor_id
    GROUP BY 
      bc.stream_id,
      bc.anchor_id,
      bc.streamer,
      bc.stream_type,
      bc.league_or_tag,
      bc.stream_name,
      bc.start_ts,
      bc.end_ts,
      bc.day,
      bc.language,
      bc.supplier
)
,

  -- Streamer recommendations (L1 Recommend Bet Count)
  by_stream_recommend AS (
    SELECT
      cr.AnchorId AS streamer_id,
      cr.SabaMatchId,
      SUM(cr.RecommendCount) AS recommend_bet_count
    FROM `nf-bifrost.LiveStreaming.chatroom_recommend` cr
    GROUP BY cr.AnchorId, cr.SabaMatchId
  ),

  -- Match dim — World Cup filter + time-slot tag + 7-stage bracket
  match_dim AS (
    SELECT
      SabaMatchId,
      AnchorId,
      KickOffTime,
      League,
      LeagueGroup,
      CASE
        WHEN EXTRACT(HOUR FROM KickOffTime) BETWEEN 0  AND 5  THEN 'late_night'
        WHEN EXTRACT(HOUR FROM KickOffTime) BETWEEN 6  AND 11 THEN 'morning'
        ELSE 'afternoon'
      END AS time_slot_taipei,
      EXTRACT(DAYOFWEEK FROM DATE(KickOffTime)) AS day_of_week,
      CASE
        WHEN DATE(KickOffTime) BETWEEN '2026-06-11' AND '2026-06-28' THEN 'group'
        WHEN DATE(KickOffTime) BETWEEN '2026-06-29' AND '2026-07-04' THEN 'R32'
        WHEN DATE(KickOffTime) BETWEEN '2026-07-05' AND '2026-07-08' THEN 'R16'
        WHEN DATE(KickOffTime) BETWEEN '2026-07-10' AND '2026-07-12' THEN 'QF'
        WHEN DATE(KickOffTime) BETWEEN '2026-07-15' AND '2026-07-16' THEN 'SF'
        WHEN DATE(KickOffTime) = '2026-07-19' THEN '3rd_place'
        WHEN DATE(KickOffTime) = '2026-07-20' THEN 'final'
        ELSE 'other'
      END AS match_stage
    FROM `nf-bifrost.LiveStreaming.match_info`
    WHERE isCancelled = FALSE
      -- TODO [Q3]: confirm exact World Cup string (likely 'WORLD CUP')
    AND UPPER(League) LIKE '%WORLD CUP%'
  )

SELECT
  s.day,
  s.stream_id,
  s.anchor_id,
  s.streamer,
  s.stream_name,
  s.start_ts,
  s.end_ts,
  s.stream_type,
  s.language,
  -- Match info
  m.time_slot_taipei,
  m.day_of_week,
  m.match_stage,
  -- NS
  s.follow_streamer_bet_count,
  s.bdw_turnover_rm,
  s.donation_amount_usd,
  -- L1
  COALESCE(r.recommend_bet_count, 0) AS recommend_bet_count,
  s.follow_streamer_bet_turnover_rm,
  s.follow_user_count,
  s.donation_user_count,
  s.donation_count,
  s.tip_amount_usd,
  s.tip_count,
  s.box_amount_usd,
  s.box_count,
  s.wheel_amount_usd,
  s.wheel_count,
  -- L2 — bet during watch count + watch
  s.bdw_bet_count,
  s.watch_min_total,
  s.viewers,
  s.watch_over_10min_user,
  SAFE_DIVIDE(s.watch_min_total, s.viewers) AS watch_min_per_viewer,
  s.pcu,
  -- Engagement extras
  s.chat_user,
  s.message_count,
  -- Raw totals
  s.total_bet_count,
  s.total_bet_turnover_rm
FROM by_stream s
LEFT JOIN match_dim m
  ON m.AnchorId = s.anchor_id
 AND m.KickOffTime BETWEEN TIMESTAMP_SUB(s.start_ts, INTERVAL 1 HOUR) AND s.end_ts
LEFT JOIN by_stream_recommend r
  ON r.streamer_id = s.anchor_id
 AND r.SabaMatchId = m.SabaMatchId;
