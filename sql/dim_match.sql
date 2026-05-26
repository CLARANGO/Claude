-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- dim_match — World Cup 2026 fixtures with comparability tags
-- Source: nf-bifrost.LiveStreaming.match_info, filtered to World Cup.
--
-- match_stage is derived from KickOffTime (Asia/Taipei) per the locked bracket dates.
--
-- TODO [Q3]: confirm the exact World Cup filter string (likely 'WORLD CUP').

CREATE OR REPLACE TABLE `nf-muses.worldcup.dim_match` AS
WITH base AS (
  SELECT
    SabaMatchId,
    AnchorId,
    KickOffTime,
    League,
    LeagueGroup,
    HomeCnName,
    AwayCnName,
    Supplier,
    IsSelfOwned,
    DATE(KickOffTime, 'Asia/Taipei') AS kickoff_date_tpe
  FROM `nf-bifrost.LiveStreaming.match_info`
  WHERE isCancelled = FALSE
    AND UPPER(League) LIKE '%WORLD CUP%'
)

SELECT
  b.SabaMatchId,
  b.AnchorId,
  b.KickOffTime,
  b.League,
  b.LeagueGroup,
  b.HomeCnName,
  b.AwayCnName,
  -- 7-stage mapping by kickoff date (Asia/Taipei)
  CASE
    WHEN b.kickoff_date_tpe BETWEEN DATE '2026-06-11' AND DATE '2026-06-28' THEN 'group'
    WHEN b.kickoff_date_tpe BETWEEN DATE '2026-06-29' AND DATE '2026-07-04' THEN 'R32'
    WHEN b.kickoff_date_tpe BETWEEN DATE '2026-07-05' AND DATE '2026-07-08' THEN 'R16'
    WHEN b.kickoff_date_tpe BETWEEN DATE '2026-07-10' AND DATE '2026-07-12' THEN 'QF'
    WHEN b.kickoff_date_tpe BETWEEN DATE '2026-07-15' AND DATE '2026-07-16' THEN 'SF'
    WHEN b.kickoff_date_tpe = DATE '2026-07-19' THEN '3rd_place'
    WHEN b.kickoff_date_tpe = DATE '2026-07-20' THEN 'final'
    ELSE 'other'
  END AS match_stage,
  CASE
    WHEN EXTRACT(HOUR FROM b.KickOffTime AT TIME ZONE 'Asia/Taipei') BETWEEN 0  AND 5  THEN 'late_night'
    WHEN EXTRACT(HOUR FROM b.KickOffTime AT TIME ZONE 'Asia/Taipei') BETWEEN 6  AND 11 THEN 'morning'
    ELSE 'afternoon'
  END AS time_slot_taipei,
  EXTRACT(DAYOFWEEK FROM b.kickoff_date_tpe) AS day_of_week,
  b.kickoff_date_tpe
FROM base b;
