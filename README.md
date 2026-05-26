# Streamer Performance Dashboard

Dashboard tracking streamer performance during the World Cup (June–July 2026).
Source: BigQuery → Google Sheets → Looker Studio. Alerts via Slack + in-sheet.

See `docs/plan.md` for the full approved design.

## Repo layout

```
docs/
  plan.md                       # approved design plan
sql/
  phase0_discovery.sql          # INFORMATION_SCHEMA probes — run first
  agg_session_metrics.sql       # one row per stream session
  agg_streamer_weekly.sql       # streamer × ISO week
  agg_streamer_monthly.sql      # streamer × calendar month
  agg_match_platform_compare.sql# match-level + time-window-aggregate vs platform
  dim_match.sql                 # World Cup fixtures (build if missing)
  dim_streamer.sql              # streamer attributes
apps_script/
  alerts.gs                     # Slack + in-sheet alert evaluator
```

## Build order

1. **Phase 0** — run `sql/phase0_discovery.sql` against the streaming BQ dataset; produce schema map + gap report.
2. **Phase 1** — fill remaining TODOs in agg-table SQL with discovered values; schedule daily 13:00 Taipei.
3. **Phase 2** — connect BQ → Sheets (one tab per agg table).
4. **Phase 3** — build Looker Studio dashboard (4 tabs: Session / Weekly+Monthly / Platform / Alert log).
5. **Phase 4** — deploy `apps_script/alerts.gs` to the Sheet; configure Slack webhook + min-volume gate.

## BQ project + datasets (resolved)

**Region: `asia-southeast1` (Singapore) — for everything.**

- Source: `nf-bifrost` — `livestream_dm` + `LiveStreaming` + `VN_CTS_Data`
- Reference: `nf-muses.muses` — TFU user-month features
- Reporting output: `nf-muses.worldcup.agg_*` and `nf-muses.worldcup.dim_*` tables

Before running any agg SQL: create the `worldcup` dataset in `asia-southeast1`:
```bash
bq --location=asia-southeast1 mk --dataset nf-muses:worldcup
```
Scheduled queries that build agg tables must also be created with `--location=asia-southeast1`.

See `.claude/skills/bq-schemas/SKILL.md` for the full schema map and `.claude/skills/bq-filter-rules/SKILL.md` for required WHERE clauses, currency conventions, and column aliases (bdw_*, *_turnover_rm, *_usd, day, language).

**Reporting currency:** turnover (`*_turnover_rm`) is **RM**; donation / tip / box / wheel amounts (`*_usd`) are **USD** (MYR / 4.2).

## Open data questions (resolve via `sql/phase0_discovery.sql`)

1. Donation composition — tip + box + wheel, or tip only?
2. Exact World Cup string in `match_info.League` (likely `WORLD CUP`)
3. `is_lic` meaning + `status_id` non-voided values
4. Match-stage date fit — distinct kickoff dates match the 7-stage map

## Slack webhook

Configured in Apps Script Script Properties: `SLACK_WEBHOOK_URL`. **Rotate the webhook** that was shared in chat before relying on it in production.
