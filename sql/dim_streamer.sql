-- dim_streamer — streamer attributes for filtering / cohort comparison
-- Source: nf-bifrost.LiveStreaming.chatroom_anchor + history from core_streaming_performance.
--
-- Tier rules (heuristic):
--   rookie  — <5 lifetime streams
--   regular — 5–50 lifetime streams
--   top     — >50 lifetime streams OR in top 20% by lifetime NS Follow Streamer Bet Count

CREATE OR REPLACE TABLE `nf-bifrost.reporting.dim_streamer` AS
WITH
  stream_stats AS (
    SELECT
      anchor_id,
      COUNT(DISTINCT stream_id) AS lifetime_stream_count,
      SUM(follow_bet_count)     AS lifetime_ns_follow_bet_count
    FROM `nf-bifrost.livestream_dm.core_streaming_performance`
    WHERE is_cancelled = FALSE
    GROUP BY anchor_id
  ),

  p80 AS (
    SELECT APPROX_QUANTILES(lifetime_ns_follow_bet_count, 100)[OFFSET(80)] AS p80_ns
    FROM stream_stats
  )

SELECT
  a.Id AS streamer_id,
  a.Name AS streamer_name,
  a.Provider,
  a.Language,
  a.Status,
  COALESCE(ss.lifetime_stream_count, 0) AS lifetime_stream_count,
  COALESCE(ss.lifetime_ns_follow_bet_count, 0) AS lifetime_ns_follow_bet_count,
  CASE
    WHEN COALESCE(ss.lifetime_stream_count, 0) < 5 THEN 'rookie'
    WHEN COALESCE(ss.lifetime_stream_count, 0) > 50
      OR COALESCE(ss.lifetime_ns_follow_bet_count, 0) >= (SELECT p80_ns FROM p80)
      THEN 'top'
    ELSE 'regular'
  END AS tier
FROM `nf-bifrost.LiveStreaming.chatroom_anchor` a
LEFT JOIN stream_stats ss ON a.Id = ss.anchor_id;
