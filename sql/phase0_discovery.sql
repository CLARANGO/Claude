-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- Phase 0 — BQ Discovery (targeted probes only — schemas are known)
-- Project: nf-bifrost
-- Probes apply bq-filter-rules: is_lic=1, is_shared IS TRUE, is_cancelled IS FALSE,
-- chatroom (site_id != 99), non-bot streamers, non-test currency.
--
-- After the user's plan revision, only 4 probes remain (Follow System category
-- and csp↔match_info join-key probes are no longer needed).

-- ============================================================
-- 1. Donation composition: confirm tip + box + wheel are all populated
--    and which ones we should sum into NS "Donation Amount"
-- ============================================================
SELECT
  COUNT(*) AS rows_in_range,
  COUNTIF(if_tip = 1)   AS sessions_with_tip,
  COUNTIF(if_box = 1)   AS sessions_with_box,
  COUNTIF(if_wheel = 1) AS sessions_with_wheel,
  SUM(tip_amount_rm)    AS total_tip_rm,
  SUM(box_amount_rm)    AS total_box_rm,
  SUM(wheel_amount_rm)  AS total_wheel_rm
FROM `nf-bifrost.livestream_dm.core_streaming_performance`
WHERE is_lic = 1
  AND is_shared IS TRUE
  AND is_cancelled IS FALSE
  AND site_id != 99
  AND streamer NOT IN ('Popo','GOKU','ID_0') AND streamer != 'ID_N/A' AND streamer NOT LIKE 'ID_%'
  AND currency != 'UUS' AND currency_id != 20
  AND stream_start_date BETWEEN '2026-05-01' AND CURRENT_DATE('Asia/Taipei');

-- ============================================================
-- 2. World Cup filter — confirm exact League / LeagueGroup string
-- ============================================================
SELECT
  League,
  LeagueGroup,
  LeagueCnName,
  COUNT(*) AS n_matches,
  MIN(KickOffTime) AS first_kick,
  MAX(KickOffTime) AS last_kick
FROM `nf-bifrost.LiveStreaming.match_info`
WHERE KickOffTime >= '2026-06-01' AND KickOffTime < '2026-08-01'
GROUP BY League, LeagueGroup, LeagueCnName
ORDER BY n_matches DESC
LIMIT 50;

-- ============================================================
-- 3. is_lic meaning + status_id breakdown (settled vs voided)
-- ============================================================
SELECT
  is_lic,
  COUNT(*) AS rows,
  COUNT(DISTINCT cust_id) AS distinct_custs
FROM `nf-bifrost.livestream_dm.core_streaming_performance`
WHERE stream_start_date >= DATE '2026-05-01'
GROUP BY is_lic;

SELECT
  status_id,
  COUNT(*) AS n,
  SUM(member_to) AS turnover
FROM `nf-bifrost.livestream_dm.fact_live_bet`
WHERE DATE(trans_dt, 'Asia/Taipei') >= DATE '2026-05-01'
GROUP BY status_id
ORDER BY n DESC;

-- ============================================================
-- 4. Match-stage sanity: distinct kickoff dates for the World Cup
--    Verify the date ranges fit the 7-stage mapping in dim_match.sql
--    (group 6/11-6/28, R32 6/29-7/4, R16 7/5-7/8, QF 7/10-7/12,
--     SF 7/15-7/16, 3rd_place 7/19, final 7/20).
-- ============================================================
SELECT
  DATE(KickOffTime, 'Asia/Taipei') AS kickoff_date_tpe,
  COUNT(*) AS n_fixtures
FROM `nf-bifrost.LiveStreaming.match_info`
WHERE UPPER(League) LIKE '%WORLD CUP%'
  AND isCancelled = FALSE
GROUP BY kickoff_date_tpe
ORDER BY kickoff_date_tpe;
