-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- agg_match_platform_compare — per-match platform betting metrics with
-- streamer / site flags. Joined with our BDW metrics (from agg_session_metrics)
-- inside the Dash app for the three platform-compare views:
--
--   1. Matches having streamer × sites having streamer
--      (our BDW vs streamer_site_* fields)
--   2. Matches having streamer × all sites
--      (our BDW vs overall_* fields)
--   3. All matches × all sites
--      (our BDW vs overall_* fields, no has_streamer filter)
--
-- Also extracted to the "platform record" Sheet tab for Apps Script.

CREATE OR REPLACE TABLE `nf-muses.worldcup.agg_match_platform_compare`
PARTITION BY date
CLUSTER BY match_id
AS
WITH streamer_matches AS (
  -- Distinct match IDs that have a streamer; flag exclusive vs all-site.
  SELECT
    SabaMatchId,
    MAX(CASE WHEN Shared IS FALSE THEN TRUE ELSE FALSE END) AS is_exclusive_site,
    MAX(CASE WHEN Shared IS TRUE  THEN TRUE ELSE FALSE END) AS is_all_site
  FROM `nf-bifrost.LiveStreaming.match_info`
  GROUP BY SabaMatchId
),

streamer_sites AS (
  -- Distinct site IDs that have an open streamer / chatroom function.
  SELECT DISTINCT SiteId AS site_id
  FROM `nf-bifrost.LiveStreaming.chatroom_site`
  WHERE IsOpen
  QUALIFY ROW_NUMBER() OVER (PARTITION BY SiteId ORDER BY Subsidiary, Id DESC) = 1
),

base_data AS (
  SELECT
    DATE(main.date) AS date,
    main.match_id,
    main.match_name,
    main.cust_id,
    main.member_turnover,
    main.member_winlost,
    main.bet_count,
    CASE WHEN sm.SabaMatchId IS NOT NULL THEN TRUE ELSE FALSE END AS has_streamer,
    COALESCE(sm.is_exclusive_site, FALSE) AS exclusive_site,
    COALESCE(sm.is_all_site, FALSE)       AS all_site,
    CASE WHEN ss.site_id IS NOT NULL THEN TRUE ELSE FALSE END AS site_has_streamer
  FROM `nfbifrost-promote-event.euro_cup_real_time_dashboard.sport_performance` AS main
  LEFT JOIN streamer_matches AS sm ON main.match_id = sm.SabaMatchId
  LEFT JOIN streamer_sites   AS ss ON main.site_id  = ss.site_id
  WHERE main.is_live IS TRUE
    AND main.sport_name = 'Soccer'
    AND main.date >= '2026-06-01'
)

SELECT
  date,
  match_id,
  match_name,
  has_streamer,
  exclusive_site,
  all_site,

  -- Overall metrics (all sites)
  SUM(member_turnover)                              AS overall_total_turnover,
  SUM(member_winlost)                               AS overall_total_winlost,
  SUM(bet_count)                                    AS overall_total_bet_count,
  COUNT(DISTINCT cust_id)                           AS overall_bet_user,
  SAFE_DIVIDE(SUM(member_turnover), SUM(bet_count)) AS overall_avg_bet_size_per_ticket,

  -- Streamer-site metrics (only matches with streamer × sites with streamer)
  SUM(CASE WHEN site_has_streamer AND has_streamer THEN member_turnover ELSE 0 END) AS streamer_site_total_turnover,
  SUM(CASE WHEN site_has_streamer AND has_streamer THEN member_winlost  ELSE 0 END) AS streamer_site_total_winlost,
  SUM(CASE WHEN site_has_streamer AND has_streamer THEN bet_count       ELSE 0 END) AS streamer_site_total_bet_count,
  COUNT(DISTINCT CASE WHEN site_has_streamer AND has_streamer THEN cust_id END)     AS streamer_site_bet_user,
  SAFE_DIVIDE(
    SUM(CASE WHEN site_has_streamer AND has_streamer THEN member_turnover ELSE 0 END),
    SUM(CASE WHEN site_has_streamer AND has_streamer THEN bet_count       ELSE 0 END)
  ) AS streamer_site_avg_bet_size

FROM base_data
GROUP BY 1, 2, 3, 4, 5, 6
ORDER BY date DESC, overall_total_turnover DESC;
