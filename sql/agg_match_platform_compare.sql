-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- agg_match_platform_compare — Scope A (per match) + Scope B (per stream window)
--
-- Applies bq-filter-rules: is_lic=1, is_shared IS TRUE, is_cancelled IS FALSE,
-- site_id != 99, bot/placeholder streamer exclusions. Currency converted to USD.
-- "Our" = during-watch bets (csp.during_watch_*). "Platform" = all non-voided fact_live_bet.
--
-- TODOs:
--   [Q2] fact_live_bet.status_id — confirm settled values
--   [Q3] World Cup filter string
--   [Q5] csp ↔ match_info join key

CREATE OR REPLACE TABLE `nf-muses.reporting.agg_match_platform_compare`
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

  filtered_csp AS (
    SELECT *
    FROM `nf-bifrost.livestream_dm.core_streaming_performance`
    WHERE is_lic = 1
      AND is_shared IS TRUE
      AND is_cancelled IS FALSE
      AND site_id != 99
      AND streamer NOT IN ('Popo', 'GOKU', 'ID_0')
      AND streamer != 'ID_N/A'
      AND streamer NOT LIKE 'ID_%'
      AND currency != 'UUS' AND currency_id != 20
      AND DATE(stream_start_time, 'Asia/Taipei') BETWEEN DATE '2026-06-01' AND DATE '2026-07-31'
  ),

  -- =============================
  -- Scope A — per match
  -- =============================
  our_by_match AS (
    SELECT
      m.SabaMatchId,
      SUM(csp.during_watch_bet_count)       AS our_bdw_bet_count,
      SUM(csp.during_watch_member_to) / 4.2 AS our_bdw_turnover_usd
    FROM filtered_csp csp
    JOIN wc_matches m
      ON m.AnchorId = csp.anchor_id
     AND m.KickOffTime BETWEEN TIMESTAMP_SUB(csp.stream_start_time, INTERVAL 1 HOUR)
                          AND csp.stream_end_time
    GROUP BY m.SabaMatchId
  ),

  platform_by_match AS (
    SELECT
      m.SabaMatchId,
      COUNT(*)                  AS platform_bet_count,
      SUM(fb.member_to) / 4.2   AS platform_bet_turnover_usd
    FROM `nf-bifrost.livestream_dm.fact_live_bet` fb
    JOIN wc_matches m ON m.SabaMatchId = fb.match_id  -- TODO confirm join
    -- TODO [Q2]: AND fb.status_id IN (...settled set...)
    GROUP BY m.SabaMatchId
  ),

  scope_a AS (
    SELECT
      m.SabaMatchId,
      m.HomeCnName,
      m.AwayCnName,
      m.KickOffTime,
      COALESCE(o.our_bdw_bet_count,    0) AS our_bdw_bet_count,
      COALESCE(o.our_bdw_turnover_usd, 0) AS our_bdw_turnover_usd,
      COALESCE(p.platform_bet_count,        0) AS platform_bet_count,
      COALESCE(p.platform_bet_turnover_usd, 0) AS platform_bet_turnover_usd,
      SAFE_DIVIDE(o.our_bdw_turnover_usd, p.platform_bet_turnover_usd) AS match_share_of_wallet,
      SAFE_DIVIDE(o.our_bdw_bet_count,    p.platform_bet_count)        AS match_share_of_bets,
      SAFE_DIVIDE(o.our_bdw_turnover_usd, NULLIF(o.our_bdw_bet_count, 0))         AS our_avg_bet_size_usd,
      SAFE_DIVIDE(p.platform_bet_turnover_usd, NULLIF(p.platform_bet_count, 0))   AS platform_avg_bet_size_usd
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
      SUM(during_watch_bet_count)       AS our_window_bdw_bet_count,
      SUM(during_watch_member_to) / 4.2 AS our_window_bdw_turnover_usd
    FROM filtered_csp
    GROUP BY stream_id, anchor_id, stream_start_time, stream_end_time
  ),

  platform_by_session AS (
    SELECT
      s.stream_id,
      COUNT(fb.trans_id)        AS platform_window_bet_count,
      SUM(fb.member_to) / 4.2   AS platform_window_bet_turnover_usd
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
      s.our_window_bdw_bet_count,
      s.our_window_bdw_turnover_usd,
      p.platform_window_bet_count,
      p.platform_window_bet_turnover_usd,
      SAFE_DIVIDE(s.our_window_bdw_turnover_usd, p.platform_window_bet_turnover_usd) AS time_window_share_turnover,
      SAFE_DIVIDE(s.our_window_bdw_bet_count,    p.platform_window_bet_count)        AS time_window_share_count
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
  a.our_bdw_bet_count,
  a.our_bdw_turnover_usd,
  a.our_avg_bet_size_usd,
  a.platform_bet_count,
  a.platform_bet_turnover_usd,
  a.platform_avg_bet_size_usd,
  a.match_share_of_wallet,
  a.match_share_of_bets,
  b.avg_time_window_share_turnover,
  b.avg_time_window_share_count
FROM scope_a a
LEFT JOIN scope_b_by_match b USING (SabaMatchId);
