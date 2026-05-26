-- Region: asia-southeast1 (Singapore). Run with --location=asia-southeast1.
-- dim_streamer — streamer attributes for filtering / cohort comparison
-- Source: nf-bifrost.LiveStreaming.chatroom_anchor + history from core_streaming_performance.
-- Applies bq-filter-rules to history source (chatroom, non-bot, valid streams).

CREATE OR REPLACE TABLE `nf-muses.worldcup.dim_streamer` AS
WITH
  stream_stats AS (
    SELECT
      anchor_id,
      COUNT(DISTINCT CASE WHEN anchor_id != 0
        THEN CONCAT(CAST(anchor_id AS STRING), '-', CAST(stream_id AS STRING))
      END) AS lifetime_stream_count,
      SUM(follow_bet_count) AS lifetime_ns_follow_bet_count
    FROM `nf-bifrost.livestream_dm.core_streaming_performance`
    WHERE is_lic = 1
      AND is_shared IS TRUE
      AND is_cancelled IS FALSE
      AND site_id != 99
      AND streamer NOT IN ('Popo', 'GOKU', 'ID_0')
      AND streamer != 'ID_N/A'
      AND streamer NOT LIKE 'ID_%'
      AND currency != 'UUS' AND currency_id != 20
    GROUP BY anchor_id
  ),

  p80 AS (
    SELECT APPROX_QUANTILES(lifetime_ns_follow_bet_count, 100)[OFFSET(80)] AS p80_ns
    FROM stream_stats
  )

SELECT
  a.Id   AS streamer_id,
  a.Name AS streamer_name,
  a.Provider AS supplier,
  a.Language,
  a.Status,
  COALESCE(ss.lifetime_stream_count, 0)        AS lifetime_stream_count,
  COALESCE(ss.lifetime_ns_follow_bet_count, 0) AS lifetime_ns_follow_bet_count,
  CASE
    WHEN COALESCE(ss.lifetime_stream_count, 0) < 5 THEN 'rookie'
    WHEN COALESCE(ss.lifetime_stream_count, 0) > 50
      OR COALESCE(ss.lifetime_ns_follow_bet_count, 0) >= (SELECT p80_ns FROM p80)
      THEN 'top'
    ELSE 'regular'
  END AS tier
FROM `nf-bifrost.LiveStreaming.chatroom_anchor` a
LEFT JOIN stream_stats ss ON a.Id = ss.anchor_id
WHERE a.Name NOT IN ('Popo', 'GOKU', 'ID_0')
  AND a.Name != 'ID_N/A'
  AND a.Name NOT LIKE 'ID_%';
