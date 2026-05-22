-- agg_match_platform_compare — match-level (Scope A) + time-window aggregate (Scope B)
-- Our product bet = "Bet During Watch" (user watching the stream + bet placed in same window)
-- Platform bet    = all non-voided bets on the platform (no stream attribution required)
--
-- Scope A: per match — our metrics vs platform metrics on the *same match*
-- Scope B: per stream session — our metrics vs platform total during the *same time window*
--          (across all matches that were open during that window)

CREATE OR REPLACE TABLE `__PROJECT__.reporting.agg_match_platform_compare`
PARTITION BY as_of_date
CLUSTER BY match_id
AS
WITH
  clean_bets AS (
    SELECT b.*
    FROM `__RAW_BETS__` b
    LEFT JOIN `__RAW_USERS__` u USING (user_id)
    WHERE b.status NOT IN ('voided', 'cancelled')
  ),

  -- Our product: only bets placed while user was watching the stream
  our_bet_during_watch AS (
    SELECT
      b.bet_id,
      b.user_id,
      b.stream_id,
      b.match_id,
      b.turnover,
      b.placed_ts,
      w.watch_start_ts,
      w.watch_end_ts
    FROM clean_bets b
    JOIN `__RAW_WATCH__` w
      ON b.user_id = w.user_id
     AND b.stream_id = w.stream_id
     AND b.placed_ts BETWEEN w.watch_start_ts AND w.watch_end_ts
    LEFT JOIN `__RAW_USERS__` u USING (user_id)
    WHERE COALESCE(u.is_bot_flag, FALSE) = FALSE
      AND COALESCE(u.multi_account_flag, FALSE) = FALSE
  ),

  -- =============================
  -- Scope A: match-level
  -- =============================
  our_by_match AS (
    SELECT
      match_id,
      COUNT(*) AS our_bet_count,
      SUM(turnover) AS our_bet_turnover,
      SAFE_DIVIDE(SUM(turnover), COUNT(*)) AS our_avg_bet_size
    FROM our_bet_during_watch
    GROUP BY match_id
  ),

  platform_by_match AS (
    SELECT
      match_id,
      COUNT(*) AS platform_bet_count,
      SUM(turnover) AS platform_bet_turnover,
      SAFE_DIVIDE(SUM(turnover), COUNT(*)) AS platform_avg_bet_size
    FROM clean_bets
    GROUP BY match_id
  ),

  scope_a AS (
    SELECT
      p.match_id,
      o.our_bet_count,
      o.our_bet_turnover,
      o.our_avg_bet_size,
      p.platform_bet_count,
      p.platform_bet_turnover,
      p.platform_avg_bet_size,
      SAFE_DIVIDE(o.our_bet_turnover, p.platform_bet_turnover) AS match_share_of_wallet,
      SAFE_DIVIDE(o.our_bet_count,    p.platform_bet_count)    AS match_share_of_bets
    FROM platform_by_match p
    LEFT JOIN our_by_match o USING (match_id)
  ),

  -- =============================
  -- Scope B: time-window aggregate (per stream session)
  -- =============================
  sessions AS (
    SELECT stream_id, streamer_id, match_id, start_ts, end_ts
    FROM `__RAW_STREAMS__`
  ),

  our_by_session AS (
    SELECT
      s.stream_id,
      s.match_id,
      COUNT(*) AS our_window_bet_count,
      SUM(b.turnover) AS our_window_bet_turnover
    FROM sessions s
    LEFT JOIN our_bet_during_watch b
      ON b.stream_id = s.stream_id
     AND b.placed_ts BETWEEN s.start_ts AND s.end_ts
    GROUP BY s.stream_id, s.match_id
  ),

  platform_by_session AS (
    -- Platform total across ALL matches during the session window
    SELECT
      s.stream_id,
      COUNT(*) AS platform_window_bet_count,
      SUM(b.turnover) AS platform_window_bet_turnover
    FROM sessions s
    LEFT JOIN clean_bets b
      ON b.placed_ts BETWEEN s.start_ts AND s.end_ts
    GROUP BY s.stream_id
  ),

  scope_b AS (
    SELECT
      o.stream_id,
      o.match_id,
      o.our_window_bet_count,
      o.our_window_bet_turnover,
      p.platform_window_bet_count,
      p.platform_window_bet_turnover,
      SAFE_DIVIDE(o.our_window_bet_turnover, p.platform_window_bet_turnover) AS time_window_share_turnover,
      SAFE_DIVIDE(o.our_window_bet_count,    p.platform_window_bet_count)    AS time_window_share_count
    FROM our_by_session o
    LEFT JOIN platform_by_session p USING (stream_id)
  ),

  -- Roll Scope B up to match_id (avg share across sessions covering the same match)
  scope_b_by_match AS (
    SELECT
      match_id,
      AVG(time_window_share_turnover) AS avg_time_window_share_turnover,
      AVG(time_window_share_count)    AS avg_time_window_share_count
    FROM scope_b
    GROUP BY match_id
  )

SELECT
  CURRENT_DATE('Asia/Taipei') AS as_of_date,
  a.match_id,
  -- Scope A
  a.our_bet_count,
  a.our_bet_turnover,
  a.our_avg_bet_size,
  a.platform_bet_count,
  a.platform_bet_turnover,
  a.platform_avg_bet_size,
  a.match_share_of_wallet,
  a.match_share_of_bets,
  -- Scope B (averaged across sessions on this match)
  b.avg_time_window_share_turnover,
  b.avg_time_window_share_count
FROM scope_a a
LEFT JOIN scope_b_by_match b USING (match_id);
