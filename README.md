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
2. **Phase 1** — fill `__RAW_*__` placeholders in agg-table SQL with discovered table/column names; schedule daily 06:00 Taipei.
3. **Phase 2** — connect BQ → Sheets (one tab per agg table).
4. **Phase 3** — build Looker Studio dashboard (4 tabs: Session / Weekly+Monthly / Platform / Alert log).
5. **Phase 4** — deploy `apps_script/alerts.gs` to the Sheet; configure Slack webhook + min-volume gate.

## Required inputs from Clara

- BQ project ID
- BQ dataset name(s) holding raw streams / bets / watch / donations / recommendations
- Slack webhook URL
- World Cup fixtures source (if no internal `matches` table exists)
