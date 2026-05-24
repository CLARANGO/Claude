-- =====================================================================
-- TFU Prediction — Lifetime user feature table (one row per cust_id)
-- Grain: cust_id  |  Chatroom only (site_id != 99)
-- Window: last 6 completed months (same as tfu_user_monthly)
-- Active filter: lifetime bet_count >= 1 OR any gift (tip/box/wheel) > 0
--
-- Pattern A — volume metrics expressed as AVG per observed month
--   (denominator = ever_flags.months_observed). Bucket CASEs apply the
--   same per-month thresholds as build_tfu_user_monthly.sql.
-- Pattern B — ever-in-state for is_tfu / is_gifter / is_follow_bet:
--   uses a monthly intermediate so "ever-TFU" requires gift AND follow_bet
--   in the SAME month, not just lifetime presence of each.
-- Pattern C — account_age_tier is computed at the latest month_end.
-- Segments — time_segment / day_segment use the UNWEIGHTED AVG across
--   observed months of each month's share (each month counts equally).
--   Sources: bets + tips + boxes + wheels + chat (chat uses
--   stream_start_time as a timing proxy and contributes amount_rm = 0
--   so it informs time_segment but not day_segment).
-- league_segment — composite text label; raw signals exposed too.
-- =====================================================================
CREATE OR REPLACE TABLE `nf-muses.muses.tfu_user_lifetime` AS

WITH
-- 1. Date window: last 6 completed calendar months ----------------------
date_bounds AS (
  SELECT
    DATE_TRUNC(DATE_SUB(CURRENT_DATE('Asia/Taipei'), INTERVAL 6 MONTH), MONTH) AS start_month,
    DATE_TRUNC(DATE_SUB(CURRENT_DATE('Asia/Taipei'), INTERVAL 1 MONTH), MONTH) AS end_month,
    LAST_DAY(
      DATE_TRUNC(DATE_SUB(CURRENT_DATE('Asia/Taipei'), INTERVAL 1 MONTH), MONTH),
      MONTH
    )                                                                       AS window_end_date
),

-- 2. Session-level chatroom data (identical filters to monthly build) --
sessions AS (
  SELECT
    csp.cust_id,
    DATE_TRUNC(csp.stream_start_date, MONTH)               AS month_start,
    csp.stream_start_time,
    csp.stream_id,
    csp.anchor_id,
    csp.streamer,
    csp.site,
    csp.currency,
    csp.device,
    CASE WHEN csp.stream_type LIKE 'Sport%' THEN 'sports'
         ELSE 'entertainment' END                          AS stream_type_norm,
    csp.watch_sec,
    csp.chatroom_sec,
    csp.bullet_sec,
    csp.message_count,
    csp.tip_count,        csp.tip_amount_rm,
    csp.box_count,        csp.box_amount_rm,
    csp.wheel_count,      csp.wheel_amount_rm,
    csp.bet_count,        csp.member_to,
    csp.during_watch_bet_count,
    csp.follow_bet_count,
    csp.if_chat, csp.if_bullet, csp.if_tip, csp.if_bet
  FROM `nf-bifrost.livestream_dm.core_streaming_performance` csp
  CROSS JOIN date_bounds db
  WHERE csp.is_lic = 1
    AND csp.is_shared IS TRUE
    AND csp.is_cancelled IS FALSE
    AND csp.site_id != 99
    AND csp.streamer NOT IN ('Popo','GOKU','ID_0')
    AND csp.streamer != 'ID_N/A'
    AND csp.streamer NOT LIKE 'ID_%'
    AND csp.stream_start_date >= db.start_month
    AND csp.stream_start_date <  DATE_ADD(db.end_month, INTERVAL 1 MONTH)
),

-- 3a. Monthly aggregates — needed for ever-TFU (same-month coincidence) -
user_month_agg AS (
  SELECT
    cust_id,
    month_start,
    SUM(tip_count + box_count + wheel_count) AS gift_count_month,
    SUM(follow_bet_count)                    AS follow_bet_count_month
  FROM sessions
  GROUP BY cust_id, month_start
),

ever_flags AS (
  SELECT
    cust_id,
    MAX(CASE WHEN gift_count_month > 0 AND follow_bet_count_month > 0
             THEN 1 ELSE 0 END)                            AS is_tfu_ever,
    MAX(CASE WHEN gift_count_month > 0 THEN 1 ELSE 0 END)  AS is_gifter_ever,
    MAX(CASE WHEN follow_bet_count_month > 0
             THEN 1 ELSE 0 END)                            AS is_follow_bet_ever,
    COUNT(*)                                               AS months_observed
  FROM user_month_agg
  GROUP BY cust_id
),

-- 3b. Lifetime session metrics per cust_id (no month grouping) ---------
user_lifetime_session AS (
  SELECT
    cust_id,
    ANY_VALUE(site)                                        AS site,
    ANY_VALUE(currency)                                    AS currency,

    -- Loyalty
    COUNT(DISTINCT CASE WHEN anchor_id != 0
      THEN CONCAT(CAST(anchor_id AS STRING),'-',CAST(stream_id AS STRING))
    END)                                                   AS sessions_count,
    COUNT(DISTINCT streamer)                               AS distinct_streamers,

    -- Watch
    SUM(watch_sec)                                         AS total_watch_sec,
    SAFE_DIVIDE(
      SUM(watch_sec),
      COUNT(DISTINCT CASE WHEN anchor_id != 0
        THEN CONCAT(CAST(anchor_id AS STRING),'-',CAST(stream_id AS STRING))
      END)
    )                                                      AS avg_watch_sec_per_session,

    -- Chat
    SUM(message_count)                                     AS total_messages,
    SUM(CASE WHEN if_chat THEN 1 ELSE 0 END)               AS chat_sessions,
    SUM(bullet_sec)                                        AS total_bullet_sec,
    SUM(chatroom_sec)                                      AS total_chatroom_sec,

    -- Gifting (RM → USD / 4.2)
    SUM(tip_count)                                         AS total_tip_count,
    SUM(tip_amount_rm)   / 4.2                             AS total_tip_usd,
    SUM(box_count)                                         AS total_box_count,
    SUM(box_amount_rm)   / 4.2                             AS total_box_usd,
    SUM(wheel_count)                                       AS total_wheel_count,
    SUM(wheel_amount_rm) / 4.2                             AS total_wheel_usd,

    -- Betting
    SUM(bet_count)                                         AS total_bet_count,
    SUM(member_to)                                         AS total_member_to,
    SUM(during_watch_bet_count)                            AS total_bdw_bet_count,
    SUM(follow_bet_count)                                  AS total_follow_bet_count,

    -- Stream type session split (for preference re-derivation)
    SUM(CASE WHEN stream_type_norm='sports'        THEN 1 ELSE 0 END) AS sports_sessions,
    SUM(CASE WHEN stream_type_norm='entertainment' THEN 1 ELSE 0 END) AS ent_sessions,

    -- Device session split (for preference re-derivation)
    SUM(CASE WHEN LOWER(device) LIKE '%mobile%'
              OR LOWER(device) LIKE '%android%'
              OR LOWER(device) LIKE '%ios%'
              OR LOWER(device) LIKE '%iphone%'   THEN 1 ELSE 0 END) AS mobile_sessions,
    SUM(CASE WHEN LOWER(device) LIKE '%desktop%'
              OR LOWER(device) LIKE '%pc%'
              OR LOWER(device) LIKE '%web%'      THEN 1 ELSE 0 END) AS desktop_sessions
  FROM sessions
  GROUP BY cust_id
),

-- 4a. Activity timing — UNION ALL of bets + tips + boxes + wheels + chat
activity_timing_lifetime AS (
  SELECT cust_id,
    DATE_TRUNC(DATE(trans_dt), MONTH) AS month_start,
    EXTRACT(HOUR      FROM trans_dt)  AS act_hour,
    EXTRACT(DAYOFWEEK FROM trans_dt)  AS act_dow,
    member_to                         AS amount_rm
  FROM `nf-bifrost.livestream_dm.fact_live_bet`
  CROSS JOIN date_bounds db
  WHERE site_id != 99
    AND DATE(trans_dt) >= db.start_month
    AND DATE(trans_dt) <  DATE_ADD(db.end_month, INTERVAL 1 MONTH)

  UNION ALL

  SELECT cust_id,
    DATE_TRUNC(DATE(tip_dt), MONTH),
    EXTRACT(HOUR      FROM tip_dt),
    EXTRACT(DAYOFWEEK FROM tip_dt),
    tip_amount_original * exchange_rate
  FROM `nf-bifrost.livestream_dm.fact_tip_record`
  CROSS JOIN date_bounds db
  WHERE site_id != 99
    AND DATE(tip_dt) >= db.start_month
    AND DATE(tip_dt) <  DATE_ADD(db.end_month, INTERVAL 1 MONTH)

  UNION ALL

  SELECT cust_id,
    DATE_TRUNC(DATE(record_dt), MONTH),
    EXTRACT(HOUR      FROM record_dt),
    EXTRACT(DAYOFWEEK FROM record_dt),
    box_amount_rm
  FROM `nf-bifrost.livestream_dm.mart_lucky_box`
  CROSS JOIN date_bounds db
  WHERE site_id != 99
    AND DATE(record_dt) >= db.start_month
    AND DATE(record_dt) <  DATE_ADD(db.end_month, INTERVAL 1 MONTH)

  UNION ALL

  -- mart_lucky_wheel has no site_id; filter by site name instead
  SELECT cust_id,
    DATE_TRUNC(DATE(record_dt), MONTH),
    EXTRACT(HOUR      FROM record_dt),
    EXTRACT(DAYOFWEEK FROM record_dt),
    amount_rm
  FROM `nf-bifrost.livestream_dm.mart_lucky_wheel`
  CROSS JOIN date_bounds db
  WHERE site != 'JiooLive'
    AND DATE(record_dt) >= db.start_month
    AND DATE(record_dt) <  DATE_ADD(db.end_month, INTERVAL 1 MONTH)

  UNION ALL

  -- Chat sessions: no per-event log, use stream_start_time as the timing proxy.
  -- amount_rm = 0 so chat does NOT bias day_segment (amount-weighted) but DOES
  -- count toward time_segment (count-based).
  SELECT cust_id,
    month_start,
    EXTRACT(HOUR      FROM stream_start_time) AS act_hour,
    EXTRACT(DAYOFWEEK FROM stream_start_time) AS act_dow,
    0                                         AS amount_rm
  FROM sessions
  WHERE if_chat = TRUE
),

-- 4b. Per-month shares — each month's day/night count split and
-- weekday/weekend amount split, computed independently per (user, month).
activity_agg_monthly AS (
  SELECT
    cust_id,
    month_start,
    SAFE_DIVIDE(
      SUM(CASE WHEN act_hour BETWEEN 6 AND 17 THEN 1 ELSE 0 END),
      COUNT(*)
    )                                                                 AS day_share,
    SAFE_DIVIDE(
      SUM(CASE WHEN act_hour BETWEEN 6 AND 17 THEN 0 ELSE 1 END),
      COUNT(*)
    )                                                                 AS night_share,
    SAFE_DIVIDE(
      SUM(CASE WHEN act_dow BETWEEN 2 AND 6 THEN amount_rm ELSE 0 END),
      SUM(amount_rm)
    )                                                                 AS weekday_share,
    SAFE_DIVIDE(
      SUM(CASE WHEN act_dow IN (1,7)        THEN amount_rm ELSE 0 END),
      SUM(amount_rm)
    )                                                                 AS weekend_share
  FROM activity_timing_lifetime
  GROUP BY cust_id, month_start
),

-- 4c. Lifetime = unweighted average of per-month shares (each observed
-- month counts equally regardless of activity volume).
activity_agg_lifetime AS (
  SELECT
    cust_id,
    AVG(day_share)     AS avg_day_share,
    AVG(night_share)   AS avg_night_share,
    AVG(weekday_share) AS avg_weekday_share,
    AVG(weekend_share) AS avg_weekend_share
  FROM activity_agg_monthly
  GROUP BY cust_id
),

-- 5. User × streamer aggregation (lifetime, for top-streamer features) -
user_streamer_lifetime AS (
  SELECT
    cust_id,
    streamer,
    anchor_id,
    SUM(follow_bet_count)                                          AS streamer_follow_bet_count,
    SUM(tip_count + box_count + wheel_count)                       AS streamer_gift_count,
    SUM(tip_amount_rm + box_amount_rm + wheel_amount_rm) / 4.2     AS streamer_gift_usd
  FROM sessions
  GROUP BY cust_id, streamer, anchor_id
),

-- 6. Top streamer by lifetime follow-bet ------------------------------
top_follow_streamer AS (
  SELECT
    cust_id,
    streamer                       AS top_follow_streamer,
    anchor_id                      AS top_follow_anchor_id,
    streamer_follow_bet_count      AS top_follow_streamer_bet_count
  FROM user_streamer_lifetime
  WHERE streamer_follow_bet_count > 0
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY cust_id
    ORDER BY streamer_follow_bet_count DESC, streamer
  ) = 1
),

-- 7. Top streamer by lifetime gifting (USD) ----------------------------
top_gift_streamer AS (
  SELECT
    cust_id,
    streamer                       AS top_gift_streamer,
    anchor_id                      AS top_gift_anchor_id,
    streamer_gift_count            AS top_gift_streamer_count,
    streamer_gift_usd              AS top_gift_streamer_usd
  FROM user_streamer_lifetime
  WHERE streamer_gift_count > 0
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY cust_id
    ORDER BY streamer_gift_usd DESC, streamer
  ) = 1
),

-- 8. League turnover (lifetime, joined to match_info for league names) -
league_to_lifetime AS (
  SELECT
    flb.cust_id,
    COALESCE(mi.League, 'Unmapped Match')              AS league,
    SUM(flb.member_to)                                 AS league_to
  FROM `nf-bifrost.livestream_dm.fact_live_bet` flb
  LEFT JOIN (
    SELECT SabaMatchId, League
    FROM `nf-bifrost.LiveStreaming.match_info`
    WHERE League IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY SabaMatchId ORDER BY KickOffTime) = 1
  ) mi ON flb.match_id = mi.SabaMatchId
  CROSS JOIN date_bounds db
  WHERE flb.site_id != 99
    AND DATE(flb.trans_dt) >= db.start_month
    AND DATE(flb.trans_dt) <  DATE_ADD(db.end_month, INTERVAL 1 MONTH)
  GROUP BY flb.cust_id, league
),

-- 9. Lifetime % share per league + dominance flag ---------------------
league_pct_lifetime AS (
  SELECT
    cust_id,
    league,
    league_to,
    SUM(league_to) OVER (PARTITION BY cust_id)                       AS total_league_to,
    SAFE_DIVIDE(league_to, SUM(league_to) OVER (PARTITION BY cust_id)) AS pct
  FROM league_to_lifetime
),

-- 10. Per-user league segment summary --------------------------------
league_segment_lifetime AS (
  SELECT
    cust_id,
    COUNTIF(pct >= 0.25 AND league != 'Unmapped Match')                          AS dominant_count,
    MAX(CASE WHEN pct >= 0.25 AND league != 'Unmapped Match' THEN league END)    AS dominant_league
  FROM league_pct_lifetime
  GROUP BY cust_id
),

-- 11. Account creation date for age tier -------------------------------
customer_age AS (
  SELECT
    CustID AS cust_id,
    DATE(
      DATETIME(
        TIMESTAMP(CreatedDate, 'UTC-4'),
        'Asia/Taipei'
      )
    ) AS created_date
  FROM `nf-bifrost.VN_CTS_Data.CTSCustomer`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY CustID ORDER BY ModifiedTime DESC) = 1
)

-- =======================================================================
-- Final assembly: lifetime features + ever-in-state targets
-- =======================================================================
SELECT
  uls.cust_id,
  ef.months_observed,
  db.start_month                                           AS window_start,
  db.end_month                                             AS window_end,
  db.window_end_date,

  -- Account age (computed at the window's latest month_end)
  DATE_DIFF(db.window_end_date, ca.created_date, DAY)      AS account_age_days,
  CASE
    WHEN ca.created_date IS NULL                                            THEN 'Unknown'
    WHEN DATE_DIFF(db.window_end_date, ca.created_date, MONTH) < 1  THEN 'Newborn'
    WHEN DATE_DIFF(db.window_end_date, ca.created_date, MONTH) < 3  THEN 'Rising'
    WHEN DATE_DIFF(db.window_end_date, ca.created_date, MONTH) < 6  THEN 'Established'
    WHEN DATE_DIFF(db.window_end_date, ca.created_date, MONTH) < 12 THEN 'Veteran'
    WHEN DATE_DIFF(db.window_end_date, ca.created_date, MONTH) < 36 THEN 'Pioneer'
    ELSE 'Legend'
  END                                                      AS account_age_tier,

  -- Site / currency
  uls.site,
  uls.currency,

  -- Loyalty (avg per observed month)
  SAFE_DIVIDE(uls.sessions_count, ef.months_observed)      AS avg_sessions_count,
  uls.distinct_streamers,
  CASE
    WHEN SAFE_DIVIDE(uls.sessions_count, ef.months_observed) BETWEEN 1 AND 2  THEN '1-2'
    WHEN SAFE_DIVIDE(uls.sessions_count, ef.months_observed) BETWEEN 3 AND 9  THEN '3-9'
    ELSE '10+'
  END                                                      AS sessions_bucket,

  -- Watch (avg per observed month)
  SAFE_DIVIDE(uls.total_watch_sec, ef.months_observed)     AS avg_watch_sec,
  uls.avg_watch_sec_per_session,
  CASE
    WHEN uls.avg_watch_sec_per_session <  900 THEN '<15min'
    WHEN uls.avg_watch_sec_per_session < 1800 THEN '15-30min'
    WHEN uls.avg_watch_sec_per_session < 2700 THEN '30-45min'
    ELSE '>45min'
  END                                                      AS watch_bucket,

  -- Chat (avg per observed month)
  SAFE_DIVIDE(uls.total_messages,      ef.months_observed) AS avg_messages,
  SAFE_DIVIDE(uls.chat_sessions,       ef.months_observed) AS avg_chat_sessions,
  SAFE_DIVIDE(uls.total_bullet_sec,    ef.months_observed) AS avg_bullet_sec,
  SAFE_DIVIDE(uls.total_chatroom_sec,  ef.months_observed) AS avg_chatroom_sec,

  -- Gifting (avg per observed month)
  SAFE_DIVIDE(uls.total_tip_count,   ef.months_observed)   AS avg_tip_count,
  SAFE_DIVIDE(uls.total_tip_usd,     ef.months_observed)   AS avg_tip_usd,
  SAFE_DIVIDE(uls.total_box_count,   ef.months_observed)   AS avg_box_count,
  SAFE_DIVIDE(uls.total_box_usd,     ef.months_observed)   AS avg_box_usd,
  SAFE_DIVIDE(uls.total_wheel_count, ef.months_observed)   AS avg_wheel_count,
  SAFE_DIVIDE(uls.total_wheel_usd,   ef.months_observed)   AS avg_wheel_usd,

  -- Betting (avg per observed month)
  SAFE_DIVIDE(uls.total_bet_count,        ef.months_observed) AS avg_bet_count,
  SAFE_DIVIDE(uls.total_member_to,        ef.months_observed) AS avg_member_to,
  SAFE_DIVIDE(uls.total_bdw_bet_count,    ef.months_observed) AS avg_bdw_bet_count,
  SAFE_DIVIDE(uls.total_follow_bet_count, ef.months_observed) AS avg_follow_bet_count,

  -- Stream type preference (re-derived from lifetime session split)
  CASE
    WHEN uls.sports_sessions > uls.ent_sessions    THEN 'sports'
    WHEN uls.ent_sessions    > uls.sports_sessions THEN 'entertainment'
    ELSE 'mixed'
  END                                                      AS stream_type_pref,

  -- Device preference (re-derived from lifetime session split)
  CASE
    WHEN uls.mobile_sessions  > uls.desktop_sessions THEN 'mobile'
    WHEN uls.desktop_sessions > uls.mobile_sessions  THEN 'desktop'
    ELSE 'mixed'
  END                                                      AS device_pref,

  -- Time segment (avg across observed months of each month's day/night
  -- count share — includes chat via stream_start_time proxy)
  CASE
    WHEN aa.avg_day_share IS NULL          THEN 'no_activity'
    WHEN aa.avg_day_share   >= 0.75        THEN 'Day'
    WHEN aa.avg_night_share >= 0.75        THEN 'Night'
    ELSE 'Mixed'
  END                                                      AS time_segment,

  -- Day segment (avg across observed months of each month's weekday/weekend
  -- amount share — chat contributes amount_rm = 0 so it is excluded here)
  CASE
    WHEN aa.avg_weekday_share IS NULL      THEN 'no_activity'
    WHEN aa.avg_weekday_share >= 0.75      THEN 'Weekday'
    WHEN aa.avg_weekend_share >= 0.75      THEN 'Weekend'
    ELSE 'Mixed'
  END                                                      AS day_segment,

  -- League dominance (raw signal — leagues with >=25% share of lifetime turnover)
  ls.dominant_count,
  ls.dominant_league,

  -- League segment (composite text label, matches monthly table)
  CASE
    WHEN uls.total_bet_count = 0                                  THEN 'non-bettor'
    WHEN ls.dominant_count IS NULL OR ls.dominant_count = 0       THEN 'No dominant league'
    WHEN ls.dominant_count = 1  THEN CONCAT(ls.dominant_league, ' player')
    ELSE 'multi-league player'
  END                                                      AS league_segment,

  -- Breadth score 0–5: distinct activities user EVER did over the window
  ( CASE WHEN uls.total_tip_count   > 0 THEN 1 ELSE 0 END
  + CASE WHEN uls.total_messages    > 0 THEN 1 ELSE 0 END
  + CASE WHEN uls.total_box_count   > 0 THEN 1 ELSE 0 END
  + CASE WHEN uls.total_wheel_count > 0 THEN 1 ELSE 0 END
  + CASE WHEN uls.total_bet_count   > 0 THEN 1 ELSE 0 END )  AS breadth_score,

  -- Top streamer by lifetime follow-bet
  tfs.top_follow_streamer,
  tfs.top_follow_anchor_id,
  tfs.top_follow_streamer_bet_count,

  -- Top streamer by lifetime gifting (USD-weighted)
  tgs.top_gift_streamer,
  tgs.top_gift_anchor_id,
  tgs.top_gift_streamer_count,
  tgs.top_gift_streamer_usd,

  -- Same streamer flag (lifetime)
  CASE
    WHEN tfs.top_follow_anchor_id IS NOT NULL
     AND tgs.top_gift_anchor_id   IS NOT NULL
     AND tfs.top_follow_anchor_id = tgs.top_gift_anchor_id
    THEN 1 ELSE 0
  END                                                      AS top_streamer_is_same,

  -- TARGETS: ever-in-state flags (same-month coincidence for is_tfu)
  ef.is_tfu_ever                                           AS is_tfu,
  ef.is_gifter_ever                                        AS is_gifter,
  ef.is_follow_bet_ever                                    AS is_follow_bet

FROM user_lifetime_session uls
CROSS JOIN date_bounds db
LEFT JOIN ever_flags          ef  ON uls.cust_id = ef.cust_id
LEFT JOIN activity_agg_lifetime aa ON uls.cust_id = aa.cust_id
LEFT JOIN customer_age        ca  ON uls.cust_id = ca.cust_id
LEFT JOIN top_follow_streamer tfs ON uls.cust_id = tfs.cust_id
LEFT JOIN top_gift_streamer   tgs ON uls.cust_id = tgs.cust_id
LEFT JOIN league_segment_lifetime ls ON uls.cust_id = ls.cust_id

-- Active-user filter: lifetime activity (any bet OR any gift)
WHERE uls.total_bet_count >= 1
   OR (uls.total_tip_count + uls.total_box_count + uls.total_wheel_count) > 0
;
