---
name: bq-schemas
description: BigQuery table schemas for the livestream/streamer dashboard project. Use when querying nf-bifrost.livestream_dm, nf-bifrost.LiveStreaming, nf-muses.muses, or building dashboards/metrics on streaming sessions, tips, live bets, lucky boxes, lucky wheel, chatroom recommendations, World Cup matches, anchor/streamer info, or TFU user features.
---

# BigQuery Schemas — Livestream Project

This skill is a reference for the BQ tables backing the streamer performance dashboard. When the user asks about streamers, tips, bets, donations, or the World Cup dashboard, consult this file before writing SQL.

**Projects + region:**
- `nf-bifrost` — livestream + chatroom + bet data — **region `asia-southeast1` (Singapore)**
- `nf-muses` — TFU feature tables — **region `asia-southeast1` (Singapore)**

**All BigQuery work — datasets, scheduled queries, materialized tables — must be in `asia-southeast1`.** Cross-region joins are not allowed.

**Reporting destination:** the dashboard's `agg_*` and `dim_*` tables are written to **`nf-muses.reporting.*`** (region `asia-southeast1`). Source reads from `nf-bifrost.*` are cross-project but same-region, which BigQuery allows.

**Default filters:**
- `is_cancelled = FALSE` on streams
- `status_id` for valid bets — confirm via probe before using
- World Cup filter: `match_info.League` or `LeagueGroup` matching FIFA World Cup string (exact value TBD)

---

## `nf-bifrost.livestream_dm.core_streaming_performance`

**Grain:** `cust_id × stream_id` — one row per customer × stream session. Already aggregated, so SUM over cust_id to get stream-level totals.

```
is_lic, stream_id, country, site_id, cust_id, system_id, currency_id, betfrom_id,
version, device, if_chatroom, if_watch, if_bullet, if_watch_over_5min, if_tip,
if_bet, if_chat, if_single, if_wheel, chatroom_sec, watch_sec, bullet_sec,
tip_count, gift_count, tip_amount_rm, message_count, wheel_count, wheel_amount_rm,
wheel_amount_diamond, bet_count, member_to, company_real_to, nodraw_bet_count,
nodraw_member_to, nodraw_company_real_to, member_winlost, company_real_income,
during_watch_bet_count, during_watch_member_to, during_watch_company_real_to,
during_watch_nodraw_bet_count, during_watch_nodraw_member_to,
during_watch_nodraw_company_real_to, during_watch_member_winlost,
during_watch_company_real_income, follow_bet_count, follow_member_to,
follow_company_real_to, follow_member_winlost, follow_company_real_income,
follow_player_bet_count, follow_player_member_to, follow_player_company_real_to,
follow_player_member_winlost, follow_player_company_real_income,
stream_type, league_or_tag, stream_name, stream_start_time, stream_start_date,
stream_end_time, anchor_id, streamer, is_shared, supplier, self_owned,
is_cancelled, is_chatroom_open, site, currency, if_box, box_count,
box_amount_rm, box_amount_diamond
```

### Column meanings
- `member_to` = bet turnover (member-side); `company_real_to` = company-side turnover
- `follow_bet_count` / `follow_member_to` = **Follow Streamer** bets (NS metric)
- `follow_player_*` = **Follow User/Player** bets
- `during_watch_*` = bet was placed while user actively watching
- `if_*` flags = boolean did-customer-do-X-in-this-session
- `is_lic` = "logged-in customer" (confirm)
- `tip_amount_rm` / `box_amount_rm` / `wheel_amount_rm` = currency in RM (RMB-equivalent? confirm)

---

## `nf-bifrost.livestream_dm.fact_tip_record`

Per-tip detail.

```
record_id, cust_id, currency_id, site_id, tip_dt, tip_type, gift_id, gift_type,
stream_type, stream_id, anchor_id, country, betfrom_id, gift_count,
tip_amount_original, exchange_rate, stream_start_time
```

---

## `nf-bifrost.livestream_dm.mart_lucky_box`

Per-draw lucky box record.

```
stream_type, league_or_tag, stream_name, stream_start_time, stream_end_time,
stream_start_date, box_name, box_start_time, box_end_time, box_amount_rm,
box_amount_diamond, gift_id, draw_chance, gift_type, gift_amount_rm,
gift_amount_diamond, streamer, supplier, country, trans_id, site, currency,
record_dt, cust_id, nickname, stream_id, anchor_id, site_id
```

---

## `nf-bifrost.livestream_dm.mart_lucky_wheel`

Per-spin lucky wheel record.

```
record_id, stream_start_date, stream_start_time, stream_id, stream_type,
stream_name, anchor_id, streamer, is_shared, country, supplier, site,
cust_id, currency, amount_rm, amount_diamond, content, is_complete,
record_dt, nickname
```

---

## `nf-bifrost.livestream_dm.fact_live_bet`

Per-bet ledger. **No `stream_id`** — attribute to a stream via `(anchor_id, match_id, trans_dt)`.

```
trans_id, trans_dt, match_id, cust_id, site_id, currency_id, username,
status_id, betfrom, member_to, company_real_to, member_winlost,
company_real_income, is_during_watch, anchor_id, bet_type, follow_type, odds_id
```

- `follow_type` distinguishes the 4 bet categories (Self / Follow User / Follow System / Follow Streamer) — values TBD via probe
- `is_during_watch` = bet placed inside a watch session
- Use this table for **platform-wide totals** (no stream filter required)

---

## `nf-bifrost.LiveStreaming.chatroom_recommend`

Streamer recommendation counts per match/team.

```
SabaMatchId, Country, Streamer, Item, League, Team, KickOffTime, timehour,
kickoffday, Month, RecommendCount, AnchorId, Shared, recommend_player,
recommend_count_byplayer
```

`RecommendCount` = L1 "Recommend Bet Count" — bets placed on the streamer's recommended pick.

---

## `nf-bifrost.LiveStreaming.match_info` (dim_match source)

```
Month, kickoffday, timehour, KickOffTime, Item, League, Team, country,
Streamer, SabaMatchId, AnchorId, Shared, InfoSiteMatchId, CloseTime,
LeagueId, LeagueGroup, LeagueCnName, HomeCnName, AwayCnName,
Supplier, IsSelfOwned, isCancelled
```

No `match_stage` column — must be added via manual mapping for World Cup fixtures (group / R16 / QF / SF / final).

---

## `nf-bifrost.LiveStreaming.chatroom_anchor` (dim_streamer source)

```
Id, Name, Provider, Language, Status
```

JOIN key: `chatroom_anchor.Id = core_streaming_performance.anchor_id = fact_tip_record.anchor_id`. No filter needed.

---

## `nf-bifrost.VN_CTS_Data.CTSCustomer`

```
CustID, CreatedDate, ModifiedTime, LastLoginTime, currency, CurrencyID, SiteID, Site
```

- Join key: `CustID` (capitalized — *not* `cust_id`)
- `CreatedDate` is **UTC-4** — convert before use:
  ```sql
  DATETIME(TIMESTAMP(CreatedDate, 'UTC-4'), 'Asia/Taipei')
  ```
- Deduplicate (multiple rows per customer):
  ```sql
  QUALIFY ROW_NUMBER() OVER (PARTITION BY CustID ORDER BY ModifiedTime DESC) = 1
  ```

---

## `nf-muses.muses.tfu_user_monthly` (TFU project, region `asia-southeast1`)

Built by `sql/build_tfu_user_monthly.sql` in the `tfu-prediction` repo. Grain: `cust_id × data_month`. Chatroom only (`site_id != 99`). Active-user filter: `total_bet_count >= 1 OR (total_tip_count + total_box_count + total_wheel_count) > 0`.

### Identifiers + time
```
cust_id, data_month, month_year, month_end
```

### Account
```
account_age_days, account_age_tier   -- Newborn / Rising / Established / Veteran / Pioneer / Legend
site, currency
```

### Loyalty
```
sessions_count, distinct_streamers, sessions_bucket   -- '1-2' / '3-9' / '10+'
```

### Watch
```
total_watch_sec, avg_watch_sec_per_session,
watch_bucket   -- '<15min' / '15-30min' / '30-45min' / '>45min'
```

### Chat
```
total_messages, chat_sessions, total_bullet_sec, total_chatroom_sec
```

### Gifting (USD = RM / 4.2)
```
total_tip_count,   total_tip_usd,
total_box_count,   total_box_usd,
total_wheel_count, total_wheel_usd
```

### Betting
```
total_bet_count, total_member_to, total_bdw_bet_count, total_follow_bet_count
```

### Preferences / segments
```
stream_type_pref   -- 'sports' / 'entertainment' / 'mixed'
device_pref        -- 'mobile' / 'desktop' / 'mixed'
time_segment       -- 'Day' / 'Night' / 'Mixed' / 'no_bets' (bet-count weighted, 06:00–17:59 = Day)
day_segment        -- 'Weekday' / 'Weekend' / 'Mixed' / 'no_bets' (turnover weighted)
breadth_score      -- 0-5: count of {tip, chat, box, wheel, bet} the user did
league_segment
```

### Streamer affinity (per cust_id × month)
```
top_follow_streamer, top_follow_anchor_id, top_follow_streamer_bet_count
top_gift_streamer,   top_gift_anchor_id,   top_gift_streamer_count, top_gift_streamer_usd
top_streamer_is_same   -- 1 if top-follow anchor == top-gift anchor
```

### Target
```
is_tfu   -- 1 if (any gift) AND (any follow-bet) in the same month
```

---

## Common joins

```sql
-- Stream session ↔ match metadata
core_streaming_performance csp
JOIN match_info mi
  ON csp.league_or_tag = mi.League   -- adjust if a SabaMatchId field exists on csp
 AND csp.stream_start_time BETWEEN TIMESTAMP_SUB(mi.KickOffTime, INTERVAL 4 HOUR)
                              AND TIMESTAMP_ADD(mi.KickOffTime, INTERVAL 4 HOUR)

-- Stream ↔ anchor
core_streaming_performance csp
JOIN chatroom_anchor a ON csp.anchor_id = a.Id

-- Recommend bets ↔ match
chatroom_recommend cr
JOIN match_info mi ON cr.SabaMatchId = mi.SabaMatchId

-- Live bet ↔ stream (no stream_id on bets — match via anchor + match + time)
fact_live_bet fb
JOIN core_streaming_performance csp
  ON fb.anchor_id = csp.anchor_id
 AND fb.match_id  = csp.league_or_tag  -- confirm join key
 AND fb.trans_dt BETWEEN csp.stream_start_time AND csp.stream_end_time
```

---

## Column → dashboard metric mapping

| Dashboard metric | Source |
|---|---|
| NS Follow Streamer Bet Count | `SUM(csp.follow_bet_count)` grouped by stream_id |
| NS Donation Amount (incl. Tips) | `SUM(tip_amount_rm + box_amount_rm + wheel_amount_rm)` — confirm composition |
| L1 Recommend Bet Count | `SUM(chatroom_recommend.RecommendCount)` joined on SabaMatchId × AnchorId |
| L1 Follow Streamer Bet Turnover | `SUM(csp.follow_member_to)` |
| L1 Follow User Count | `COUNT(DISTINCT cust_id) WHERE follow_bet_count > 0` |
| L1 Donation User Count | `COUNT(DISTINCT cust_id) WHERE if_tip = 1 OR if_box = 1 OR if_wheel = 1` |
| L1 Tip Amount / Count / User Count | `SUM(tip_amount_rm)` / `SUM(tip_count)` / `COUNT(DISTINCT cust_id) WHERE if_tip=1` |
| L1 Stream Count | `COUNT(DISTINCT stream_id)` per streamer × period |
| L2 Bet Count (Follow Streamer) | `SUM(follow_bet_count)` |
| L2 Bet Count (Follow Player/User) | `SUM(follow_player_bet_count)` |
| L2 Bet Count (Follow System) | from `fact_live_bet` where `follow_type = '<system value>'` |
| L2 Bet Count (Self) | total `bet_count` − follow_streamer − follow_player − follow_system |
| L2 Bet During Watch — Count / Turnover | `SUM(during_watch_bet_count)` / `SUM(during_watch_member_to)` |
| L2 Watch Time | `SUM(watch_sec)` |
| Viewers | `COUNT(DISTINCT cust_id) WHERE if_watch = 1` |

---

## Open questions (probe before locking SQL)

1. **Donation composition** — `Donation = tip + box + wheel?` or just tip? Default: include all three.
2. **`fact_live_bet.follow_type` values** — which string identifies the Follow System category.
3. **World Cup filter** — exact `League` / `LeagueGroup` string for FIFA World Cup 2026.
4. **`is_lic` meaning** — logged-in customer? filter or ignore?
5. **`csp ↔ match_info` join key** — is there a direct `SabaMatchId` on `core_streaming_performance`, or must we join via `(league_or_tag, stream_start_time near KickOffTime)`?
