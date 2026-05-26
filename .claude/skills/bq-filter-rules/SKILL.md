---
name: bq-filter-rules
description: SQL filter rules and column aliases for the livestream BigQuery project. Use whenever writing queries against nf-bifrost.livestream_dm or nf-bifrost.LiveStreaming — covers required WHERE clauses (is_lic, is_shared, bot/placeholder streamer exclusions, test-currency exclusions), site_id segmentation (chatroom vs Jioo), MYR→USD conversion, standard column aliases (bdw_bet_count, follow_turnover, etc.), and the stream_count distinct-key pattern.
---

# SQL Filter Rules — Livestream BQ Queries

> For table schemas and column lists, refer to the **`bq-schemas`** skill. This skill is the *filter* and *naming* layer that every query must respect.

---

## `core_streaming_performance` — always apply ALL of these

```sql
WHERE is_lic = 1
  AND is_shared IS TRUE                -- omit only if is_shared is a SELECT dimension
  AND is_cancelled IS FALSE
  AND streamer NOT IN ('Popo', 'GOKU', 'ID_0')
  AND streamer != 'ID_N/A'
  AND streamer NOT LIKE 'ID_%'         -- catches all bot/placeholder streamers
```

- **Chatroom queries:** also add `AND site_id != 99`
- **Jioo queries:** also add `AND site_id = 99`
- **`is_shared` exception:** if `is_shared` must appear as a SELECT column, drop the `IS TRUE` filter and include it as a dimension instead.
- **`stream_type`:** do NOT filter. Normalize for display only — see below.

---

## `mart_lucky_box` (standalone)

```sql
WHERE currency != 'UUS'
  AND currency_id != 20                -- both = test currency, always exclude
  AND stream_start_time <= CURRENT_DATETIME("UTC+8")
```

When joined from `core_streaming_performance`: filters propagate from the driving table; still apply `currency != 'UUS' AND currency_id != 20` on the box table itself.

---

## `fact_tip_record`

### Chatroom tips (site_id != 99)

```sql
WHERE site_id != 99
  AND tip_type = 'Normal'              -- exclude 'Voucher' and all other types
  AND anchor.Name NOT IN ('Popo', 'GOKU', 'ID_0')
  AND anchor.Name != 'ID_N/A'
  AND anchor.Name NOT LIKE 'ID_%'
```

- Always LEFT JOIN `nf-bifrost.LiveStreaming.chatroom_anchor` ON `tip.anchor_id = anchor.Id`
- Date column: prefer `DATE(stream_start_time)`. Legacy `COALESCE(DATE(stream_start_time), DATE(tip_dt))` still valid.
- Tip amount to USD: `tip_amount_original * exchange_rate / 4.2`

### Jioo tips (site_id = 99)

```sql
WHERE site_id = 99
  AND tip_type = 'Normal'
  AND currency_id = 998                -- diamond = USD for Jioo
  AND anchor.Name NOT IN ('Popo', 'GOKU', 'ID_0')
  AND anchor.Name != 'ID_N/A'
  AND anchor.Name NOT LIKE 'ID_%'
```

---

## `jioolive_statement` (top-up)

```sql
WHERE TIMESTAMP_TRUNC(_PARTITIONTIME, DAY) BETWEEN TIMESTAMP("start") AND TIMESTAMP("end")
  AND Type IN (3, 5)
```

- Always use `_PARTITIONTIME` for date range (partition filter required)
- `price` is already USD, no conversion needed

---

## Currency / conversion

| Channel  | Amount columns                                                                        | Unit | To USD        |
|----------|---------------------------------------------------------------------------------------|------|---------------|
| Chatroom | `tip_amount_original * exchange_rate`, `wheel_amount_rm`, `box_amount_rm`             | MYR  | `/ 4.2`       |
| Jioo     | `tip_amount_original` (currency_id=998), `wheel_amount_diamond`, `box_amount_diamond` | USD  | no conversion |

**Test currencies — always exclude:** `currency != 'UUS' AND currency_id != 20`

### Currency policy override — worldcup dashboard

For the World Cup streamer dashboard (`nf-muses.worldcup.*`):

- **Turnover stays RM** — `member_to`, `follow_member_to`, `during_watch_member_to`, `follow_player_member_to` are reported raw, no `/4.2`. Standard alias: `*_turnover_rm`.
- **Donation / tip / box / wheel amounts convert to USD** — divide by `4.2`. Alias: `*_amount_usd` or `*_usd`.
- Rationale: betting stakes are interpreted at site currency parity; donations are normalized for cross-site comparison.

---

## Column naming conventions

| Raw expression                                                | Standard alias            |
|---------------------------------------------------------------|---------------------------|
| `during_watch_bet_count`                                      | `bdw_bet_count`           |
| `during_watch_member_to`                                      | `bdw_turnover_rm`         |
| Any `during_watch_*`                                          | `bdw_*`                   |
| `follow_member_to`                                            | `follow_streamer_bet_turnover_rm` |
| `*_amount_rm / 4.2`                                           | `*_amount_usd` or `*_usd` |
| `country` (in core_streaming_performance context)             | `language`                |
| `stream_start_date`                                           | `day`                     |
| `FORMAT_DATE('%Y-%m', stream_start_date)`                     | `month_year`              |
| `COUNT(DISTINCT CASE WHEN watch_sec >= 600 THEN cust_id END)` | `watch_over_10min_user`   |

---

## `stream_type` normalization (display only — never filter)

```sql
CASE
  WHEN stream_type LIKE 'Sport%' THEN 'sports'
  ELSE 'entertainment'
END AS stream_type
```

---

## Watch thresholds

- Over 10 min: `total_watch_sec >= 600`
- Over 5 min: `total_watch_sec >= 300` (or use `if_watch_over_5min` flag when available)

---

## `stream_count` distinct-key pattern

```sql
COUNT(DISTINCT CASE WHEN anchor_id != 0
  THEN CONCAT(CAST(anchor_id AS STRING), '-', CAST(stream_id AS STRING))
END) AS stream_count
```

Never use bare `COUNT(DISTINCT stream_id)` — always pair with `anchor_id` and guard `!= 0`.

---

## `LIKE` in BigQuery

- BQ does **not** need underscore escaping
- Use `NOT LIKE 'ID_%'` — **NOT** `NOT LIKE 'ID\_%'`
