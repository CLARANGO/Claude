-- build_tfu_user_monthly.sql
-- Destination : nf-muses.muses.tfu_user_monthly
-- Sources     : nf-bifrost.livestream_dm.core_streaming_performance
--               nf-bifrost.VN_CTS_Data.CTSCustomer
--               nf-bifrost.livestream_dm.fact_live_bet
-- Grain       : cust_id × month  (rolling last 6 months)
-- Run once; refresh monthly before scoring.
--
-- BUGS FIXED vs original placeholder:
--   • cust_id (not user_id) throughout
--   • BigQuery DATE_TRUNC(col, MONTH) syntax (not Postgres 'month' string)
--   • Correct source + destination table references
--   • total_bet_count explicitly includes follow_bet_count per design
--   • breadth_score removed (not computed — excluded from ML feature set)

CREATE OR REPLACE TABLE `nf-muses.muses.tfu_user_monthly`
OPTIONS (
  description = 'Monthly user-behaviour feature table for TFU prediction pipeline. One row per (cust_id, month).'
)
AS

WITH

-- ── Rolling 6-month window ────────────────────────────────────────────────────
date_window AS (
  SELECT month_start
  FROM UNNEST(
    GENERATE_DATE_ARRAY(
      DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 6 MONTH), MONTH),
      DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH),
      INTERVAL 1 MONTH
    )
  ) AS month_start
),

-- ── Core session aggregates ───────────────────────────────────────────────────
base_sessions AS (
  SELECT
    cust_id,
    DATE_TRUNC(session_date, MONTH)                          AS month,
    COUNT(DISTINCT session_id)                               AS session_count,

    -- gifting
    SUM(COALESCE(tip_count,   0))                            AS total_tip_count,
    SUM(COALESCE(box_count,   0))                            AS total_box_count,
    SUM(COALESCE(wheel_count, 0))                            AS total_wheel_count,

    -- follow bet (separate column — kept for targeting in Analysis 1 & 2)
    SUM(COALESCE(follow_bet_count, 0))                       AS total_follow_bet_count,

    -- total_bet_count: ALL bet types including follow_bet per design.
    -- Assumes source bet_count column excludes follow_bet;
    -- both are summed here so the field represents any-bet activity.
    -- Verify: if source bet_count already includes follow_bet, remove the
    -- follow_bet_count term below to avoid double-counting.
    SUM(COALESCE(bet_count, 0) + COALESCE(follow_bet_count, 0))
                                                             AS total_bet_count,

    SUM(COALESCE(bdw_bet_count, 0))                          AS total_bdw_bet_count,
    SUM(COALESCE(member_to, 0))                              AS total_member_to,

    -- watch
    SUM(COALESCE(watch_sec, 0))                              AS total_watch_sec,
    MAX(COALESCE(if_watch, 0))                               AS if_watch,

    -- chat
    SUM(COALESCE(message_count,      0))                     AS total_messages,
    SUM(COALESCE(chat_session_count, 0))                     AS chat_sessions,
    SUM(COALESCE(bullet_sec,         0))                     AS total_bullet_sec,
    SUM(COALESCE(chatroom_sec,       0))                     AS total_chatroom_sec,

    -- diversity (raw count used in ML; breadth_score composite dropped)
    COUNT(DISTINCT streamer)                                 AS distinct_streamers,

    -- stream type / device — mode per month
    APPROX_TOP_COUNT(stream_type, 1)[OFFSET(0)].value        AS primary_stream_type,
    APPROX_TOP_COUNT(device_type, 1)[OFFSET(0)].value        AS primary_device

  FROM `nf-bifrost.livestream_dm.core_streaming_performance`
  WHERE is_lic       = 1
    AND is_shared    IS TRUE
    AND is_cancelled IS FALSE
    AND site_id      != 99                          -- chatroom only
    AND streamer     NOT IN ('Popo', 'GOKU', 'ID_0')
    AND streamer     != 'ID_N/A'
    AND streamer     NOT LIKE 'ID_%'
    AND DATE_TRUNC(session_date, MONTH) IN (SELECT month_start FROM date_window)
  GROUP BY 1, 2
),

-- ── Bet timing → day/night + weekday/weekend segments ────────────────────────
-- Uses fact_live_bet for finer-grained timestamp; site_id != 99 mirrors main filter.
bet_timing AS (
  SELECT
    cust_id,
    DATE_TRUNC(DATE(bet_datetime), MONTH)                                          AS month,
    COUNT(*)                                                                        AS _bet_cnt,

    -- day = 06:00–18:00, night = 18:01–05:59
    COUNTIF(EXTRACT(HOUR FROM bet_datetime) BETWEEN 6 AND 18)                      AS day_bets,
    COUNTIF(EXTRACT(HOUR FROM bet_datetime) NOT BETWEEN 6 AND 18)                  AS night_bets,

    -- weekday = Mon–Fri (DAYOFWEEK 2–6), weekend = Sat–Sun (1, 7)
    SUM(CASE WHEN EXTRACT(DAYOFWEEK FROM DATE(bet_datetime)) BETWEEN 2 AND 6
             THEN COALESCE(turnover, 0) ELSE 0 END)                                AS weekday_to,
    SUM(CASE WHEN EXTRACT(DAYOFWEEK FROM DATE(bet_datetime)) IN (1, 7)
             THEN COALESCE(turnover, 0) ELSE 0 END)                                AS weekend_to,
    SUM(COALESCE(turnover, 0))                                                      AS total_to

  FROM `nf-bifrost.livestream_dm.fact_live_bet`
  WHERE site_id != 99
  GROUP BY 1, 2
),

-- ── Account age (first transfer date) ────────────────────────────────────────
account_age AS (
  SELECT DISTINCT
    CustId            AS cust_id,
    DATE(CreatedDate) AS created_date
  FROM `nf-bifrost.VN_CTS_Data.CTSCustomer`
),

-- ── Combine + derive segments ─────────────────────────────────────────────────
combined AS (
  SELECT
    s.cust_id,
    s.month,

    -- raw aggregates
    s.session_count,
    s.total_tip_count,
    s.total_box_count,
    s.total_wheel_count,
    s.total_tip_count + s.total_box_count + s.total_wheel_count AS total_gift_count,
    s.total_follow_bet_count,
    s.total_bet_count,          -- includes follow_bet per design
    s.total_bdw_bet_count,
    s.total_member_to,
    s.total_watch_sec,
    s.if_watch,
    s.total_messages,
    s.chat_sessions,
    s.total_bullet_sec,
    s.total_chatroom_sec,
    s.distinct_streamers,
    s.primary_stream_type,
    s.primary_device,

    -- avg watch per session
    SAFE_DIVIDE(s.total_watch_sec, s.session_count)             AS avg_watch_sec_per_session,

    -- watch bucket: 1=<15 min, 2=15–30 min, 3=30–45 min, 4=>45 min
    CASE
      WHEN SAFE_DIVIDE(s.total_watch_sec, s.session_count) <  900  THEN 1
      WHEN SAFE_DIVIDE(s.total_watch_sec, s.session_count) < 1800  THEN 2
      WHEN SAFE_DIVIDE(s.total_watch_sec, s.session_count) < 2700  THEN 3
      ELSE 4
    END                                                         AS watch_bucket,

    -- TFU flags
    CASE WHEN (s.total_tip_count + s.total_box_count + s.total_wheel_count) > 0
              AND s.total_follow_bet_count > 0
         THEN 1 ELSE 0 END                                      AS is_tfu,

    CASE WHEN (s.total_tip_count + s.total_box_count + s.total_wheel_count) > 0
              AND s.total_follow_bet_count = 0
         THEN 1 ELSE 0 END                                      AS is_donated,

    CASE WHEN (s.total_tip_count + s.total_box_count + s.total_wheel_count) = 0
              AND s.total_follow_bet_count > 0
         THEN 1 ELSE 0 END                                      AS is_follow_bet,

    -- tfu_gap: 0=TFU, 1=one dimension only (Donated or FollowBet), 2=Cold
    CASE
      WHEN (s.total_tip_count + s.total_box_count + s.total_wheel_count) > 0
           AND s.total_follow_bet_count > 0  THEN 0
      WHEN (s.total_tip_count + s.total_box_count + s.total_wheel_count) > 0
           OR  s.total_follow_bet_count > 0  THEN 1
      ELSE 2
    END                                                         AS tfu_gap,

    -- account age tier — measured at end of data month
    CASE
      WHEN DATE_DIFF(DATE_ADD(s.month, INTERVAL 1 MONTH), a.created_date, MONTH) <  1  THEN 'Newborn'
      WHEN DATE_DIFF(DATE_ADD(s.month, INTERVAL 1 MONTH), a.created_date, MONTH) <  3  THEN 'Rising'
      WHEN DATE_DIFF(DATE_ADD(s.month, INTERVAL 1 MONTH), a.created_date, MONTH) <  6  THEN 'Established'
      WHEN DATE_DIFF(DATE_ADD(s.month, INTERVAL 1 MONTH), a.created_date, MONTH) < 12  THEN 'Veteran'
      WHEN DATE_DIFF(DATE_ADD(s.month, INTERVAL 1 MONTH), a.created_date, MONTH) < 36  THEN 'Pioneer'
      ELSE 'Legend'
    END                                                         AS account_age_tier,

    -- day/night segment (NULL when user had no bets that month)
    CASE
      WHEN t._bet_cnt IS NULL OR t._bet_cnt = 0                     THEN NULL
      WHEN SAFE_DIVIDE(t.day_bets,   t._bet_cnt) >= 0.75            THEN 'Day'
      WHEN SAFE_DIVIDE(t.night_bets, t._bet_cnt) >= 0.75            THEN 'Night'
      ELSE 'Mixed_Time'
    END                                                         AS time_segment,

    -- weekday/weekend segment
    CASE
      WHEN t.total_to IS NULL OR t.total_to = 0                     THEN NULL
      WHEN SAFE_DIVIDE(t.weekday_to, t.total_to) >= 0.75            THEN 'Weekday'
      WHEN SAFE_DIVIDE(t.weekend_to, t.total_to) >= 0.75            THEN 'Weekend'
      ELSE 'Mixed_Day'
    END                                                         AS day_segment

  FROM      base_sessions s
  LEFT JOIN account_age   a ON s.cust_id = a.cust_id
  LEFT JOIN bet_timing    t ON s.cust_id = t.cust_id AND s.month = t.month
)

SELECT
  cust_id,
  month,
  session_count,
  total_tip_count,
  total_box_count,
  total_wheel_count,
  total_gift_count,
  total_follow_bet_count,
  total_bet_count,           -- includes follow_bet per design
  total_bdw_bet_count,
  total_member_to,
  total_watch_sec,
  avg_watch_sec_per_session,
  watch_bucket,
  if_watch,
  total_messages,
  chat_sessions,
  total_bullet_sec,
  total_chatroom_sec,
  distinct_streamers,
  primary_stream_type,
  primary_device,
  account_age_tier,
  time_segment,
  day_segment,
  is_tfu,
  is_donated,
  is_follow_bet,
  tfu_gap
FROM combined
ORDER BY cust_id, month
;
