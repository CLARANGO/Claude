-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- agg_match_platform_compare — Scope A (per match) + Scope B (full-season aggregate)
--
-- Applies bq-filter-rules: is_lic=1, is_shared IS TRUE, is_cancelled IS FALSE,
-- site_id != 99, bot/placeholder streamer exclusions.
-- Currency: turnover in RM (raw member_to); no /4.2.
-- "Our" = during-watch bets (csp.during_watch_*). "Platform" = all non-voided fact_live_bet
-- restricted to sites with streamer function (see STREAMER_SITE_IDS placeholder).
--
-- Season window: 2026-06-11 → 2026-07-20 (World Cup 2026 kickoff → final).
--
-- TODOs:
--   [Q2] fact_live_bet.status_id — confirm settled values
--   [Q3] World Cup filter string in match_info.League (likely 'WORLD CUP')
--   [SITES] populate STREAMER_SITE_IDS with the site IDs that expose streamer function

CREATE OR REPLACE TABLE `nf-muses.worldcup.agg_match_platform_compare`
PARTITION BY as_of_date
CLUSTER BY SabaMatchId
AS
WITH
  -- Site IDs that have the streamer function (Scope A platform side restricted to these).
  -- TODO: replace with the real list.
  streamer_sites AS (
    SELECT * FROM UNNEST([CAST(NULL AS INT64)]) AS site_id WHERE site_id IS NOT NULL
  ),

  wc_matches AS (
    SELECT SabaMatchId, AnchorId, KickOffTime, HomeCnName, AwayCnName, League, LeagueGroup
    FROM `nf-bifrost.LiveStreaming.match_info`
    WHERE isCancelled = FALSE
      AND UPPER(League) LIKE '%WORLD CUP%'
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
      AND DATE(stream_start_time, 'Asia/Taipei') BETWEEN DATE '2026-06-11' AND DATE '2026-07-20'
  ),

  -- =============================
  -- Scope A — per match (Saba-match level)
  -- =============================
  our_by_match AS (
    SELECT
      m.SabaMatchId,
      SUM(csp.during_watch_bet_count) AS our_bdw_bet_count,
      SUM(csp.during_watch_member_to) AS our_bdw_turnover_rm
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
      COUNT(*)          AS platform_bet_count,
      SUM(fb.member_to) AS platform_bet_turnover_rm
    FROM `nf-bifrost.livestream_dm.fact_live_bet` fb
    JOIN wc_matches m ON m.SabaMatchId = fb.match_id  -- TODO confirm join
    -- Restrict platform side to sites with streamer function; comment out until streamer_sites populated.
    -- WHERE fb.site_id IN (SELECT site_id FROM streamer_sites)
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
      COALESCE(o.our_bdw_turnover_rm,  0) AS our_bdw_turnover_rm,
      COALESCE(p.platform_bet_count,       0) AS platform_bet_count,
      COALESCE(p.platform_bet_turnover_rm, 0) AS platform_bet_turnover_rm,
      SAFE_DIVIDE(o.our_bdw_turnover_rm, p.platform_bet_turnover_rm) AS match_share_of_wallet,
      SAFE_DIVIDE(o.our_bdw_bet_count,   p.platform_bet_count)       AS match_share_of_bets,
      SAFE_DIVIDE(o.our_bdw_turnover_rm, NULLIF(o.our_bdw_bet_count, 0))         AS our_avg_bet_size_rm,
      SAFE_DIVIDE(p.platform_bet_turnover_rm, NULLIF(p.platform_bet_count, 0))   AS platform_avg_bet_size_rm
    FROM wc_matches m
    LEFT JOIN our_by_match o      USING (SabaMatchId)
    LEFT JOIN platform_by_match p USING (SabaMatchId)
  ),

  -- =============================
  -- Scope B — season-window aggregate (single ratio across the full World Cup)
  -- =============================
  season_our AS (
    SELECT
      SUM(during_watch_bet_count) AS our_season_bdw_bet_count,
      SUM(during_watch_member_to) AS our_season_bdw_turnover_rm
    FROM filtered_csp
  ),

  season_platform AS (
    SELECT
      COUNT(*)          AS platform_season_bet_count,
      SUM(fb.member_to) AS platform_season_bet_turnover_rm
    FROM `nf-bifrost.livestream_dm.fact_live_bet` fb
    WHERE DATE(fb.trans_dt, 'Asia/Taipei') BETWEEN DATE '2026-06-11' AND DATE '2026-07-20'
    -- TODO [Q2]: AND fb.status_id IN (...settled set...)
  ),

  scope_b AS (
    SELECT
      o.our_season_bdw_bet_count,
      o.our_season_bdw_turnover_rm,
      p.platform_season_bet_count,
      p.platform_season_bet_turnover_rm,
      SAFE_DIVIDE(o.our_season_bdw_turnover_rm, p.platform_season_bet_turnover_rm) AS season_share_of_wallet,
      SAFE_DIVIDE(o.our_season_bdw_bet_count,   p.platform_season_bet_count)       AS season_share_of_bets
    FROM season_our o, season_platform p
  )

SELECT
  CURRENT_DATE('Asia/Taipei') AS as_of_date,
  a.SabaMatchId,
  a.HomeCnName,
  a.AwayCnName,
  a.KickOffTime,
  a.our_bdw_bet_count,
  a.our_bdw_turnover_rm,
  a.our_avg_bet_size_rm,
  a.platform_bet_count,
  a.platform_bet_turnover_rm,
  a.platform_avg_bet_size_rm,
  a.match_share_of_wallet,
  a.match_share_of_bets,
  -- Season-window aggregate — same value repeats on every row (single scalar).
  b.our_season_bdw_bet_count,
  b.our_season_bdw_turnover_rm,
  b.platform_season_bet_count,
  b.platform_season_bet_turnover_rm,
  b.season_share_of_wallet,
  b.season_share_of_bets
FROM scope_a a
CROSS JOIN scope_b b;
