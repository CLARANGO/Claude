-- dim_streamer — streamer attributes for filtering/cohort comparison
-- Tier rules (heuristic — adjust after Phase 0):
--   rookie  — joined within last 30 days OR <5 lifetime streams
--   regular — joined 30d+ ago, 5–50 lifetime streams
--   top     — >50 lifetime streams OR in top 20% by total NS Follow Streamer Bet Count

CREATE OR REPLACE TABLE `__PROJECT__.reporting.dim_streamer` AS
WITH stream_stats AS (
  SELECT
    streamer_id,
    COUNT(*) AS lifetime_stream_count,
    SUM(follow_streamer_bet_count) AS lifetime_ns_follow_bet_count
  FROM `__PROJECT__.reporting.agg_session_metrics`
  GROUP BY streamer_id
),

rank_cut AS (
  SELECT
    PERCENTILE_CONT(lifetime_ns_follow_bet_count, 0.8) OVER () AS p80_ns
  FROM stream_stats
  LIMIT 1
)

SELECT
  s.streamer_id,
  s.name,
  s.join_date,
  COALESCE(ss.lifetime_stream_count, 0) AS lifetime_stream_count,
  COALESCE(ss.lifetime_ns_follow_bet_count, 0) AS lifetime_ns_follow_bet_count,
  CASE
    WHEN DATE_DIFF(CURRENT_DATE('Asia/Taipei'), s.join_date, DAY) < 30
      OR COALESCE(ss.lifetime_stream_count, 0) < 5
      THEN 'rookie'
    WHEN COALESCE(ss.lifetime_stream_count, 0) > 50
      OR COALESCE(ss.lifetime_ns_follow_bet_count, 0) >= (SELECT p80_ns FROM rank_cut)
      THEN 'top'
    ELSE 'regular'
  END AS tier
FROM `__RAW_STREAMERS__` s
LEFT JOIN stream_stats ss USING (streamer_id);
