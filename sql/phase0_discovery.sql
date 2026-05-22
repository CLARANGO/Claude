-- Phase 0 — BQ Discovery
-- Goal: enumerate raw tables, map them to required metrics, produce a gap report.
-- Replace __PROJECT__ and __DATASET__ before running. Run each block top-to-bottom.

-- ============================================================
-- 1. List all tables in the dataset with row counts + date range
-- ============================================================
SELECT
  table_schema,
  table_name,
  row_count,
  ROUND(size_bytes / POW(1024, 3), 2) AS size_gb,
  TIMESTAMP_MILLIS(creation_time) AS created_at,
  TIMESTAMP_MILLIS(last_modified_time) AS last_modified_at
FROM `__PROJECT__.__DATASET__.__TABLES__`
ORDER BY size_bytes DESC;

-- ============================================================
-- 2. Full schema dump for every table in the dataset
-- ============================================================
SELECT
  table_name,
  ordinal_position,
  column_name,
  data_type,
  is_nullable
FROM `__PROJECT__.__DATASET__.INFORMATION_SCHEMA.COLUMNS`
ORDER BY table_name, ordinal_position;

-- ============================================================
-- 3. Per-candidate-table probes (uncomment & adjust table name per run)
-- ============================================================

-- 3a. Row count + date range for a single table
-- SELECT
--   COUNT(*) AS n_rows,
--   MIN(<timestamp_col>) AS first_event,
--   MAX(<timestamp_col>) AS last_event
-- FROM `__PROJECT__.__DATASET__.<table_name>`;

-- 3b. Sample 5 rows (use TABLESAMPLE for big tables)
-- SELECT *
-- FROM `__PROJECT__.__DATASET__.<table_name>`
-- TABLESAMPLE SYSTEM (1 PERCENT)
-- LIMIT 5;

-- 3c. Distinct-value probe for category/enum columns
-- SELECT <col>, COUNT(*) n
-- FROM `__PROJECT__.__DATASET__.<table_name>`
-- GROUP BY <col>
-- ORDER BY n DESC
-- LIMIT 50;

-- ============================================================
-- 4. Candidate tables to look for (rename as discovered)
-- ============================================================
-- streams / sessions       → stream_id, streamer_id, match_id, start_ts, end_ts
-- bets                     → bet_id, user_id, stream_id, match_id, category, turnover, placed_ts, status
-- recommendations          → rec_id, streamer_id, stream_id, match_id, pick, shown_ts
-- watch_sessions           → user_id, stream_id, watch_start_ts, watch_end_ts
-- follows                  → follower_id, target_id, target_type, created_ts
-- donations                → donation_id, user_id, stream_id, amount, type ('donation'|'tip'), created_ts
-- users                    → user_id, is_bot_flag, multi_account_flag
-- matches                  → match_id, kickoff_ts, team_home, team_away, stage

-- ============================================================
-- 5. Gap-report checklist (fill in after schema dump)
-- ============================================================
-- [ ] Stream session boundaries available?
-- [ ] Bet category column distinguishes self / follow-user / follow-system / follow-streamer?
-- [ ] Recommend-bet link: can we join bet → streamer recommendation?
-- [ ] Watch sessions per user per stream (with start+end ts)?
-- [ ] Donation type field separates tip from non-tip donation?
-- [ ] Voided/cancelled bet status flag present?
-- [ ] Bot/multi-account flags on users?
-- [ ] World Cup fixture table exists OR needs manual build?
-- [ ] Platform-level bets accessible (all bets, not just stream-attributed)?
