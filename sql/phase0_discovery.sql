-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- Phase 0 — BQ Discovery (targeted probes only — schemas are known)
-- Project: nf-bifrost
-- Probes apply bq-filter-rules: is_lic=1, is_shared IS TRUE, is_cancelled IS FALSE,
-- chatroom (site_id != 99), non-bot streamers, non-test currency.

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
-- 2. fact_live_bet.follow_type distinct values
--    Identifies the string for "Follow System" category in agg_session_metrics.
-- ============================================================
SELECT
  follow_type,
  bet_type,
  COUNT(*) AS n,
  SUM(member_to) AS total_turnover
FROM `nf-bifrost.livestream_dm.fact_live_bet`
WHERE DATE(trans_dt, 'Asia/Taipei') >= DATE '2026-05-01'
GROUP BY follow_type, bet_type
ORDER BY n DESC
LIMIT 50;

-- ============================================================
-- 3. World Cup filter — exact League / LeagueGroup string
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
-- 4. is_lic meaning + status_id breakdown
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
-- 5. Sanity: csp ↔ match_info join coverage
--    How many streams have a matching World Cup fixture via (anchor + kickoff in window)?
-- ============================================================
WITH wc AS (
  SELECT SabaMatchId, AnchorId, KickOffTime
  FROM `nf-bifrost.LiveStreaming.match_info`
  WHERE isCancelled = FALSE
    AND (LeagueGroup LIKE '%World Cup%' OR League LIKE '%World Cup%')
)
SELECT
  COUNT(DISTINCT csp.stream_id) AS world_cup_stream_count,
  COUNT(DISTINCT IF(wc.SabaMatchId IS NULL, csp.stream_id, NULL)) AS streams_without_match
FROM `nf-bifrost.livestream_dm.core_streaming_performance` csp
LEFT JOIN wc
  ON wc.AnchorId = csp.anchor_id
 AND wc.KickOffTime BETWEEN TIMESTAMP_SUB(csp.stream_start_time, INTERVAL 1 HOUR)
                       AND csp.stream_end_time
WHERE csp.stream_start_date BETWEEN '2026-06-01' AND '2026-07-31'
  AND csp.is_cancelled = FALSE;
