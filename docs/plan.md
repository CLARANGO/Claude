# Streamer Performance Dashboard — Final Plan

## Context
Clara (data analysis) needs a dashboard tracking streamer performance during the **World Cup (June–July 2026)**. Each streamer covers live football matches; viewers can follow streamer picks, place own bets, donate, and tip. Three views are required:

1. **Per-live-session performance** (one row per match a streamer covers)
2. **Weekly + monthly streamer performance** (Mon–Sun ISO weeks; calendar months)
3. **Our product vs Platform comparison** (platform only exposes bet turnover + count)

Source: existing BigQuery tables (schemas unknown — needs Phase 0 discovery).
Output: BQ materialized agg tables → Google Sheets → Looker Studio.
Refresh: daily batch, 06:00 Taipei (UTC+8).
Audience: ops team + management (view-only).
Alerts: Slack webhook + in-sheet conditional formatting.

---

## Metrics Tree (locked)

```
NORTH STAR
├── Follow Streamer Bet Count     ← bets in the Follow-Streamer category
└── Donation Amount (incl. Tips)  ← Tips roll up into Donation total

L1 — DRIVERS
├── Recommend Bet Count           ← streamer's recommend bet
├── Follow Streamer Bet Turnover  ← $ on follow-streamer bets
├── Follow User Count             ← unique users placing follow-streamer bets
├── Donation Amount (total)       ← includes tips
├── Donation User Count
├── Tip Amount                    ← subset of Donation (broken out)
├── Tip Count
├── Tip User Count
└── Stream Count                  ← # live sessions in period

L2 — DECOMPOSITION
├── Bet Count by category
│   ├── Self
│   ├── Follow User
│   ├── Follow System
│   └── Follow Streamer
├── Bet Turnover by category (same 4)
├── Bet During Watch — Count      ← bet placed while user actively watching stream
├── Bet During Watch — Turnover
└── Watch Time (total + avg per viewer)
```

### Locked definitions
- **NS "Follow Streamer Bet Count"** = Follow Streamer category only (strictest definition of stream influence). Same metric appears at L2 as decomposition.
- **Recommend Bet Count** = streamer-recommended bets — viewer placed a bet on the streamer's recommended pick during the live session. Not system recommendations.
- **Donation/Tip** = Tip is a subset of Donation. NS Donation Amount = full total; Tip metrics broken out at L1.
- **Bet During Watch** = bet placed by a user whose watch session overlaps the bet timestamp (same stream).
- **Like-for-like guards:** exclude voided bets; exclude flagged bot/multi-accounts on our side.

---

## World Cup Context & Match Tagging

June–July is a single tournament — comparability is easier, but variance comes from team popularity and stage. Tag matches in `dim_match`:

- `match_stage` — group / R16 / QF / SF / final
- `team_popularity_tier` — tier 1 (Brazil, Argentina, England, France, Germany, Spain…) / tier 2 / tier 3 (by FIFA ranking or platform historical bet volume)
- `time_slot_taipei` — prime (20:00–24:00) / late-night / morning / afternoon (derived from kickoff)
- `day_of_week`

---

## Comparison Views

### 1. Match-by-match (per session)
- Each row = one live session (stream_id, streamer_id, match_id)
- Compare current vs **rolling median of streamer's last 5 matches**
- Display: current, rolling median, delta %, sparkline
- **Sample-size guard:** <3 prior matches → show "n/a"

### 2. Week (ISO Mon–Sun)
- Current week vs rolling-4-week median
- WoW % change
- Top 5 streamers + top 5 matches by NS

### 3. Month
- MTD vs prior month
- 6-month trend
- Top streamer + top match of the month

### 4. Our Product vs Platform (two scopes)

**Scope A — Match-level (per match our streamers covered):**
- Our bet turnover / Platform bet turnover on that match = **match share of wallet**
- Our bet count / Platform bet count = **match share of bets**
- Our avg bet size vs platform avg bet size
- Rolls up by streamer / week / month

**Scope B — Time-window aggregate (per session window):**
- Our bet turnover during stream window / Platform total bet turnover during same window = **time-window share**
- Tests whether our streams move overall platform activity, not just the covered match

Definitions:
- Our product bet = Bet During Watch (watch + bet timestamps overlap)
- Platform bet = all bets on platform (Scope A: same match; Scope B: same time window)
- Both sides: exclude voided

---

## Phase 0 — BQ Discovery (must run first)

Tables exist but schemas are unknown. Output of this phase is a schema map + gap report.

1. `INFORMATION_SCHEMA.TABLES` + `COLUMNS` on candidate dataset to enumerate tables.
2. For each candidate table (likely names: `streams`, `sessions`, `bets`, `recommendations`, `follows`, `donations`, `tips`, `watch_sessions`, `users`, `matches`):
   - Column list + types
   - Row count + date range
   - Sample 5 rows
   - Identify primary key + join keys
3. Map BQ entities to required metrics:

| Metric area | Likely table | Required columns |
|---|---|---|
| Stream sessions | streams/sessions | stream_id, streamer_id, match_id, start_ts, end_ts |
| Bets (4 categories) | bets | bet_id, user_id, stream_id, match_id, category, turnover, placed_ts, status |
| Streamer recommendations | recommendations | rec_id, streamer_id, stream_id, match_id, pick, shown_ts |
| Recommend-bet conversions | bets ↔ recommendations | bet_id linked to rec_id, OR bet.context='streamer_rec' flag |
| Watch sessions | watch_sessions | user_id, stream_id, watch_start_ts, watch_end_ts |
| Follows (category dim) | follows or bet.category | user/system/streamer flag |
| Donations + Tips | donations | donation_id, user_id, stream_id, amount, type, created_ts |
| Platform bets | bets (no stream filter) | match_id, turnover, count, placed_ts |
| Match dim | matches | match_id, kickoff_ts, team_home, team_away, stage |

4. **Gap report** — flag any metric not computable from existing tables.
5. If no World Cup fixtures table exists, build `dim_match` from FIFA fixture list (~64 matches).

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

## Phase 4 — Alert Rules

**Min-volume gate (always applied):** session must have ≥100 viewers AND ≥10 bets, else suppress alerts.

**Threshold alerts** (configurable per metric):
- NS Follow Streamer Bet Count drops >30% vs rolling-5 median
- Donation Amount drops >40% vs rolling-5 median
- Watch Time drops >25% vs rolling-5 median
- Weekly Stream Count drops >20% vs rolling-4-week median
- Platform share drops >5 percentage points

**Anomaly alerts:** any L1 metric outside rolling-5 median ± 2 × MAD.

**Severity routing:**
- High → Slack channel ping (@ops-team) + in-sheet red
- Medium → Slack channel post (no @) + in-sheet yellow
- All → Alert log row

---

## Verification Plan

1. **Post-Phase-0:** review BQ schema map with Clara; confirm all NS/L1/L2 computable; close gaps before SQL.
2. **Post-Phase-1:** hand-compute NS + L1 for one sample session via raw SQL; must match `agg_session_metrics` exactly.
3. **Post-Phase-2:** verify daily 06:00 Taipei refresh runs 3 days straight without manual intervention; check partition idempotency by re-running a day.
4. **Post-Phase-3:** UAT with one ops user + one manager; confirm filters, drill-downs, <5s load.
5. **Post-Phase-4:** synthetic threshold breach (edit a metric cell); confirm Slack + in-sheet alert fire within 10 min; confirm min-volume gate suppresses correctly.

---

## Recommended Next Action
Kick off **Phase 0 BQ discovery**: write SQL probes (`INFORMATION_SCHEMA` queries + sample-row pulls) against the streaming dataset. Deliverable is a schema map + gap report we use to write the agg-table SQL. Needs from Clara: BQ project + dataset name(s).
