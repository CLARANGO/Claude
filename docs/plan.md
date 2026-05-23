# Streamer Performance Dashboard — Final Plan

## Context
Clara (data analysis) needs a dashboard tracking streamer performance during the **World Cup (June–July 2026)**. Each streamer covers live football matches; viewers can follow streamer picks, place own bets, donate, and tip. Three views are required:

1. **Per-live-session performance** (one row per match a streamer covers)
2. **Weekly + monthly streamer performance** (Mon–Sun ISO weeks; calendar months)
3. **Our product vs Platform comparison** (platform only exposes bet turnover + count)

Source: existing BigQuery tables (schemas resolved — see "BQ Schema Map" below).
Output: BQ materialized agg tables → Google Sheets → Looker Studio.
Refresh: daily batch, 13:00 Taipei (UTC+8).
Audience: ops team + management (view-only).
Alerts: Slack webhook + in-sheet conditional formatting.

---

## Metrics Tree (locked) — [ALERT] = monitored daily

```
NORTH STAR
├── Follow Streamer Bet Count     ← bets in the Follow-Streamer category     [ALERT]
├── Bet During Watch — Turnover   ← RM                                       [ALERT]
└── Donation Amount (incl. Tips)  ← USD,Tips roll up into Donation total     [ALERT]


L1 — DRIVERS
├── Recommend Bet Count           ← streamer's recommend bet
├── Follow Streamer Bet Turnover  ← RM on follow-streamer bets               [ALERT]
├── Follow User Count             ← unique users placing follow-streamer bets
├── Donation User Count                                                      [ALERT]
├── Donation Count
├── Tip Amount                    ← USD, subset of Donation (broken out)       
├── Tip Count                     
├── Wheel Amount                  ← USD, subset of Donation (broken out)         
├── Wheel Count                                                               
├── Box Amount                    ← USD, subset of Donation (broken out)         
├── Box Count                                                                
└── Stream Count                  ← # live sessions in period, streamer+stream id = 1 count

L2 — DECOMPOSITION
├── Bet Turnover by category (same 4)
├── Bet During Watch — Count                                                 [ALERT]
└── Watch Time (total + avg per viewer + PCU)
```

**The 6 [ALERT] KPIs are the alertable surface:**
1. Follow Streamer Bet Count (NS)
2. Follow Streamer Bet Turnover (L1)
3. Tip Amount (NS)
4. Tip Count (L1)
5. Bet During Watch Count (L2)
6. Bet During Watch Turnover (NS)

Each is evaluated daily per stream session against rolling-5-median baseline + min-volume gate. Daily Slack digest summarizes overall performance so the dashboard doesn't need to be opened every morning.

### Locked definitions
- **NS "Follow Streamer Bet Count"** = Follow Streamer category only (strictest definition of stream influence). Same metric appears at L2 as decomposition.
- **Recommend Bet Count** = streamer-recommended bets — viewer placed a bet on the streamer's recommended pick during the live session. Not system recommendations.
- **Donation/Tip** = Tip is a subset of Donation. NS Donation Amount = full total; Tip metrics broken out at L1.
- **Bet During Watch** = bet placed by a user whose watch session overlaps the bet timestamp (same stream).
- **Like-for-like guards:** exclude voided bets; exclude flagged bot/multi-accounts on our side.

---

## World Cup Context & Match Tagging

June 12–July 20 is worldcup tournament — comparability is easier, but variance comes from team popularity and stage. 

Original dim table : nf-bifrost.LiveStreaming.match_info 
Schemas: Month \	kickoffday\	timehour \	KickOffTime	\ Item \	League	\Team	\country	\Streamer	\SabaMatchId	\AnchorId	\Shared	\InfoSiteMatchId	\CloseTime	\LeagueId	\LeagueGroup	\LeagueCnName	\HomeCnName	\AwayCnName\	Supplier	\IsSelfOwned	\isCancelled

->Tag matches in `dim_match`:

- `match_stage` — group(6/11-6/28) / R32(6/29-7/4) / R16(7/5-7/8) / QF(7/1-7/12) / SF(7/15-7/16) / Third place play-off (7/19) / final(7/20)
- `time_slot_taipei` — late-night / morning / afternoon (derived from kickoff)
- `day_of_week`

---

## Comparison Views

### 1. Match-by-match (per session)
- Each row = one live session (stream_id, streamer_id, match_id)
- Compare current vs **rolling median of the last 3 matches** when match_stage = group
  + Compare current vs **rolling median of the last 3 matches** when match_stage = R32, R16
  + Compare current vs **rolling median of the last match ** when match_stage = QF, SF, Third place play-off, final
  + final compare to avg AVG all matches in the season
- Display: current, rolling median, delta %, sparkline
- **Sample-size guard:** <3 prior matches → show "n/a"

### 2. Week (ISO Mon–Sun)
- Current week vs rolling acummulated week avg ( week 2 vs week 1, week 3 vs avg week1+2, ...)
- WoW % change
- Top 5 streamers + top 5 matches by NS

### 3. Month
- June vs July
- Top streamer + top match of the whole season

### 4. Our Product vs Platform (two scopes)

**Scope A — Match-level (per match our streamers covered):**
- Our bet turnover / Platform bet turnover on that match = **match share**
- Our bet count / Platform bet count = **match share of bets**
- Our avg bet size vs platform avg bet size
- Rolls up by streamer / week / month

**Scope B — Season-window aggregate (per session window):**
- Our bet turnover during the season / Platform total bet turnover during season 
- Tests whether our streams move overall platform activity, not just the covered match

Definitions:
- Our product bet = Bet During Watch (watch + bet timestamps overlap)
- Platform bet = all bets on platform (Scope A: same match, with sites having streamer function; Scope B: whole season)
- Both sides: exclude voided

---

## BQ Schema Map (resolved from claude.ai skill)

**Project + region:** `nf-bifrost` (chatroom/livestream data) + `nf-muses` (TFU features). **All datasets live in `asia-southeast1` (Singapore).** **Reporting destination is `nf-muses.reporting.*`** — agg tables live in `nf-muses`, not `nf-bifrost`. Source reads from `nf-bifrost.*` are cross-project but same-region (allowed). Scheduled queries must run with `--location=asia-southeast1`.

### Primary fact tables

| Table | Grain | Used for |
|---|---|---|
| `nf-bifrost.livestream_dm.core_streaming_performance` | cust_id × stream_id (already aggregated per customer-session) | **All session-level NS/L1/L2 metrics.** This is the workhorse — most of the agg work is already done. |
| `nf-bifrost.livestream_dm.fact_live_bet` | trans_id (per bet) | Platform-wide bet totals (Scope A & B comparison); the `follow_type` column gives the 4 bet categories at row level. |
| `nf-bifrost.livestream_dm.fact_tip_record` | record_id (per tip) | Tip drill-down if needed; not required since core_streaming_performance has tip totals. |
| `nf-bifrost.LiveStreaming.chatroom_recommend` | streamer × match × pick | **L1 "Recommend Bet Count"** — `RecommendCount` is bets on the streamer's recommended pick. |

### Dimension tables

| Table | Use |
|---|---|
| `nf-bifrost.LiveStreaming.match_info` | dim_match — has `SabaMatchId`, `KickOffTime`, `League`, `LeagueGroup`, `HomeCnName`, `AwayCnName`, `isCancelled`. **No stage column** — needs manual tagging for group/R16/QF/SF/final. |
| `nf-bifrost.LiveStreaming.chatroom_anchor` | dim_streamer — `Id` (= anchor_id), `Name`, `Provider`, `Language`, `Status`. |
| `nf-bifrost.VN_CTS_Data.CTSCustomer` | User attrs if needed. Join key is `CustID` (capital). `CreatedDate` is UTC-4 → convert with `DATETIME(TIMESTAMP(CreatedDate,'UTC-4'),'Asia/Taipei')`. Dedup with `QUALIFY ROW_NUMBER() OVER (PARTITION BY CustID ORDER BY ModifiedTime DESC) = 1`. |

### Column-to-metric mapping (in core_streaming_performance)

```
NS Follow Streamer Bet Count        = SUM(follow_bet_count)
NS Donation Amount (incl. Tips)     = SUM(tip_amount_rm) + SUM(box_amount_rm) + SUM(wheel_amount_rm)   ← needs confirmation
L1 Recommend Bet Count              = SUM(chatroom_recommend.RecommendCount)  on SabaMatchId × AnchorId
L1 Follow Streamer Bet Turnover     = SUM(follow_member_to)
L1 Follow User Count                = COUNT(DISTINCT cust_id) WHERE follow_bet_count > 0
L1 Donation User Count              = COUNT(DISTINCT cust_id) WHERE if_tip = 1 (or if_tip|if_box|if_wheel)
L1 Tip Amount                       = SUM(tip_amount_rm)
L1 Tip Count                        = SUM(tip_count)
L1 Tip User Count                   = COUNT(DISTINCT cust_id) WHERE if_tip = 1
L1 Stream Count                     = COUNT(DISTINCT stream_id) at streamer × period grain
L2 Follow Streamer (bet)            = SUM(follow_bet_count), SUM(follow_member_to)
L2 Follow User/Player (bet)         = SUM(follow_player_bet_count), SUM(follow_player_member_to)
L2 Follow System (bet)              = NOT IN core_streaming_performance — derive from fact_live_bet.follow_type
L2 Self (bet)                       = total bet_count − follow_bet_count − follow_player_bet_count − follow_system_bet_count
L2 Bet During Watch — Count         = SUM(during_watch_bet_count)
L2 Bet During Watch — Turnover      = SUM(during_watch_member_to)
L2 Watch Time total                 = SUM(watch_sec)
L2 Watch Time per viewer            = SUM(watch_sec) / COUNT(DISTINCT cust_id WHERE if_watch = 1)
Viewers                             = COUNT(DISTINCT cust_id WHERE if_watch = 1)
```

### Filters
- `is_cancelled = FALSE` (exclude cancelled streams)
- World Cup filter: `match_info.League` or `LeagueGroup` matching "FIFA World Cup" (exact value TBD — needs distinct-value probe)
- For "Bet During Watch" we don't need to recompute the overlap — the `during_watch_*` columns and `is_during_watch` flag are pre-computed

### Open data questions (small, can be resolved in one probe each)
1. **Donation composition** — does "Donation" = tip only, or tip + box + wheel? Default: include all three.
2. **Follow System category** — is it `fact_live_bet.follow_type = 'system'` or similar? Distinct-value probe needed.
3. **World Cup filter value** — exact string in `match_info.League` / `LeagueGroup` for World Cup 2026.
4. **Match stage** — needs manual mapping or derive from match date + bracket structure.
5. **`is_lic` column** — meaning? (suspect "logged-in customer"). Filter or ignore?

---

## Skill File to Create (Phase 0 deliverable)

Create `.claude/skills/bq-schemas/SKILL.md` in the repo so future Claude Code sessions auto-load this schema map.

**Frontmatter:**
```yaml
---
name: bq-schemas
description: BigQuery table schemas for the livestream/streamer dashboard project. Use when querying nf-bifrost.livestream_dm, nf-bifrost.LiveStreaming, nf-muses.muses, or building dashboards/metrics on streaming, tips, bets, lucky boxes, lucky wheel, chatroom recommendations, or TFU features.
---
```

**Body content:** the full text Clara pasted, organized by table, plus:
- A "Common joins" section showing typical JOIN paths
- A "Common filters" section (cancelled streams, World Cup, etc.)
- The column-to-metric mapping above

Path: `/home/user/Claude/.claude/skills/bq-schemas/SKILL.md`.

---

## SQL Files to Rewrite

Replace `__RAW_*__` placeholders in all `sql/agg_*.sql` files with the real table names. Major shape change: **`core_streaming_performance` already does the per-cust × per-stream aggregation**, so:

- `agg_session_metrics.sql` collapses to: `GROUP BY stream_id` on core_streaming_performance + JOIN chatroom_recommend + JOIN match_info — no need to recompute Bet During Watch or 4-category bets from raw bets (mostly).
- `agg_match_platform_compare.sql` still needs `fact_live_bet` for Scope A (platform totals per match) and Scope B (platform totals per session time window).
- `dim_match.sql` rebuilt against `match_info`, with manual `match_stage` mapping (TODO: lookup table for the 64 World Cup fixtures).
- `dim_streamer.sql` rebuilt against `chatroom_anchor`, with `tier` derived from streamer history in `core_streaming_performance`.

---

## Phase 0 — BQ Discovery (now: small targeted probes only)

Schemas are known; only 4 distinct-value probes needed:

```sql
-- 1. Confirm Donation composition columns present + ranges
SELECT
  COUNTIF(if_tip=1) AS sessions_with_tip,
  COUNTIF(if_box=1) AS sessions_with_box,
  COUNTIF(wheel_count>0) AS sessions_with_wheel,
  SUM(tip_amount_rm) AS total_tip_rm,
  SUM(box_amount_rm) AS total_box_rm,
  SUM(wheel_amount_rm) AS total_wheel_rm
FROM `nf-bifrost.livestream_dm.core_streaming_performance`
WHERE stream_start_date BETWEEN '2026-05-01' AND '2026-05-22';

-- 2. fact_live_bet.follow_type distinct values (to identify "Follow System")
SELECT follow_type, COUNT(*) n FROM `nf-bifrost.livestream_dm.fact_live_bet`
WHERE trans_dt >= '2026-05-01'
GROUP BY follow_type ORDER BY n DESC;

-- 3. match_info — find the World Cup string
SELECT DISTINCT League, LeagueGroup, LeagueCnName, COUNT(*) n
FROM `nf-bifrost.LiveStreaming.match_info`
WHERE KickOffTime >= '2026-06-01'
GROUP BY 1,2,3 ORDER BY n DESC LIMIT 50;

-- 4. is_lic meaning — sample
SELECT is_lic, COUNT(*) n
FROM `nf-bifrost.livestream_dm.core_streaming_performance`
WHERE stream_start_date >= '2026-05-01'
GROUP BY 1;
```

After these 4 probes, all `__RAW_*__` placeholders can be filled and SQL is final.

---

## Phase 1 — Dataset Design

Materialized BQ tables in a dedicated reporting dataset, written by scheduled query.

| Table | Grain | Contents |
|---|---|---|
| `agg_session_metrics` | one row per stream session | All NS + L1 + L2 for that session; joined to dim_match |
| `agg_streamer_weekly` | streamer × ISO week | Weekly totals, 4-week rolling median, WoW % |
| `agg_streamer_monthly` | streamer × month | Monthly totals, MoM % |
| `agg_match_platform_compare` | match_id | Our + platform metrics, share % for Scope A & B |
| `dim_match` | match_id | kickoff_ts, teams, stage, popularity_tier, time_slot, day_of_week |
| `dim_streamer` | streamer_id | name, tier (rookie/regular/top), join_date |

Each `agg_*` table includes `as_of_date` so reruns are idempotent (insert-overwrite by date partition).

---

## Phase 2 — Automation Flow

```
[BigQuery raw tables]
        ↓
[BQ Scheduled Query — daily 06:00 Taipei]
   • Rebuilds agg_session_metrics, agg_streamer_weekly,
     agg_streamer_monthly, agg_match_platform_compare
   • Insert-overwrite by as_of_date partition (idempotent backfill)
        ↓
[BQ → Google Sheets connector — auto-refresh]
   • One tab per agg table
        ↓
[Looker Studio dashboard — reads BQ direct (preferred) or Sheets]
   • Tab 1 Session  • Tab 2 Weekly/Monthly
   • Tab 3 Platform • Tab 4 Alert log
        ↓
[Apps Script on Sheet — runs post-refresh]
   • Evaluates threshold + anomaly rules
   • Posts to Slack webhook
   • Writes Alert log tab + applies conditional formatting
```

---

## Phase 3 — Dashboard Layout (Looker Studio)

**Tab 1 — Session view**
- Filters: date range, streamer, match stage, popularity tier
- Table: row per session; NS + key L1 columns; rolling-5 median delta column; sparkline
- Drill-down: full L2 breakdown card on row click

**Tab 2 — Streamer weekly + monthly**
- Toggle weekly/monthly
- This-period NS totals + period-over-period delta
- Top 10 streamer leaderboard (tier-filterable)
- Top match leaderboard
- 6-period trend line

**Tab 3 — Platform comparison**
- Match-share scoreboard (Scope A): our % per match
- Time-window-share stacked bar (Scope B): our vs platform per session window
- Filters: stage, popularity tier, streamer

**Tab 4 — Alert log**
- Columns: date, metric, streamer/match, type (threshold/anomaly), severity, value, expected, acknowledged
- Filters: severity, date, ack status

---

## Phase 4 — Alerts & Daily Slack Digest

Two complementary Slack surfaces so Clara never needs to open the dashboard:

### 4a. Daily digest (always sent, 08:00 Taipei)

```
📊 World Cup Dashboard — <date>
<N> streams · <N> streamers · <N> matches

KPI snapshot (yesterday vs rolling-5 median):
  Follow Streamer Bet Count       <total>   ▲/▼ <%>
  Follow Streamer Bet Turnover    <total>   ▲/▼ <%>
  Bet During Watch Count          <total>   ▲/▼ <%>
  Bet During Watch Turnover       <total>   ▲/▼ <%>
  Tip Amount (RM)                 <total>   ▲/▼ <%>
  Tip Count                       <total>   ▲/▼ <%>

🏆 Top 3 streamers (by Follow Streamer Bet Count)
⚽ Top 3 matches

⚠️ <N> alerts overnight — see thread
```

Implementation in `apps_script/alerts.gs` → `runDaily()`; scheduled at 08:00 Taipei (post-batch + 2h buffer).

### 4b. Threshold + anomaly alerts (triggered)

**Min-volume gate (always applied):** session must have ≥100 viewers AND ≥10 bets.

**Per-session thresholds** — drop vs streamer's rolling-5 median:

| KPI | Medium | High |
|---|---|---|
| Follow Streamer Bet Count | ≤ −30% | ≤ −50% |
| Follow Streamer Bet Turnover | ≤ −30% | ≤ −50% |
| Bet During Watch Count | ≤ −30% | ≤ −50% |
| Bet During Watch Turnover | ≤ −30% | ≤ −50% |
| Tip Amount | ≤ −40% | ≤ −60% |
| Tip Count | ≤ −40% | ≤ −60% |

**Weekly:** weekly stream count drops >20% vs rolling-4-week median.
**Anomaly:** any of the 6 KPIs outside rolling-5 median ± 2 × MAD → medium.
**Streamer absent:** streamer with ≥1 stream in last 3 days didn't stream yesterday → low.

**Severity routing:**
- High → Slack @-mention ping + in-sheet red
- Medium → Slack thread under digest + in-sheet yellow
- Low → digest only + in-sheet green
- All → Alert Log row

### 4c. Comparison-view definitions (used in Scope A/B share calculations)

**"Our product" — strict definition (recommended default):** Bet During Watch only (`during_watch_member_to`). Most direct measure of stream-driven activity. Alternative: any bet from a user who watched any stream that day (any-touch). Lock one before reporting.

**Platform side:** all non-voided bets from `fact_live_bet` (no stream filter). `status_id` filter TBD via Phase 0 probe.

**Currency:** report in **USD**. Chatroom MYR amounts converted via `/ 4.2` (per `bq-filter-rules` skill). Jioo is already USD when `currency_id = 998`.

**Scope A (per match)** — `our / platform` on the same `SabaMatchId`. Answers: "Did our streams capture more of this match?"

**Scope B (per stream window)** — `our_during_watch / platform_in_window`. Answers: "Did our streamers move overall platform betting during their broadcast?"

Both scopes roll up to streamer / week / month via `dim_streamer`.

---

## Easy-to-miss gotchas (worth checking before locking)

1. **Per-viewer normalization.** A drop in raw Tip Amount can be (a) fewer viewers or (b) same viewers tipping less — different fixes. Track raw + per-viewer side-by-side.
2. **Stream-length normalization.** 30-min vs 3-hour streams produce wildly different absolutes. Per-hour rates fix this.
3. **Bet status filter.** `fact_live_bet.status_id` includes pending/voided — confirm settled values via probe before using `member_to`.
4. **Currency mismatch.** `tip_amount_rm` (RM) vs `member_to` (site currency). Pick reporting currency once and convert.
5. **Mean reversion.** After a hot streak, "back to normal" looks like a drop. Pair relative threshold with an absolute floor.
6. **Tier-aware thresholds.** Rookies are noisier than top streamers. Consider looser thresholds (or skip anomaly alerts) for `dim_streamer.tier = 'rookie'`.
7. **Match-tier mixing.** Brazil match vs minor-team match in a rolling median is apples-to-oranges. Include `team_popularity_tier` in alert context so reviewers can dismiss false positives.
8. **Late-settling bets.** Bets settle after match ends. The 06:00 batch may miss recent settlements — schedule a 24h-rerun for the prior-prior day.
9. **Slack noise budget.** 50 streamers × 6 KPIs = many candidates. Cap via digest model + tier filter + dedup (one streamer × metric × day max).
10. **Streamer absence.** If a streamer skips a day, the rolling-5 doesn't shift — they look "fine" with zero data. The `absent` alert (≥1 stream in last 3d + 0 yesterday) catches this.
11. **Min-volume gate edge.** 99 viewers = suppressed. Start at 100 viewers + 10 bets; tune after week 1.
12. **World Cup format change.** Group stage = 4 matches/day; knockout = 1–2/day. WoW comparison across the boundary is misleading — flag the transition date in the digest.
13. **Digest delivery timing.** 08:00 Taipei = 06:00 batch + 2h buffer. If batches sometimes run late, gate the digest on a "data complete" sentinel cell.
14. **Holiday calendars.** Local holidays change viewing patterns. Add a `holidays` lookup or annotate the digest.

---

## Verification Plan

1. **Post-Phase-0:** review BQ schema map with Clara; confirm all NS/L1/L2 computable; close gaps before SQL.
2. **Post-Phase-1:** hand-compute NS + L1 for one sample session via raw SQL; must match `agg_session_metrics` exactly.
3. **Post-Phase-2:** verify daily 06:00 Taipei refresh runs 3 days straight without manual intervention; check partition idempotency by re-running a day.
4. **Post-Phase-3:** UAT with one ops user + one manager; confirm filters, drill-downs, <5s load.
5. **Post-Phase-4:** synthetic threshold breach (edit a metric cell); confirm Slack + in-sheet alert fire within 10 min; confirm min-volume gate suppresses correctly.

---

## Recommended Next Action (revised — schemas now known)

Three concrete deliverables, ordered:

1. **Commit `.claude/skills/bq-schemas/SKILL.md`** with the schema map Clara pasted, so future Claude Code sessions in this repo auto-load it. Include frontmatter `name: bq-schemas` + a description rich in trigger words (BigQuery, livestream, tips, bets, World Cup).
2. **Rewrite the agg SQL** to use real table/column names:
   - `agg_session_metrics.sql` — `GROUP BY stream_id` over `core_streaming_performance`, JOIN `chatroom_recommend` and `match_info`
   - `agg_match_platform_compare.sql` — keep two CTEs (Scope A vs platform per match using `fact_live_bet`; Scope B vs platform during stream window)
   - `dim_match.sql` — built from `match_info` filtered to World Cup; manual `match_stage` lookup for ~64 fixtures
   - `dim_streamer.sql` — built from `chatroom_anchor`; tier derived from history in `core_streaming_performance`
3. **Run the 4 discovery probes** (above) to confirm:
   - Donation composition (tip + box + wheel?)
   - `fact_live_bet.follow_type` distinct values
   - World Cup filter string in `match_info.League` / `LeagueGroup`
   - `is_lic` meaning

After step 3, lock the SQL and schedule the daily 06:00 Taipei refresh.
