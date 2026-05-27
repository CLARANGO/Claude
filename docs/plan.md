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
└── Gift Amount (incl. Tips)  ← USD, Tips roll up into gift total    [ALERT]

L1 — DRIVERS
├── Recommend Bet Count           ← streamer's recommend bet
├── Follow User Count             ← unique users placing follow-streamer bets
├── Bet During Watch — Count                                                 [ALERT]
├── Bet During Watch — Bet size                                              
├── Tip user/Watch user           ← streamers' attraction                    [ALERT]                    
├── Folow bet user/BdW user       ← streamers' recommendation influence      [ALERT]                                  
├── Tip Amount                    ← USD, subset of gift (broken out)
├── Wheel Amount                  ← USD, subset of gift (broken out)
├── Box Amount                    ← USD, subset of gift (broken out)

L2 — DECOMPOSITION
├── Stream Count                  ← # live sessions in period, streamer+stream id = 1 count
├── Follow Streamer Bet Turnover  ← RM on follow-streamer bets               [ALERT]
├── Bet During Watch — User Count                                            [ALERT]
├── Gift  Count
      ├── Wheel Count
      ├── Box Count
      ├── Tip Count
├── Gift User Count                                                          [ALERT]
├── Viewer                                                                   [ALERT]
├── Viewer above 10min                                                       [ALERT]
└── Watch Time (total + avg per viewer + PCU)
```

**The 6 [ALERT] KPIs are the alertable surface:**
1. Follow Streamer Bet Count (NS)
2. Bet During Watch — Turnover (NS)
3. Gift Amount (NS)
4. Follow Streamer Bet Turnover (L2)
5. Gift User Count (L1)
6. Bet During Watch — Count (L1)
7. Viewer above 10min (L2)

Each is evaluated daily per stream session against rolling-5-median baseline + min-volume gate. Daily Slack digest summarizes overall performance so the dashboard doesn't need to be opened every morning.

### Locked definitions
- **NS "Follow Streamer Bet Count"** = Follow Streamer category only (strictest definition of stream influence). Same metric appears at L2 as decomposition.
- **Recommend Bet Count** = streamer-recommended bets — viewer placed a bet on the streamer's recommended pick during the live session. Not system recommendations.
- **gift/Tip** = Tip is a subset of gift. NS gift Amount = full total; Tip metrics broken out at L1.
- **Bet During Watch** = bet placed by a user whose watch session overlaps the bet timestamp (same stream).
- **Like-for-like guards:** exclude voided bets; exclude flagged bot/multi-accounts on our side.

---

## World Cup Context & Match Tagging

June 12 – July 20 is the World Cup tournament — comparability is easier, but variance comes from team popularity and stage.

Original dim table: `nf-bifrost.LiveStreaming.match_info` — columns: Month, kickoffday, timehour, KickOffTime, Item, League, Team, country, Streamer, SabaMatchId, AnchorId, Shared, InfoSiteMatchId, CloseTime, LeagueId, LeagueGroup, LeagueCnName, HomeCnName, AwayCnName, Supplier, IsSelfOwned, isCancelled.

Tag matches in `dim_match`:

- `match_stage` — group (6/11–6/28) / R32 (6/29–7/4) / R16 (7/5–7/8) / QF (7/10–7/12) / SF (7/15–7/16) / 3rd_place (7/19) / final (7/20)
- `time_slot_taipei` — late_night / morning / afternoon (derived from kickoff)
- `day_of_week`

---

## Comparison Views

### 1. Match-by-match (per session)
- Each row = one live session (stream_id, streamer_id, match_id)
- Baseline depends on `match_stage`:
  - `group`, `R32`, `R16` → rolling median of the last **3** matches
  - `QF`, `SF`, `3rd_place`, `final` → rolling median of the last **1** match
  - `final` additionally compared to the season **average** across all matches
- Display: current, baseline, delta %, sparkline
- **Sample-size guard:** `<3` prior matches → show "n/a" (only applies where baseline = 3)

### 2. Week (ISO Mon–Sun)
- Current week vs **cumulative-prior-weeks average** (week N vs avg of weeks 1…N-1)
- WoW % change
- Top 5 streamers + top 5 matches by NS

### 3. Month
- June vs July
- Top streamer + top match of the whole season

### 4. Our Product vs Platform (two scopes)

**Scope A — Match-level (per match our streamers covered):**
- Our bet turnover / Platform bet turnover on that match = **match share**
- Our bet count / Platform bet count  on that match = **match share of bets**
- Our avg bet size vs platform avg bet size
- Rolls up by streamer / week / month
- Platform side restricted to sites with streamer function

**Scope B — Season-window aggregate (full World Cup):**
- Our bet turnover across the full season / Platform total bet turnover across the full season = **season share**
- Tests whether our streams move overall platform activity, not just the covered matches

Definitions:
- Our product bet = Bet During Watch (watch + bet timestamps overlap)
- Platform bet = all bets on platform (Scope A: same match, sites with streamer function; Scope B: whole season)
- Both sides: exclude voided

---

## BQ Schema Map (resolved from claude.ai skill)

**Project + region:** `nf-bifrost` (chatroom/livestream data) + `nf-muses` (TFU features). **All datasets live in `asia-southeast1` (Singapore).** **Reporting destination is `nf-muses.worldcup.*`** — agg tables live in `nf-muses`, not `nf-bifrost`. Source reads from `nf-bifrost.*` are cross-project but same-region (allowed). Scheduled queries must run with `--location=asia-southeast1`.

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
| `nf-bifrost.LiveStreaming.match_info` | dim_match — has `SabaMatchId`, `KickOffTime`, `kickoffday`, `League`, `Team`, `Shared`, `isCancelled`, `country`, `Streamer`, `AnchorId`. **No stage column** — derived from kickoff date per the 7-stage map (group / R32 / R16 / QF / SF / 3rd_place / final). |
| `nf-bifrost.LiveStreaming.chatroom_anchor` | dim_streamer — `Id` (= anchor_id), `Name`, `Provider` (= Supplier), `Language`, `Status`. |
| `nf-bifrost.VN_CTS_Data.CTSCustomer` | User attrs if needed. Join key is `CustID` (capital). `CreatedDate` is UTC-4 → convert with `DATETIME(TIMESTAMP(CreatedDate,'UTC-4'),'Asia/Taipei')`. Dedup with `QUALIFY ROW_NUMBER() OVER (PARTITION BY CustID ORDER BY ModifiedTime DESC) = 1`. |

### Column-to-metric mapping (in core_streaming_performance)

```
NS Follow Streamer Bet Count        = SUM(follow_bet_count)
NS Bet During Watch — Turnover (RM) = SUM(during_watch_member_to)               -- raw RM, no /4.2
NS gift Amount (USD)            = (SUM(tip_amount_rm)+SUM(box_amount_rm)+SUM(wheel_amount_rm)) / 4.2
L1 Recommend Bet Count              = SUM(chatroom_recommend.RecommendCount)  on SabaMatchId × AnchorId
L1 Follow Streamer Bet Turnover(RM) = SUM(follow_member_to)                     -- raw RM
L1 Follow User Count                = COUNT(DISTINCT cust_id) WHERE follow_bet_count > 0
L1 gift User Count              = COUNT(DISTINCT cust_id) WHERE if_tip|if_box|if_wheel = 1
L1 gift Count                   = SUM(tip_count) + SUM(box_count) + SUM(wheel_count)
L1 Tip / Box / Wheel Amount (USD)   = SUM(*_amount_rm) / 4.2  (one per channel)
L1 Tip / Box / Wheel Count          = SUM(tip_count) / SUM(box_count) / SUM(wheel_count)
L1 Stream Count                     = COUNT(DISTINCT CONCAT(anchor_id,'-',stream_id)) at streamer × period grain
L2 Bet Turnover (Follow Streamer / Follow User / Self) — Self = total − the other two; no Follow System.
L2 Bet During Watch — Count         = SUM(during_watch_bet_count)
L2 Bet During Watch — User          = COUNT(DISTINCT cust_id) WHERE during_watch_bet_count > 0
L2 Bet During Watch — Avg bet size  = SUM(during_watch_member_to) / SUM(during_watch_bet_count)
L2 Watch Time total (min)           = SUM(watch_sec) / 60
L2 Watch Time per viewer (min)      = (SUM(watch_sec)/60) / COUNT(DISTINCT cust_id WHERE if_watch = 1)
Viewers                             = COUNT(DISTINCT cust_id WHERE if_watch = 1)
Viewers over 10 min                 = COUNT(DISTINCT cust_id WHERE if_watch=1 AND watch_sec >= 600)
PCU (peak concurrent users)         = MAX(call_pcu) from nf-bifrost.livestream_dm.mart_comprehensive_metrics
```

### Filters
- `is_cancelled = FALSE` (exclude cancelled streams)
- World Cup filter: `match_info.League LIKE '%WORLD CUP%'` (exact value to be confirmed via Phase 0 probe)
- For "Bet During Watch" we don't need to recompute the overlap — the `during_watch_*` columns and `is_during_watch` flag are pre-computed

### Open data questions (small, can be resolved in one probe each)
1. **gift composition** — does "gift" = tip only, or tip + box + wheel? Default: include all three.
2. **World Cup filter value** — exact string in `match_info.League` (likely `WORLD CUP`).
3. **Match stage** — verify kickoff dates fit the 7-stage map.
4. **`is_lic` column** — meaning? (suspect "logged-in customer"). Current filter keeps `is_lic = 1`.

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

All `agg_*` and `dim_*` SQL writes to `nf-muses.worldcup.*`. Currency convention: turnover RM, amounts USD. No Follow System category in this dashboard.

- `agg_session_metrics.sql` — `GROUP BY stream_id` on `core_streaming_performance` + JOIN `chatroom_recommend` + JOIN `match_info`. Self bet = total − follow_streamer − follow_user. Adds `gift_count`, PCU placeholder.
- `agg_streamer_weekly.sql` — reads `agg_session_metrics`; cumulative-prior-weeks avg replaces 4-week rolling median.
- `agg_streamer_monthly.sql` — reads `agg_session_metrics`; June vs July only with `vs_june` delta.
- `agg_match_platform_compare.sql` — Scope A per match + Scope B season-window aggregate. Platform side TODO: restrict to sites with streamer function.
- `dim_match.sql` — built from `match_info`; `match_stage` derived from kickoff date per 7-stage map; no `team_popularity_tier`.
- `dim_streamer.sql` — built from `chatroom_anchor`; `tier` derived from streamer history.

---

## Phase 0 — BQ Discovery (now: small targeted probes only)

Schemas are known. Four probes remain (see `sql/phase0_discovery.sql`):

1. **gift composition** — confirm tip + box + wheel populated; default include all three.
2. **World Cup filter** — distinct `League` / `LeagueGroup` strings for fixtures ≥ 2026-06-01; expected `WORLD CUP`.
3. **`is_lic` meaning + `status_id` settled vs voided** — confirms filter assumptions.
4. **Match-stage date fit** — distinct kickoff dates for World Cup fixtures must match the 7-stage map (group 6/11–6/28, R32 6/29–7/4, R16 7/5–7/8, QF 7/10–7/12, SF 7/15–7/16, 3rd_place 7/19, final 7/20).

After these probes, the remaining TODOs in the SQL files can be locked.

---

## Phase 1 — Dataset Design

Materialized BQ tables in `nf-muses.worldcup`, written by scheduled query.

| Table | Grain | Contents |
|---|---|---|
| `agg_session_metrics` | one row per stream session | All NS + L1 + L2 for that session; joined to dim_match |
| `agg_streamer_weekly` | streamer × ISO week | Weekly totals, cumulative-prior-weeks avg, WoW % |
| `agg_streamer_monthly` | streamer × month (June/July) | Monthly totals + vs-June delta |
| `agg_match_platform_compare` | match_id | Our + platform metrics, share % for Scope A; season-window scalars for Scope B |
| `dim_match` | match_id | kickoff_ts, teams, stage, time_slot, day_of_week |
| `dim_streamer` | streamer_id | name, supplier, tier (rookie/regular/top) |

Each `agg_*` table includes `as_of_date` so reruns are idempotent (insert-overwrite by date partition).

---

## Phase 2 — Automation Flow

```
[BigQuery raw tables]
        ↓
[BQ Scheduled Query — daily 13:00 Taipei]
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
- Filters: date range, streamer, match stage
- Table: row per session; NS + key L1 columns; stage-adaptive baseline delta column; sparkline
- Drill-down: full L2 breakdown card on row click

**Tab 2 — Streamer weekly + monthly**
- Toggle weekly/monthly
- This-period NS totals + period-over-period delta (week = cumulative-prior-weeks avg; month = vs June)
- Top 10 streamer leaderboard (tier-filterable)
- Top match leaderboard
- Season trend line

**Tab 3 — Platform comparison**
- Match-share scoreboard (Scope A): our % per match
- Season-share scorecard (Scope B): our vs platform across full World Cup
- Filters: stage, streamer

**Tab 4 — Alert log**
- Columns: date, metric, streamer/match, type (threshold/anomaly), severity, value, expected, acknowledged
- Filters: severity, date, ack status

---

## Phase 4 — Alerts & Daily Slack Digest

Two complementary Slack surfaces so Clara never needs to open the dashboard:

### 4a. Daily digest (always sent, ~15:00 Taipei)

```
📊 World Cup Dashboard — <date>
<N> streams · <N> streamers · <N> matches

KPI snapshot (yesterday vs rolling-5 median):
  Follow Streamer Bet Count (NS)       <total>   ▲/▼ <%>
  Bet During Watch Turnover (NS, RM)   <total>   ▲/▼ <%>
  gift Amount (NS, USD)            <total>   ▲/▼ <%>
  Follow Streamer Bet Turnover (L1,RM) <total>   ▲/▼ <%>
  gift User Count (L1)             <total>   ▲/▼ <%>
  Bet During Watch Count (L2)          <total>   ▲/▼ <%>

⚽ Top 1 match + streamer highest Follow bet count
⚽ Top 1 match + streamer highest BdW turnover
⚽ Top 1 match + streamer highest gift


⚠️ <N> alerts overnight — see thread
```

Implementation in `apps_script/alerts.gs` → `runDaily()`; scheduled at ~15:00 Taipei (13:00 batch + 2h buffer).

### 4b. Threshold + anomaly alerts (triggered)

**Min-volume gate (always applied):** session must have ≥100 viewers AND ≥10 bets.

**Per-session thresholds** — drop vs streamer's rolling-5 median:

| KPI | Medium | High |
|---|---|---|
| Follow Streamer Bet Count (NS) | ≤ −30% | ≤ −50% |
| Bet During Watch Turnover (NS, RM) | ≤ −30% | ≤ −50% |
| gift Amount (NS, USD) | ≤ −40% | ≤ −60% |
| Follow Streamer Bet Turnover (L1, RM) | ≤ −30% | ≤ −50% |
| gift User Count (L1) | ≤ −30% | ≤ −50% |
| Bet During Watch Count (L2) | ≤ −30% | ≤ −50% |

**Weekly:** weekly stream count drops >20% vs cumulative-prior-weeks average.
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

**Currency (worldcup dashboard):** turnover in **RM** (raw `member_to` family); gift / tip / box / wheel in **USD** (MYR / 4.2). See `bq-filter-rules` skill for the override note.

**Scope A (per match)** — `our / platform` on the same `SabaMatchId`, platform side restricted to sites with streamer function. Answers: "Did our streams capture more of this match?"

**Scope B (season-window aggregate)** — `our_season / platform_season` across the full World Cup window (2026-06-11 → 2026-07-20). Single scalar. Answers: "Did our streamers move overall platform betting across the tournament?"

Scope A rolls up to streamer / week / month via `dim_streamer`. Scope B is a season-level scorecard.

---

## Easy-to-miss gotchas (worth checking before locking)

1. **Per-viewer normalization.** A drop in raw Tip Amount can be (a) fewer viewers or (b) same viewers tipping less — different fixes. Track raw + per-viewer side-by-side.
2. **Stream-length normalization.** 30-min vs 3-hour streams produce wildly different absolutes. Per-hour rates fix this.
3. **Bet status filter.** `fact_live_bet.status_id` includes pending/voided — confirm settled values via probe before using `member_to`.
4. **Mixed-currency reporting (intentional).** Turnover stays RM (`*_turnover_rm`); gift/tip/box/wheel convert to USD (`*_usd`). Make sure the digest and dashboard label each metric with its unit so RM and USD aren't compared directly.
5. **Mean reversion.** After a hot streak, "back to normal" looks like a drop. Pair relative threshold with an absolute floor.
6. **Tier-aware thresholds.** Rookies are noisier than top streamers. Consider looser thresholds (or skip anomaly alerts) for `dim_streamer.tier = 'rookie'`.
7. **Match mixing.** Brazil match vs minor-team match in a rolling median is apples-to-oranges. The stage-adaptive baseline (group/R32/R16 use 3 priors; QF+ use 1 prior) partly addresses this; surface match label in the alert context so reviewers can dismiss false positives.
8. **Late-settling bets.** Bets settle after match ends. The 13:00 batch should catch most overnight settlements; if needed, schedule a 24h-rerun for the prior-prior day.
9. **Slack noise budget.** 50 streamers × 6 KPIs = many candidates. Cap via digest model + tier filter + dedup (one streamer × metric × day max).
10. **Streamer absence.** If a streamer skips a day, the rolling-5 doesn't shift — they look "fine" with zero data. The `absent` alert (≥1 stream in last 3d + 0 yesterday) catches this.
11. **Min-volume gate edge.** 99 viewers = suppressed. Start at 100 viewers + 10 bets; tune after week 1.
12. **World Cup format change.** Group stage = 4 matches/day; knockout = 1–2/day. WoW comparison across the boundary is misleading — flag the transition date in the digest.
13. **Digest delivery timing.** ~15:00 Taipei = 13:00 batch + 2h buffer. If batches sometimes run late, gate the digest on a "data complete" sentinel cell.
14. **Holiday calendars.** Local holidays change viewing patterns. Add a `holidays` lookup or annotate the digest.

---

## Verification Plan

1. **Post-Phase-0:** review BQ schema map with Clara; confirm all NS/L1/L2 computable; close gaps before SQL.
2. **Post-Phase-1:** hand-compute NS + L1 for one sample session via raw SQL; must match `agg_session_metrics` exactly.
3. **Post-Phase-2:** verify daily 13:00 Taipei refresh runs 3 days straight without manual intervention; check partition idempotency by re-running a day.
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
3. **Run the 4 discovery probes** (`sql/phase0_discovery.sql`) to confirm:
   - gift composition (tip + box + wheel?)
   - World Cup filter string in `match_info.League` (likely `WORLD CUP`)
   - `is_lic` meaning + `status_id` settled values
   - Match-stage date fit (verify kickoffs match the 7-stage map)

After step 3, lock the SQL and schedule the daily 13:00 Taipei refresh.
