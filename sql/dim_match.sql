-- dim_match — World Cup 2026 fixtures with comparability tags
-- If an internal matches table exists, prefer that; else seed manually from FIFA fixture list.
--
-- Tags:
--   match_stage           — 'group' | 'r16' | 'qf' | 'sf' | '3rd_place' | 'final'
--   team_popularity_tier  — 1 (top draws) / 2 / 3
--   time_slot_taipei      — 'prime' (20:00–24:00) | 'late_night' (00:00–06:00)
--                           'morning' (06:00–12:00) | 'afternoon' (12:00–20:00)
--   day_of_week           — 1..7 (Mon=1)

CREATE OR REPLACE TABLE `__PROJECT__.reporting.dim_match` AS
WITH base AS (
  -- Option A: pull from internal raw matches table if present
  SELECT
    match_id,
    kickoff_ts,
    team_home,
    team_away,
    stage AS match_stage
  FROM `__RAW_MATCHES__`
  -- Option B: replace the SELECT above with a literal VALUES list seeded from FIFA fixtures
  --   SELECT * FROM UNNEST([STRUCT('M001' AS match_id, TIMESTAMP '2026-06-11 19:00:00 UTC' AS kickoff_ts,
  --                                 'Mexico' AS team_home, 'Canada' AS team_away, 'group' AS match_stage), ...])
),

tier_map AS (
  SELECT * FROM UNNEST([
    -- Tier 1: historical top bet-volume nations
    STRUCT('Brazil' AS team, 1 AS tier), ('Argentina', 1), ('England', 1), ('France', 1),
    ('Germany', 1), ('Spain', 1), ('Portugal', 1), ('Netherlands', 1), ('Italy', 1),
    -- Tier 2: strong but secondary draws
    ('Belgium', 2), ('Croatia', 2), ('Uruguay', 2), ('Mexico', 2), ('USA', 2),
    ('Japan', 2), ('Korea Republic', 2), ('Switzerland', 2), ('Denmark', 2),
    ('Poland', 2), ('Senegal', 2), ('Morocco', 2), ('Australia', 2)
    -- Everyone else defaults to tier 3
  ])
)

SELECT
  b.match_id,
  b.kickoff_ts,
  b.team_home,
  b.team_away,
  b.match_stage,
  LEAST(COALESCE(th.tier, 3), COALESCE(ta.tier, 3)) AS team_popularity_tier,
  CASE
    WHEN EXTRACT(HOUR FROM b.kickoff_ts AT TIME ZONE 'Asia/Taipei') BETWEEN 20 AND 23 THEN 'prime'
    WHEN EXTRACT(HOUR FROM b.kickoff_ts AT TIME ZONE 'Asia/Taipei') BETWEEN 0  AND 5  THEN 'late_night'
    WHEN EXTRACT(HOUR FROM b.kickoff_ts AT TIME ZONE 'Asia/Taipei') BETWEEN 6  AND 11 THEN 'morning'
    ELSE 'afternoon'
  END AS time_slot_taipei,
  EXTRACT(DAYOFWEEK FROM DATE(b.kickoff_ts, 'Asia/Taipei')) AS day_of_week
FROM base b
LEFT JOIN tier_map th ON th.team = b.team_home
LEFT JOIN tier_map ta ON ta.team = b.team_away;
