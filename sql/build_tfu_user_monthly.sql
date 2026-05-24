-- =====================================================================
-- TFU Prediction — Build user × month feature table
-- Grain: cust_id × data_month  |  Chatroom only (site_id != 99)
-- Window: last 6 completed months
-- Active filter: bet_count >= 1 OR any gift (tip/box/wheel) > 0
-- =====================================================================
CREATE OR REPLACE TABLE `nf-muses.muses.tfu_user_monthly` AS

WITH
-- 1. Date window: last 6 completed calendar months ----------------------
date_bounds AS (
  SELECT
    DATE_TRUNC(DATE_SUB(CURRENT_DATE('Asia/Taipei'), INTERVAL 6 MONTH), MONTH) AS start_month,
    DATE_TRUNC(DATE_SUB(CURRENT_DATE('Asia/Taipei'), INTERVAL 1 MONTH), MONTH) AS end_month
),

-- 2. Session-level chatroom data with standard filters applied ---------
sessions AS (
  SELECT
    csp.cust_id,
    DATE_TRUNC(csp.stream_start_date, MONTH)               AS month_start,
    LAST_DAY(csp.stream_start_date, MONTH)                 AS month_end,
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

-- 3. Aggregate session metrics per cust_id × month ----------------------
user_month_session AS (
  SELECT
    cust_id,
    month_start,
    ANY_VALUE(month_end)                                   AS month_end,
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

    -- Stream type session split (for preference)
    SUM(CASE WHEN stream_type_norm='sports'        THEN 1 ELSE 0 END) AS sports_sessions,
    SUM(CASE WHEN stream_type_norm='entertainment' THEN 1 ELSE 0 END) AS ent_sessions,

    -- Device session split (for preference)
    SUM(CASE WHEN LOWER(device) LIKE '%mobile%'
              OR LOWER(device) LIKE '%android%'
              OR LOWER(device) LIKE '%ios%'
              OR LOWER(device) LIKE '%iphone%'   THEN 1 ELSE 0 END) AS mobile_sessions,
    SUM(CASE WHEN LOWER(device) LIKE '%desktop%'
              OR LOWER(device) LIKE '%pc%'
              OR LOWER(device) LIKE '%web%'      THEN 1 ELSE 0 END) AS desktop_sessions
  FROM sessions
  GROUP BY cust_id, month_start
),

-- 4a. Activity timing — UNION ALL across bets + tips + boxes + wheels --
activity_timing AS (
  SELECT cust_id,
    DATE_TRUNC(DATE(trans_dt), MONTH)                        AS month_start,
    EXTRACT(HOUR        FROM trans_dt)                       AS act_hour,
    EXTRACT(DAYOFWEEK   FROM trans_dt)                       AS act_dow,
    member_to                                                AS amount_rm
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
),

-- 4b. Per-month activity aggregation (day/night counts, weekday/weekend amounts) -
activity_agg AS (
  SELECT
    cust_id, month_start,
    SUM(CASE WHEN act_hour BETWEEN 6 AND 17 THEN 1 ELSE 0 END)        AS day_act_count,
    SUM(CASE WHEN act_hour BETWEEN 6 AND 17 THEN 0 ELSE 1 END)        AS night_act_count,
    COUNT(*)                                                          AS total_acts,
    SUM(CASE WHEN act_dow BETWEEN 2 AND 6 THEN amount_rm ELSE 0 END)  AS weekday_amount,
    SUM(CASE WHEN act_dow IN (1,7)        THEN amount_rm ELSE 0 END)  AS weekend_amount,
    SUM(amount_rm)                                                    AS total_amount
  FROM activity_timing
  GROUP BY cust_id, month_start
),

-- 5. User × streamer aggregation (for top-streamer features) -----------
user_streamer AS (
  SELECT
    cust_id,
    month_start,
    streamer,
    anchor_id,
    SUM(follow_bet_count)                                          AS streamer_follow_bet_count,
    SUM(tip_count + box_count + wheel_count)                       AS streamer_gift_count,
    SUM(tip_amount_rm + box_amount_rm + wheel_amount_rm) / 4.2     AS streamer_gift_usd
  FROM sessions
  GROUP BY cust_id, month_start, streamer, anchor_id
),

-- 6. Top streamer by follow-bet per user × month -----------------------
top_follow_streamer AS (
  SELECT
    cust_id,
    month_start,
    streamer                       AS top_follow_streamer,
    anchor_id                      AS top_follow_anchor_id,
    streamer_follow_bet_count      AS top_follow_streamer_bet_count
  FROM user_streamer
  WHERE streamer_follow_bet_count > 0
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY cust_id, month_start
    ORDER BY streamer_follow_bet_count DESC, streamer
  ) = 1
),

-- 7. Top streamer by gifting (USD) per user × month --------------------
top_gift_streamer AS (
  SELECT
    cust_id,
    month_start,
    streamer                       AS top_gift_streamer,
    anchor_id                      AS top_gift_anchor_id,
    streamer_gift_count            AS top_gift_streamer_count,
    streamer_gift_usd              AS top_gift_streamer_usd
  FROM user_streamer
  WHERE streamer_gift_count > 0
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY cust_id, month_start
    ORDER BY streamer_gift_usd DESC, streamer
  ) = 1
),

-- 8. Account creation date for age tier --------------------------------
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
),

-- 9. League turnover calculations --------------------------------------
league_to AS (
  SELECT
    flb.cust_id,
    DATE_TRUNC(DATE(flb.trans_dt), MONTH)              AS month_start,
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
  GROUP BY flb.cust_id, month_start, league
),

-- % share per league, flag dominance
league_pct AS (
  SELECT
    lt.cust_id,
    lt.month_start,
    lt.league,
    lt.league_to,
    SUM(lt.league_to) OVER (PARTITION BY lt.cust_id, lt.month_start)   AS total_to,
    SAFE_DIVIDE(
      lt.league_to,
      SUM(lt.league_to) OVER (PARTITION BY lt.cust_id, lt.month_start)
    )                                                                 AS pct
  FROM league_to AS lt
),

league_segment AS (
  SELECT
    cust_id,
    month_start,
    COUNTIF(pct >= 0.25 AND league != 'Unmapped Match')                          AS dominant_count,
    MAX(CASE WHEN pct >= 0.25 AND league != 'Unmapped Match' THEN league END)    AS dominant_league
  FROM league_pct
  GROUP BY cust_id, month_start
)

-- =======================================================================
-- Final assembly: features + is_tfu target + active-user filter
-- =======================================================================
SELECT
  ums.cust_id,
  ums.month_start                                          AS data_month,
  FORMAT_DATE('%Y-%m', ums.month_start)                    AS month_year,
  ums.month_end,

  -- Account age (computed at month-end)
  DATE_DIFF(ums.month_end, ca.created_date, DAY)           AS account_age_days,
  CASE
    WHEN ca.created_date IS NULL                                THEN 'Unknown'
    WHEN DATE_DIFF(ums.month_end, ca.created_date, MONTH) < 1   THEN 'Newborn'
    WHEN DATE_DIFF(ums.month_end, ca.created_date, MONTH) < 3   THEN 'Rising'
    WHEN DATE_DIFF(ums.month_end, ca.created_date, MONTH) < 6   THEN 'Established'
    WHEN DATE_DIFF(ums.month_end, ca.created_date, MONTH) < 12  THEN 'Veteran'
    WHEN DATE_DIFF(ums.month_end, ca.created_date, MONTH) < 36  THEN 'Pioneer'
    ELSE 'Legend'
  END                                                      AS account_age_tier,

  -- Site / currency
  ums.site,
  ums.currency,

  -- Loyalty
  ums.sessions_count,
  ums.distinct_streamers,
  CASE
    WHEN ums.sessions_count BETWEEN 1 AND 2  THEN '1-2'
    WHEN ums.sessions_count BETWEEN 3 AND 9  THEN '3-9'
    ELSE '10+'
  END                                                      AS sessions_bucket,

  -- Watch
  ums.total_watch_sec,
  ums.avg_watch_sec_per_session,
  CASE
    WHEN ums.avg_watch_sec_per_session <  900 THEN '<15min'
    WHEN ums.avg_watch_sec_per_session < 1800 THEN '15-30min'
    WHEN ums.avg_watch_sec_per_session < 2700 THEN '30-45min'
    ELSE '>45min'
  END                                                      AS watch_bucket,

  -- Chat
  ums.total_messages,
  ums.chat_sessions,
  ums.total_bullet_sec,
  ums.total_chatroom_sec,

  -- Gifting
  ums.total_tip_count,   ums.total_tip_usd,
  ums.total_box_count,   ums.total_box_usd,
  ums.total_wheel_count, ums.total_wheel_usd,

  -- Betting
  ums.total_bet_count,
  ums.total_member_to,
  ums.total_bdw_bet_count,
  ums.total_follow_bet_count,

  -- Stream type preference
  CASE
    WHEN ums.sports_sessions > ums.ent_sessions    THEN 'sports'
    WHEN ums.ent_sessions    > ums.sports_sessions THEN 'entertainment'
    ELSE 'mixed'
  END                                                      AS stream_type_pref,

  -- Device preference
  CASE
    WHEN ums.mobile_sessions  > ums.desktop_sessions THEN 'mobile'
    WHEN ums.desktop_sessions > ums.mobile_sessions  THEN 'desktop'
    ELSE 'mixed'
  END                                                      AS device_pref,

  -- Time segment (activity-count weighted across bets+tips+boxes+wheels)
  CASE
    WHEN COALESCE(aa.total_acts, 0) = 0                          THEN 'no_activity'
    WHEN SAFE_DIVIDE(aa.day_act_count,   aa.total_acts) >= 0.75  THEN 'Day'
    WHEN SAFE_DIVIDE(aa.night_act_count, aa.total_acts) >= 0.75  THEN 'Night'
    ELSE 'Mixed'
  END                                                      AS time_segment,

  -- Day segment (activity-amount weighted)
  CASE
    WHEN COALESCE(aa.total_amount, 0) = 0                            THEN 'no_activity'
    WHEN SAFE_DIVIDE(aa.weekday_amount, aa.total_amount) >= 0.75     THEN 'Weekday'
    WHEN SAFE_DIVIDE(aa.weekend_amount, aa.total_amount) >= 0.75     THEN 'Weekend'
    ELSE 'Mixed'
  END                                                      AS day_segment,

  -- Breadth score 0–5 across {tip, chat, box, wheel, bet}
  ( CASE WHEN ums.total_tip_count   > 0 THEN 1 ELSE 0 END
  + CASE WHEN ums.total_messages    > 0 THEN 1 ELSE 0 END
  + CASE WHEN ums.total_box_count   > 0 THEN 1 ELSE 0 END
  + CASE WHEN ums.total_wheel_count > 0 THEN 1 ELSE 0 END
  + CASE WHEN ums.total_bet_count   > 0 THEN 1 ELSE 0 END )  AS breadth_score,

  -- Top streamer by follow-bet
  tfs.top_follow_streamer,
  tfs.top_follow_anchor_id,
  tfs.top_follow_streamer_bet_count,

  -- Top streamer by gifting (USD-weighted)
  tgs.top_gift_streamer,
  tgs.top_gift_anchor_id,
  tgs.top_gift_streamer_count,
  tgs.top_gift_streamer_usd,

  -- Flag: top follow-bet and top gift streamer are the same person
  CASE
    WHEN tfs.top_follow_anchor_id IS NOT NULL
     AND tgs.top_gift_anchor_id   IS NOT NULL
     AND tfs.top_follow_anchor_id = tgs.top_gift_anchor_id
    THEN 1 ELSE 0
  END                                                      AS top_streamer_is_same,

  -- League segment (composite text label)
  CASE
    WHEN ums.total_bet_count = 0                              THEN 'non-bettor'
    WHEN lgs.dominant_count IS NULL OR lgs.dominant_count = 0 THEN 'No dominant league'
    WHEN lgs.dominant_count = 1  THEN CONCAT(lgs.dominant_league, ' player')
    ELSE 'multi-league player'
  END                                                      AS league_segment,

  -- TARGET: TFU flag
  CASE
    WHEN (ums.total_tip_count + ums.total_box_count + ums.total_wheel_count) > 0
     AND ums.total_follow_bet_count > 0
    THEN 1 ELSE 0
  END                                                      AS is_tfu

FROM user_month_session ums
LEFT JOIN activity_agg        aa  ON ums.cust_id = aa.cust_id
                                 AND ums.month_start = aa.month_start
LEFT JOIN customer_age        ca  ON ums.cust_id = ca.cust_id
LEFT JOIN top_follow_streamer tfs ON ums.cust_id = tfs.cust_id
                                 AND ums.month_start = tfs.month_start
LEFT JOIN top_gift_streamer   tgs ON ums.cust_id = tgs.cust_id
                                 AND ums.month_start = tgs.month_start
LEFT JOIN league_segment      lgs ON ums.cust_id = lgs.cust_id
                                 AND ums.month_start = lgs.month_start

-- Active-user filter: any bet OR any gift
WHERE ums.total_bet_count >= 1
   OR (ums.total_tip_count + ums.total_box_count + ums.total_wheel_count) > 0
;
