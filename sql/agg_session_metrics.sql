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
PARTITION BY date
CLUSTER BY streamer_id, stream_id
AS
WITH
  -- Per-stream aggregation of customer-session rows
by_stream AS (
    SELECT
      csp.stream_id,
      ANY_VALUE(csp.anchor_id)         AS streamer_id,
      ANY_VALUE(csp.streamer)          AS streamer,
      ANY_VALUE(CASE
        WHEN csp.stream_type LIKE 'Sport%' THEN 'sports'
        ELSE 'entertainment'
      END)                             AS stream_type,
      ANY_VALUE(csp.league_or_tag)     AS league_or_tag,
      ANY_VALUE(csp.stream_name)       AS stream_name,
      ANY_VALUE(csp.stream_start_time) AS start_ts,
      ANY_VALUE(csp.stream_end_time)   AS end_ts,
      ANY_VALUE(csp.stream_start_date) AS date,
      ANY_VALUE(csp.country)           AS language,
      ANY_VALUE(csp.site)              AS site,
      ANY_VALUE(csp.currency)          AS currency,
      ANY_VALUE(csp.supplier)          AS supplier,

      -- NS
      SUM(csp.follow_bet_count) AS follow_streamer_bet_count,
      SUM(IFNULL(csp.tip_amount_rm, 0)
        + IFNULL(csp.box_amount_rm, 0)
        + IFNULL(csp.wheel_amount_rm, 0)) / 4.2 AS donation_amount_usd,

      -- L1
      SUM(csp.follow_member_to) AS follow_streamer_bet_turnover,
      COUNT(DISTINCT IF(csp.follow_bet_count > 0, csp.cust_id, NULL)) AS follow_user_count,
      COUNT(DISTINCT IF(csp.if_tip IS TRUE OR csp.if_box IS TRUE OR csp.if_wheel IS TRUE, csp.cust_id, NULL)) AS donation_user_count,
      SUM(csp.tip_amount_rm)  / 4.2 AS tip_amount_usd,
      SUM(csp.tip_count)            AS tip_count,
      COUNT(DISTINCT IF(csp.if_tip IS TRUE, csp.cust_id, NULL)) AS tip_user_count,
      SUM(csp.box_amount_rm)  / 4.2 AS box_amount_usd,
      SUM(csp.box_count)            AS box_count,
      SUM(csp.wheel_amount_rm)/ 4.2 AS wheel_amount_usd,
      SUM(csp.wheel_count)          AS wheel_count,

      -- L2 totals
      SUM(csp.bet_count)        AS total_bet_count,
      SUM(csp.member_to)        AS total_bet_turnover,

      -- L2 bet during watch (aliased bdw_*)
      SUM(csp.during_watch_bet_count)        AS bdw_bet_count,
      SUM(csp.during_watch_member_to)        AS bdw_turnover,

      -- Watch
      SUM(csp.watch_sec) / 60 AS watch_min,
      COUNT(DISTINCT IF(csp.if_watch IS TRUE, csp.cust_id, NULL)) AS viewers,
      COUNT(DISTINCT CASE WHEN csp.watch_sec >= 600 THEN csp.cust_id END) AS watch_over_10min_user,
      COUNT(DISTINCT IF(csp.if_chat IS TRUE, csp.cust_id, NULL)) AS chat_user,
      SUM(csp.message_count) AS message_count,                         -- Fixed trailing comma
      MAX(mcm.call_pcu) AS PCU
    FROM `nf-bifrost.livestream_dm.core_streaming_performance` csp
    JOIN `nf-bifrost.livestream_dm.mart_comprehensive_metrics` mcm 
      ON csp.stream_id = mcm.stream_id 
     AND csp.streamer = mcm.streamer    
     AND csp.anchor_id = mcm.anchor_id                               -- Explicit join prevents ambiguity
    WHERE csp.is_lic = 1
      AND csp.is_shared IS TRUE
      AND csp.is_cancelled IS FALSE
      AND csp.site_id != 99                                   
      AND csp.streamer NOT IN ('Popo', 'GOKU', 'ID_0')
      AND csp.streamer != 'ID_N/A'
      AND csp.streamer NOT LIKE 'ID_%'
      AND csp.currency != 'UUS' AND csp.currency_id != 20
      AND csp.stream_start_date BETWEEN DATE '2026-06-01' AND DATE '2026-07-31'
    GROUP BY csp.stream_id
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
        WHEN EXTRACT(HOUR FROM KickOffTime) BETWEEN 20 AND 23 THEN 'prime'
        WHEN EXTRACT(HOUR FROM KickOffTime) BETWEEN 0  AND 5  THEN 'late_night'
        WHEN EXTRACT(HOUR FROM KickOffTime) BETWEEN 6  AND 11 THEN 'morning'
        ELSE 'afternoon'
      END AS time_slot_taipei,
      EXTRACT(DAYOFWEEK FROM DATE(KickOffTime)) AS day_of_week
    FROM `nf-bifrost.LiveStreaming.match_info`
    WHERE isCancelled = FALSE
      -- TODO [Q3]: confirmed World Cup string
      AND (League LIKE '%WORLD CUP 2026%' OR League LIKE '%WORLD CUP 2026%')
  )

SELECT
  CURRENT_DATE('Asia/Taipei') AS as_of_date,
  s.stream_id,
  s.streamer_id,
  s.streamer,
  s.date,
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
  s.follow_streamer_bet_turnover,
  s.follow_user_count,
  s.donation_user_count,
  s.tip_amount_usd,
  s.tip_count,
  s.tip_user_count,
  -- L2 — bet during watch + watch
  s.bdw_bet_count,
  s.bdw_turnover,
  s.watch_min,
  s.viewers,
  s.watch_over_10min_user,
  SAFE_DIVIDE(s.watch_min, s.viewers) AS watch_min_per_viewer,
  s.pcu,
  -- Engagement extras
  s.chat_user,
  s.message_count,
  s.box_amount_usd,
  s.wheel_amount_usd,
  -- Raw totals
  s.total_bet_count,
  s.total_bet_turnover
FROM by_stream s
LEFT JOIN match_dim m
  ON m.AnchorId = s.streamer_id
 AND m.KickOffTime BETWEEN TIMESTAMP_SUB(s.start_ts, INTERVAL 1 HOUR) AND s.end_ts
LEFT JOIN by_stream_recommend r
  ON r.streamer_id = s.streamer_id
 AND r.SabaMatchId = m.SabaMatchId;
