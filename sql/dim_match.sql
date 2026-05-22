-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- dim_match — World Cup 2026 fixtures with comparability tags
-- Source: nf-bifrost.LiveStreaming.match_info, filtered to World Cup.
--
-- match_stage and team_popularity_tier are NOT in match_info — added via lookup tables.
-- The stage lookup is keyed by SabaMatchId (one row per fixture) and must be maintained
-- manually as the bracket fills out. Tier is keyed by team name (CN).
--
-- TODO [Q3]: confirm the exact World Cup filter string.

CREATE OR REPLACE TABLE `nf-bifrost.reporting.dim_match` AS
WITH
  base AS (
    SELECT
      SabaMatchId,
      AnchorId,
      KickOffTime,
      League,
      LeagueGroup,
      HomeCnName,
      AwayCnName,
      Supplier,
      IsSelfOwned
    FROM `nf-bifrost.LiveStreaming.match_info`
    WHERE isCancelled = FALSE
      AND (LeagueGroup LIKE '%World Cup%' OR League LIKE '%World Cup%')
  ),

  -- Team popularity tier (CN names — update from match_info distinct values after probe)
  tier_map AS (
    SELECT * FROM UNNEST([
      STRUCT('巴西' AS team, 1 AS tier), ('阿根廷', 1), ('英格蘭', 1), ('法國', 1),
      ('德國', 1), ('西班牙', 1), ('葡萄牙', 1), ('荷蘭', 1), ('意大利', 1),
      ('比利時', 2), ('克羅地亞', 2), ('烏拉圭', 2), ('墨西哥', 2), ('美國', 2),
      ('日本', 2), ('韓國', 2), ('瑞士', 2), ('丹麥', 2),
      ('波蘭', 2), ('塞內加爾', 2), ('摩洛哥', 2), ('澳大利亞', 2)
      -- everything else defaults to tier 3
    ])
  ),

  -- Match stage lookup — fill in as fixtures finalize. Defaults to 'group' for early matches.
  -- TODO: populate this lookup with the 64 World Cup fixtures.
  stage_map AS (
    SELECT * FROM UNNEST([
      STRUCT(CAST(NULL AS STRING) AS SabaMatchId, CAST(NULL AS STRING) AS match_stage)
    ])
    WHERE SabaMatchId IS NOT NULL
  )

SELECT
  b.SabaMatchId,
  b.AnchorId,
  b.KickOffTime,
  b.League,
  b.LeagueGroup,
  b.HomeCnName,
  b.AwayCnName,
  COALESCE(sm.match_stage, 'group') AS match_stage,
  LEAST(COALESCE(th.tier, 3), COALESCE(ta.tier, 3)) AS team_popularity_tier,
  CASE
    WHEN EXTRACT(HOUR FROM b.KickOffTime AT TIME ZONE 'Asia/Taipei') BETWEEN 20 AND 23 THEN 'prime'
    WHEN EXTRACT(HOUR FROM b.KickOffTime AT TIME ZONE 'Asia/Taipei') BETWEEN 0  AND 5  THEN 'late_night'
    WHEN EXTRACT(HOUR FROM b.KickOffTime AT TIME ZONE 'Asia/Taipei') BETWEEN 6  AND 11 THEN 'morning'
    ELSE 'afternoon'
  END AS time_slot_taipei,
  EXTRACT(DAYOFWEEK FROM DATE(b.KickOffTime, 'Asia/Taipei')) AS day_of_week
FROM base b
LEFT JOIN tier_map th ON th.team = b.HomeCnName
LEFT JOIN tier_map ta ON ta.team = b.AwayCnName
LEFT JOIN stage_map sm ON sm.SabaMatchId = b.SabaMatchId;
