-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- agg_match_platform_compare — match-level (Scope A) + time-window aggregate (Scope B)
--
-- Scope A: per SabaMatchId, our (during-watch) bet volume vs platform total on that match
-- Scope B: per stream session, our bet volume vs platform total during the stream window
--          (across ALL matches that were live in that window)
--
-- "Our" = during-watch bets (user actively watching the streamer) — sourced from
-- core_streaming_performance.during_watch_* (pre-computed).
-- "Platform" = all non-voided bets from fact_live_bet (no stream/anchor filter).
--
-- TODOs:
--   [Q2] fact_live_bet status filter — confirm valid status_id values for non-voided
--   [Q3] World Cup filter on match_info
--   [Q5] csp ↔ match_info join key

CREATE OR REPLACE TABLE `nf-bifrost.reporting.agg_match_platform_compare`
PARTITION BY as_of_date
CLUSTER BY SabaMatchId
AS
WITH
  wc_matches AS (
    SELECT SabaMatchId, AnchorId, KickOffTime, HomeCnName, AwayCnName, League, LeagueGroup
    FROM `nf-bifrost.LiveStreaming.match_info`
    WHERE isCancelled = FALSE
      AND (LeagueGroup LIKE '%World Cup%' OR League LIKE '%World Cup%')
  ),

  -- =============================
  -- Scope A — per match
  -- =============================
  our_by_match AS (
    SELECT
      m.SabaMatchId,
      SUM(csp.during_watch_bet_count) AS our_bet_count,
      SUM(csp.during_watch_member_to) AS our_bet_turnover
    FROM `nf-bifrost.livestream_dm.core_streaming_performance` csp
    JOIN wc_matches m
      ON m.AnchorId = csp.anchor_id
     AND m.KickOffTime BETWEEN TIMESTAMP_SUB(csp.stream_start_time, INTERVAL 1 HOUR)
                          AND csp.stream_end_time
    WHERE csp.is_cancelled = FALSE
    GROUP BY m.SabaMatchId
  ),

  platform_by_match AS (
    SELECT
      m.SabaMatchId,
      COUNT(*)         AS platform_bet_count,
      SUM(fb.member_to) AS platform_bet_turnover
    FROM `nf-bifrost.livestream_dm.fact_live_bet` fb
    JOIN wc_matches m ON m.SabaMatchId = fb.match_id  -- TODO confirm fb.match_id == SabaMatchId
    -- TODO [Q2]: add status filter, e.g. WHERE fb.status_id IN (settled set)
    GROUP BY m.SabaMatchId
  ),

  scope_a AS (
    SELECT
      m.SabaMatchId,
      m.HomeCnName,
      m.AwayCnName,
      m.KickOffTime,
      COALESCE(o.our_bet_count,    0) AS our_bet_count,
      COALESCE(o.our_bet_turnover, 0) AS our_bet_turnover,
      COALESCE(p.platform_bet_count,    0) AS platform_bet_count,
      COALESCE(p.platform_bet_turnover, 0) AS platform_bet_turnover,
      SAFE_DIVIDE(o.our_bet_turnover, p.platform_bet_turnover) AS match_share_of_wallet,
      SAFE_DIVIDE(o.our_bet_count,    p.platform_bet_count)    AS match_share_of_bets,
      SAFE_DIVIDE(o.our_bet_turnover, NULLIF(o.our_bet_count, 0))      AS our_avg_bet_size,
      SAFE_DIVIDE(p.platform_bet_turnover, NULLIF(p.platform_bet_count, 0)) AS platform_avg_bet_size
    FROM wc_matches m
    LEFT JOIN our_by_match o      USING (SabaMatchId)
    LEFT JOIN platform_by_match p USING (SabaMatchId)
  ),

  -- =============================
  -- Scope B — per stream window, rolled up to match
  -- =============================
  sessions AS (
    SELECT
      stream_id,
      anchor_id,
      stream_start_time AS start_ts,
      stream_end_time   AS end_ts,
      SUM(during_watch_bet_count) AS our_window_bet_count,
      SUM(during_watch_member_to) AS our_window_bet_turnover
    FROM `nf-bifrost.livestream_dm.core_streaming_performance`
    WHERE is_cancelled = FALSE
      AND DATE(stream_start_time, 'Asia/Taipei') BETWEEN DATE '2026-06-01' AND DATE '2026-07-31'
    GROUP BY stream_id, anchor_id, stream_start_time, stream_end_time
  ),

  platform_by_session AS (
    SELECT
      s.stream_id,
      COUNT(fb.trans_id) AS platform_window_bet_count,
      SUM(fb.member_to)  AS platform_window_bet_turnover
    FROM sessions s
    LEFT JOIN `nf-bifrost.livestream_dm.fact_live_bet` fb
      ON fb.trans_dt BETWEEN s.start_ts AND s.end_ts
    GROUP BY s.stream_id
  ),

  scope_b_per_session AS (
    SELECT
      s.stream_id,
      s.anchor_id,
      s.start_ts,
      s.our_window_bet_count,
      s.our_window_bet_turnover,
      p.platform_window_bet_count,
      p.platform_window_bet_turnover,
      SAFE_DIVIDE(s.our_window_bet_turnover, p.platform_window_bet_turnover) AS time_window_share_turnover,
      SAFE_DIVIDE(s.our_window_bet_count,    p.platform_window_bet_count)    AS time_window_share_count
    FROM sessions s
    LEFT JOIN platform_by_session p USING (stream_id)
  ),

  scope_b_by_match AS (
    SELECT
      m.SabaMatchId,
      AVG(b.time_window_share_turnover) AS avg_time_window_share_turnover,
      AVG(b.time_window_share_count)    AS avg_time_window_share_count
    FROM scope_b_per_session b
    JOIN wc_matches m
      ON m.AnchorId = b.anchor_id
     AND m.KickOffTime BETWEEN TIMESTAMP_SUB(b.start_ts, INTERVAL 1 HOUR)
                          AND TIMESTAMP_ADD(b.start_ts, INTERVAL 4 HOUR)
    GROUP BY m.SabaMatchId
  )

SELECT
  CURRENT_DATE('Asia/Taipei') AS as_of_date,
  a.SabaMatchId,
  a.HomeCnName,
  a.AwayCnName,
  a.KickOffTime,
  a.our_bet_count,
  a.our_bet_turnover,
  a.our_avg_bet_size,
  a.platform_bet_count,
  a.platform_bet_turnover,
  a.platform_avg_bet_size,
  a.match_share_of_wallet,
  a.match_share_of_bets,
  b.avg_time_window_share_turnover,
  b.avg_time_window_share_count
FROM scope_a a
LEFT JOIN scope_b_by_match b USING (SabaMatchId);
