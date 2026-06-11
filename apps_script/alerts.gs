/**
 * Streamer Performance Dashboard — Alert + Daily Digest Layer
 *
 * Two surfaces in Slack:
 *   1. Daily digest at ~15:00 Taipei (always sent — 13:00 batch + 2h buffer) —
 *      KPI snapshot, top streamers, top matches, alert count summary.
 *   2. Threshold + anomaly alerts (only when triggered) — threaded
 *      under the digest message for that day; high-severity gets its
 *      own channel ping.
 *
 * Setup:
 *   1. Open the Sheet connected to BQ (one tab per nf-muses.worldcup.* table).
 *   2. Extensions → Apps Script → paste this file.
 *   3. Project Settings → Script Properties → add:
 *        SLACK_WEBHOOK_URL  = https://hooks.slack.com/services/...
 *        SLACK_HIGH_MENTION = <!channel>   (or <!subteam^TEAMID>, or blank)
 *   4. Triggers (clock icon) → add daily trigger on `runDaily` at 15:00 Taipei.
 *
 * Currency convention (worldcup dashboard):
 *   • Turnover columns (*_turnover_rm) are RM — formatted with the 'rm' fmt.
 *   • Donation / tip / box / wheel amounts (*_usd) are USD — formatted with 'usd'.
 */

const CONFIG = {
  SESSION_TAB: 'agg_session_metrics',
  WEEKLY_TAB:  'agg_streamer_weekly',
  MATCH_COMPARE_TAB: 'agg_match_platform_compare',
  LOG_TAB: 'Alert Log',
  SNAPSHOT_TAB: 'Data Snapshot',
  TIMEZONE: 'Asia/Taipei',

  // Site column convention: 'All site' = aggregated across sites; specific
  // site name = single-site row. Daily uses per-site rows; weekly uses
  // only 'All site' rows. Daily baselines (last 2 / May avg) are filtered
  // to the same (streamer, site) pool — don't mix sites.
  ALL_SITE_LABEL: 'All Site',

  // The 6 [ALERT] KPIs (per revised metrics tree). Order = how they appear in the digest.
  KPIS: [
    { col: 'follow_streamer_bet_count',        label: 'Follow Streamer Bet Count (NS)',    fmt: 'int' },
    { col: 'bdw_turnover_rm',                  label: 'Bet During Watch Turnover (NS)',    fmt: 'rm'  },
    { col: 'donation_amount_usd',              label: 'Donation Amount (NS)',              fmt: 'usd' },
    { col: 'follow_streamer_bet_turnover_rm',  label: 'Follow Streamer Bet Turnover (L1)', fmt: 'rm'  },
    { col: 'donation_user_count',              label: 'Donation User Count (L1)',          fmt: 'int' },
    { col: 'bdw_bet_count',                    label: 'Bet During Watch Count (L2)',       fmt: 'int' },
  ],

  // Daily per-match table + weekly KPI table. 'sum' aggs total via SUM, 'max' via MAX,
  // 'rate' is rateNum/rateDen (weighted across the pool for totals, mean for avg/match).
  DISPLAY_METRICS: [
    { col: 'follow_streamer_bet_count',        label: 'Follow Streamer Bet Count',    fmt: 'int', agg: 'sum' },
    { col: 'bdw_turnover_rm',                  label: 'Bet During Watch Turnover',    fmt: 'rm',  agg: 'sum' },
    { col: 'donation_amount_usd',              label: 'Donation Amount',              fmt: 'usd', agg: 'sum' },
    { col: 'follow_streamer_bet_turnover_rm',  label: 'Follow Streamer Bet Turnover', fmt: 'rm',  agg: 'sum' },
    { col: 'follow_user_count',                label: 'Follow Bet User',              fmt: 'int', agg: 'sum' },
    { col: 'follow_bet_user_rate',             label: 'Follow Bet User Rate',         fmt: 'pct', agg: 'rate',
      rateNum: 'follow_user_count', rateDen: 'bdw_user_count' },
    { col: 'donation_user_count',              label: 'Donation User Count',          fmt: 'int', agg: 'sum' },
    { col: 'bdw_bet_count',                    label: 'Bet During Watch Count',       fmt: 'int', agg: 'sum' },
    { col: 'during_watch_user_rate',           label: 'BDW User Rate',                fmt: 'pct', agg: 'rate',
      rateNum: 'bdw_user_count', rateDen: 'viewers' },
    { col: 'pcu',                              label: 'PCU',                          fmt: 'int', agg: 'max' },
  ],

  STAGE_LABELS: {
    'group':     'Group Stage',
    'R32':       'Round of 32',
    'R16':       'Round of 16',
    'QF':        'Quarter-Final',
    'SF':        'Semi-Final',
    '3rd_place': 'Third Place',
    'final':     'Final',
    'other':     'Other',
  },

  // Min-volume gate (per session)
  MIN_VIEWERS: 100,
  MIN_BETS: 10,

  // Drop thresholds vs rolling-5 median; severity tiers.
  // Donation Amount uses the looser tier (it's noisier than bet counts).
  THRESHOLDS: {
    follow_streamer_bet_count:        { medium: -0.30, high: -0.50 },
    bdw_turnover_rm:                  { medium: -0.30, high: -0.50 },
    donation_amount_usd:              { medium: -0.40, high: -0.60 },
    follow_streamer_bet_turnover_rm:  { medium: -0.30, high: -0.50 },
    donation_user_count:              { medium: -0.30, high: -0.50 },
    bdw_bet_count:                    { medium: -0.30, high: -0.50 },
  },

  // Anomaly: outside median ± k·MAD
  ANOMALY_MAD_MULTIPLIER: 2,

  // Sample-size guard: need ≥ N prior sessions
  ROLLING_WINDOW: 5,
  MIN_SAMPLE_FOR_DELTA: 3,

  // Streamer-absent: was active in last N days, didn't stream yesterday
  ABSENT_LOOKBACK_DAYS: 3,

  // Data-revision drift alert: each run snapshots the last N days of
  // per-(stream, site) KPI values. If a previously-snapshotted day's value
  // changes ≥ DRIFT_THRESHOLD on a later run, fire a revision alert.
  DRIFT_THRESHOLD: 0.15,
  SNAPSHOT_LOOKBACK_DAYS: 7,
  SNAPSHOT_RETENTION_DAYS: 14,
};

// ============================================================
// Entry points
// ============================================================

/** Daily trigger — runs once per day at ~15:00 Taipei (13:00 batch + 2h buffer). */
function runDaily() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const yesterday = yesterdayInTz_(CONFIG.TIMEZONE);
  const win = reportWindow_(CONFIG.TIMEZONE);

  const sessions = readTab_(ss, CONFIG.SESSION_TAB);
  if (!sessions.length) {
    postSlack_('No data found in `' + CONFIG.SESSION_TAB + '` tab — skipping today.');
    return;
  }

  // Drift check first — compare current values against the prior snapshot of
  // the same data dates. Catches the case where the 12pm refresh silently
  // revised yesterday's numbers.
  const drift = evaluateDrift_(ss, sessions);
  if (drift.length) postDriftAlerts_(drift);

  const alerts = evaluateAlerts_(sessions, yesterday);
  const digest = buildDigest_(sessions, win, alerts);

  // Post main digest, then thread alerts under it
  const ts = postSlackBlocks_(digest);
  if (alerts.length) {
    postAlertThread_(alerts, ts);
  }
  if (alerts.some(function(a) { return a.severity === 'high'; })) {
    postHighSeverityPing_(alerts.filter(function(a) { return a.severity === 'high'; }));
  }

  writeAlertLog_(ss, alerts, yesterday);
  applyConditionalFormatting_(ss);

  // Weekly report — fires every Monday, summarising the completed Mon–Sun week
  if (isMonday_()) {
    const weekRange = priorWeekRange_(yesterday);
    const weekReport = buildWeeklyDigest_(sessions, weekRange.start, weekRange.end);
    postSlack_(weekReport);
  }

  // Snapshot last N days AFTER drift check so we capture today's read for
  // tomorrow's comparison.
  snapshotMetrics_(ss, sessions);
  pruneSnapshot_(ss);
}

/** Test helper — posts a sample daily digest, no thread. */
function testDigest() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const yesterday = yesterdayInTz_(CONFIG.TIMEZONE);
  const win = reportWindow_(CONFIG.TIMEZONE);
  const sessions = readTab_(ss, CONFIG.SESSION_TAB);
  const alerts = evaluateAlerts_(sessions, yesterday);
  const digest = buildDigest_(sessions, win, alerts);
  postSlackBlocks_(digest);
}

/**
 * Test helper — manually posts the weekly report for the most recent
 * completed Mon–Sun week. Use this on non-Mondays since runDaily only
 * fires the weekly report on Mondays.
 */
function testWeekly() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sessions = readTab_(ss, CONFIG.SESSION_TAB);
  if (!sessions.length) {
    postSlack_('No data found in `' + CONFIG.SESSION_TAB + '` tab — weekly skipped.');
    return;
  }
  // Find the most recent Sunday on or before yesterday
  const today = new Date();
  const offsetToSunday = (today.getDay() + 6) % 7 + 1; // day=Sun(0)→1, Mon(1)→7
  const sun = new Date(today.getTime());
  sun.setDate(today.getDate() - offsetToSunday);
  const sunStr = Utilities.formatDate(sun, CONFIG.TIMEZONE, 'yyyy-MM-dd');
  const range = priorWeekRange_(sunStr);
  const report = buildWeeklyDigest_(sessions, range.start, range.end);
  postSlack_(report);
}

/** Simple webhook ping. */
function testSlack() {
  postSlack_('✅ Streamer dashboard alerts — test from Apps Script');
}

// ============================================================
// Digest builder
// ============================================================

function buildDigest_(sessions, win, alerts) {
  // Filter to the 12pm-yesterday → 12pm-today window. Baselines in
  // buildMatchBlock_ are scoped to the same (streamer, site).
  const yestRaw = sessions.filter(function(r) { return inWindow_(r, win); });
  // De-dupe: SQL outer JOIN can produce repeat rows for the same
  // (stream_id, stream_site_id) tuple. Keep the first.
  const seen = {};
  const yest = [];
  yestRaw.forEach(function(r) {
    const key = (r.stream_id || '') + '|' + (r.stream_site_id == null ? '' : r.stream_site_id);
    if (seen[key]) return;
    seen[key] = true;
    yest.push(r);
  });
  yest.sort(function(a, b) {
    return (Number(b.follow_streamer_bet_count) || 0) - (Number(a.follow_streamer_bet_count) || 0);
  });

  const matchBlocks = yest.map(function(s) { return buildMatchBlock_(sessions, s); });
  const alertSummary = summarizeAlerts_(alerts);
  const matchCount = distinct_(yest, 'stream_id').length;
  const topMatches = buildTopMatches_(yest, 3);

  return '*📊 World Cup Dashboard — ' + windowLabel_(win, CONFIG.TIMEZONE) + '*\n' +
    '_' + matchCount + ' matches · ' +
      distinct_(yest, 'streamer_id').length + ' streamers · ' +
      yest.length + ' per-site rows_\n\n' +
    (topMatches ? topMatches + '\n\n' : '') +
    (matchBlocks.length ? matchBlocks.join('\n\n') : '_No matches in this window._') +
    '\n\n' + alertSummary;
}

/**
 * Top N matches by Follow Streamer Bet Count, with streamer name + site.
 * Each match shows the key NS metrics so the team can spot leaders quickly.
 */
function buildTopMatches_(yest, n) {
  if (!yest || !yest.length) return '';
  const sorted = yest.slice().sort(function(a, b) {
    return (Number(b.follow_streamer_bet_count) || 0) - (Number(a.follow_streamer_bet_count) || 0);
  }).slice(0, n);
  const lines = sorted.map(function(s, i) {
    const streamer = s.streamer || s.anchor_id || '?';
    const site = s.stream_site || s.site || '';
    const matchId = s.stream_id || s.SabaMatchId || 'n/a';
    const matchName = cleanStreamName_(s.stream_name || '');
    return '  ' + (i + 1) + '. *' + streamer + '* (' + site + ') · Match ' + matchId +
           (matchName ? ' — ' + matchName : '') + '\n' +
           '     Follow Bets: ' + formatVal_(s.follow_streamer_bet_count, 'int') +
           ' · BDW: ' + formatVal_(s.bdw_turnover_rm, 'rm') +
           ' · Donations: ' + formatVal_(s.donation_amount_usd, 'usd');
  });
  return '*🏆 Top ' + sorted.length + ' matches (by Follow Bet Count):*\n' + lines.join('\n');
}

/**
 * Per-match block: header + single 4-column comparison table.
 * Columns: Metric | Value | vs Last 2 Matches | vs May Avg
 */
function buildMatchBlock_(sessions, s) {
  const streamId = s.stream_id || s.SabaMatchId || 'n/a';
  const streamer = s.streamer || s.streamer_id || '?';
  const site = s.site || 'unknown';
  const matchName = cleanStreamName_(s.stream_name || '');
  const stageKey = s.match_stage || 'n/a';
  const stageLabel = CONFIG.STAGE_LABELS[stageKey] || stageKey;
  const titleLine = stageLabel + (matchName ? ' — ' + matchName : '');

  // Baseline pool: same streamer AND same site (don't mix sites). Also
  // exclude 'All site' rows so we compare apples to apples.
  const samePool = sessions.filter(function(p) {
    return p.streamer_id === s.streamer_id &&
           (p.site || '') === site &&
           (p.site || '') !== CONFIG.ALL_SITE_LABEL;
  });

  // Baseline 1 — last 2 matches before this one
  const last2 = samePool
    .filter(function(p) { return new Date(p.day) < new Date(s.day); })
    .sort(function(a, b) { return new Date(a.day) - new Date(b.day); })
    .slice(-2);

  // Baseline 2 — same-site May 2026 average
  const may = samePool.filter(function(p) { return extractMonth_(p.day) === 5; });

  const rows = [['Metric', 'Value', 'vs Last 2', 'vs May Avg']];
  CONFIG.DISPLAY_METRICS.forEach(function(kpi) {
    const v = valueForRow_(s, kpi);
    rows.push([
      kpi.label,
      isNaN(v) ? 'n/a' : formatVal_(v, kpi.fmt),
      arrowDelta_(v, avgForKpi_(last2, kpi)),
      arrowDelta_(v, avgForKpi_(may, kpi)),
    ]);
  });

  return '*⚽ Match ' + streamId + ' · ' + streamer + ' · ' + site + '*\n' +
    '_' + titleLine + '_\n' +
    '```\n' + formatTable_(rows, [1]) + '\n```';
}

/** Mean of `col` across an array of session rows (NaNs filtered). */
function avgOf_(rows, col) {
  if (!rows || !rows.length) return 0;
  const vals = rows.map(function(r) { return Number(r[col]); }).filter(function(n) { return !isNaN(n); });
  if (!vals.length) return 0;
  return vals.reduce(function(a, x) { return a + x; }, 0) / vals.length;
}

/** Resolve a single-row value for a KPI (handles 'rate' agg). */
function valueForRow_(row, kpi) {
  if (kpi.agg === 'rate') {
    const num = Number(row[kpi.rateNum]) || 0;
    const den = Number(row[kpi.rateDen]) || 0;
    return den > 0 ? num / den : NaN;
  }
  return Number(row[kpi.col]);
}

/** Total of a KPI across rows. Sum for 'sum', max for 'max', weighted ratio for 'rate'. */
function totalForKpi_(rows, kpi) {
  if (!rows || !rows.length) return 0;
  if (kpi.agg === 'rate') {
    const num = sum_(rows, kpi.rateNum);
    const den = sum_(rows, kpi.rateDen);
    return den > 0 ? num / den : 0;
  }
  if (kpi.agg === 'max') {
    const vals = rows.map(function(r) { return Number(r[kpi.col]) || 0; });
    return vals.length ? Math.max.apply(null, vals) : 0;
  }
  return sum_(rows, kpi.col);
}

/** Mean of per-row KPI values (used for daily baselines + per-match avgs). */
function avgForKpi_(rows, kpi) {
  if (!rows || !rows.length) return 0;
  const vals = rows.map(function(r) { return valueForRow_(r, kpi); })
                   .filter(function(v) { return !isNaN(v); });
  if (!vals.length) return 0;
  return vals.reduce(function(a, x) { return a + x; }, 0) / vals.length;
}

/** Avg/Match for the weekly table — sum agg divides total by matchCount, others mean. */
function avgPerMatchForKpi_(rows, kpi, matchCount) {
  if (!matchCount) return 0;
  if (kpi.agg === 'sum') return totalForKpi_(rows, kpi) / matchCount;
  return avgForKpi_(rows, kpi);
}

/** Return colored delta indicator: '🟢 ▲ N%' / '🔴 ▼ N%' / 'n/a'. */
function arrowDelta_(v, baseline) {
  if (!(baseline > 0) || isNaN(v)) return 'n/a';
  const delta = (v - baseline) / baseline;
  const pct = Math.abs(delta * 100).toFixed(0) + '%';
  return delta >= 0 ? '🟢 ▲ ' + pct : '🔴 ▼ ' + pct;
}

/**
 * Render a 2D array as an aligned monospace table.
 * `rightCols` = array of column indices to right-align (numeric value cols).
 * All other columns are left-aligned.
 */
function formatTable_(rows, rightCols) {
  if (!rows.length) return '';
  rightCols = rightCols || [];
  const cols = rows[0].length;
  const widths = [];
  for (let c = 0; c < cols; c++) {
    let w = 0;
    rows.forEach(function(r) { w = Math.max(w, String(r[c] == null ? '' : r[c]).length); });
    widths.push(w);
  }
  return rows.map(function(r) {
    return r.map(function(v, i) {
      const s = String(v == null ? '' : v);
      return rightCols.indexOf(i) !== -1 ? padL_(s, widths[i]) : padR_(s, widths[i]);
    }).join('    ');
  }).join('\n');
}

/** Aggregate `col` across `rows` using sum / max / avg per the metric's agg field. */
function aggregate_(rows, col, agg) {
  const vals = (rows || []).map(function(r) { return Number(r[col]); }).filter(function(n) { return !isNaN(n); });
  if (!vals.length) return 0;
  if (agg === 'max') return Math.max.apply(null, vals);
  if (agg === 'avg') return vals.reduce(function(a, x) { return a + x; }, 0) / vals.length;
  return vals.reduce(function(a, x) { return a + x; }, 0);
}

/** Strip the trailing ` ( 12345 )` Saba-match-id suffix from stream_name. */
function cleanStreamName_(name) {
  if (!name) return '';
  return String(name).replace(/\s*\(\s*\d+\s*\)\s*$/, '').trim();
}

function extractMonth_(dayVal) {
  return Number(formatDate_(dayVal).split('-')[1]);
}

// ============================================================
// Weekly digest builder
// ============================================================

function buildWeeklyDigest_(sessions, weekStart, weekEnd) {
  // Weekly uses only the 'All site' aggregate rows (no per-site detail).
  const allSiteSessions = sessions.filter(function(r) {
    return (r.site || '') === CONFIG.ALL_SITE_LABEL;
  });
  const weekSessions = allSiteSessions.filter(function(r) {
    const d = formatDate_(r.day);
    return d >= weekStart && d <= weekEnd;
  });
  const matchCount = distinct_(weekSessions, 'stream_id').length;

  // Combined KPI table: Metric / Total / Δ vs prior weeks / Avg per Match / Δ vs prior weeks per-match
  const kpiTable = buildCombinedKpiTable_(allSiteSessions, weekSessions, weekStart, matchCount);

  // Per-language blocks — same combined table format, scoped to one language
  const langBlocks = buildLanguageBlocks_(allSiteSessions, weekSessions, weekStart);

  // 4) Top 5 streamers
  const byStreamer = groupAndSum_(weekSessions, 'streamer_id', ['follow_streamer_bet_count', 'bdw_turnover_rm', 'donation_amount_usd']);
  byStreamer.sort(function(a, b) { return b.follow_streamer_bet_count - a.follow_streamer_bet_count; });
  const top5Streamers = byStreamer.slice(0, 5).map(function(s, i) {
    const r = weekSessions.find(function(x) { return x.streamer_id === s.streamer_id; }) || {};
    return '  ' + (i + 1) + '. ' + (r.streamer || s.streamer_id) +
           ' — ' + (s.follow_streamer_bet_count || 0) + ' follow bets' +
           ', ' + formatVal_(s.bdw_turnover_rm, 'rm') + ' BDW' +
           ', ' + formatVal_(s.donation_amount_usd, 'usd') + ' donations';
  });

  // 5) Top 5 matches (with cleaned stream name)
  const byMatch = groupAndSum_(weekSessions, 'stream_id', ['follow_streamer_bet_count', 'bdw_turnover_rm']);
  byMatch.sort(function(a, b) { return b.follow_streamer_bet_count - a.follow_streamer_bet_count; });
  const top5Matches = byMatch.slice(0, 5).map(function(m, i) {
    const r = weekSessions.find(function(x) { return x.stream_id === m.stream_id; }) || {};
    const name = cleanStreamName_(r.stream_name || '');
    return '  ' + (i + 1) + '. Match ' + (m.stream_id || 'n/a') +
           (name ? ' — ' + name : '') +
           ' — ' + (m.follow_streamer_bet_count || 0) + ' follow bets' +
           ', ' + formatVal_(m.bdw_turnover_rm, 'rm') + ' BDW';
  });

  return '*📅 Weekly Report — ' + weekDateLabel_(weekStart, weekEnd) + '*\n' +
    '_' + matchCount + ' matches · ' +
      distinct_(weekSessions, 'streamer_id').length + ' streamers_\n' +
    '\n*NS Weekly KPIs:*\n```\n' + kpiTable + '\n```\n' +
    '\n*🌐 By Language:*\n' +
    (langBlocks.length ? langBlocks.join('\n\n') : '  _no data_') + '\n' +
    '\n*🏆 Top 5 streamers (Follow Bet Count):*\n' +
      (top5Streamers.length ? top5Streamers.join('\n') : '  _no data_') + '\n' +
    '\n*⚽ Top 5 matches (Follow Bet Count):*\n' +
      (top5Matches.length ? top5Matches.join('\n') : '  _no data_') + '\n';
}

/**
 * Combined weekly KPI table — one row per metric, with Total + Δ and
 * Avg/Match + Δ side by side. Uses CONFIG.DISPLAY_METRICS (6 alert KPIs
 * + PCU). PCU uses max for Total and mean for Avg/Match.
 *
 * `allSessions` is the unfiltered session pool for baseline math;
 * `weekSessions` is just the rows inside [weekStart, weekEnd].
 */
function buildCombinedKpiTable_(allSessions, weekSessions, weekStart, matchCount) {
  const rows = [[
    'Metric',
    'Total',
    'WoW',
    'Avg/Match',
    'WoW (avg/match)',
  ]];
  const prior = priorWeekSessions_(allSessions, weekStart);
  const priorMatchCount = distinct_(prior, 'stream_id').length;
  CONFIG.DISPLAY_METRICS.forEach(function(kpi) {
    const total = totalForKpi_(weekSessions, kpi);
    const avgPerMatch = avgPerMatchForKpi_(weekSessions, kpi, matchCount);
    const baselineTotal = totalForKpi_(prior, kpi);
    const baselineAvg = avgPerMatchForKpi_(prior, kpi, priorMatchCount);
    rows.push([
      kpi.label,
      formatVal_(total, kpi.fmt),
      arrowDelta_(total, baselineTotal),
      formatVal_(avgPerMatch, kpi.fmt),
      arrowDelta_(avgPerMatch, baselineAvg),
    ]);
  });
  // Col 1 = Total (right), col 3 = Avg/Match (right); cols 2 and 4 are deltas (left)
  return formatTable_(rows, [1, 3]);
}

/** Sessions in the previous ISO week (Mon–Sun) before weekStart. */
function priorWeekSessions_(sessions, weekStart) {
  const p = weekStart.split('-');
  const wsDate = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
  const priorStart = new Date(wsDate.getTime() - 7 * 86400000);
  const priorEnd   = new Date(wsDate.getTime() - 86400000);
  const priorStartStr = priorStart.getFullYear() + '-' + pad2_(priorStart.getMonth() + 1) + '-' + pad2_(priorStart.getDate());
  const priorEndStr   = priorEnd.getFullYear()   + '-' + pad2_(priorEnd.getMonth()   + 1) + '-' + pad2_(priorEnd.getDate());
  return sessions.filter(function(r) {
    const d = formatDate_(r.day);
    return d >= priorStartStr && d <= priorEndStr;
  });
}

/**
 * Per-language blocks — same shape as the weekly overview: a Totals
 * table and an Avg/Match table, each with the delta column. Baselines
 * are computed from prior weeks of the SAME language only, so each
 * language is compared against its own history.
 */
function buildLanguageBlocks_(sessions, weekSessions, weekStart) {
  const byLangWeek = {};
  weekSessions.forEach(function(r) {
    const lang = r.language || 'unknown';
    if (!byLangWeek[lang]) byLangWeek[lang] = [];
    byLangWeek[lang].push(r);
  });

  // Sort languages by Follow Streamer Bet Count desc
  const sortedLangs = Object.keys(byLangWeek).sort(function(a, b) {
    return sum_(byLangWeek[b], 'follow_streamer_bet_count') -
           sum_(byLangWeek[a], 'follow_streamer_bet_count');
  });

  return sortedLangs.map(function(lang) {
    const langWeek = byLangWeek[lang];
    const langAll = sessions.filter(function(r) { return (r.language || 'unknown') === lang; });
    const matchCount = distinct_(langWeek, 'stream_id').length;
    const table = buildCombinedKpiTable_(langAll, langWeek, weekStart, matchCount);
    return '*' + lang + '* _(' + matchCount + ' matches)_\n```\n' + table + '\n```';
  });
}

/** Average of (per-match KPI value) across all complete prior weeks. */
function priorWeeksPerMatchAvg_(sessions, weekStart, kpi) {
  const perWeek = {};
  sessions.forEach(function(r) {
    if (formatDate_(r.day) >= weekStart) return;
    const w = isoWeekStart_(r.day);
    if (!perWeek[w]) perWeek[w] = [];
    perWeek[w].push(r);
  });
  const weeks = Object.keys(perWeek);
  if (!weeks.length) return 0;
  const weekAvgs = weeks.map(function(w) {
    const wkRows = perWeek[w];
    const matchCount = distinct_(wkRows, 'stream_id').length;
    return avgPerMatchForKpi_(wkRows, kpi, matchCount);
  });
  return weekAvgs.reduce(function(a, x) { return a + x; }, 0) / weekAvgs.length;
}

function summarizeAlerts_(alerts) {
  if (!alerts.length) return '✅ No alerts overnight.';
  const high = alerts.filter(function(a) { return a.severity === 'high'; }).length;
  const med  = alerts.filter(function(a) { return a.severity === 'medium'; }).length;
  const low  = alerts.filter(function(a) { return a.severity === 'low'; }).length;
  const parts = [];
  if (high) parts.push(high + ' high');
  if (med)  parts.push(med  + ' medium');
  if (low)  parts.push(low  + ' low');
  return '⚠️ *' + alerts.length + ' alert' + (alerts.length > 1 ? 's' : '') +
         ' overnight* (' + parts.join(', ') + ') — see thread for detail.';
}

// ============================================================
// Alert evaluation
// ============================================================

function evaluateAlerts_(sessions, yesterday) {
  const alerts = [];

  // Group sessions by streamer, sorted by session_date ASC
  const byStreamer = {};
  sessions.forEach(function(r) {
    if (!byStreamer[r.streamer_id]) byStreamer[r.streamer_id] = [];
    byStreamer[r.streamer_id].push(r);
  });
  Object.keys(byStreamer).forEach(function(k) {
    byStreamer[k].sort(function(a, b) {
      return new Date(a.day) - new Date(b.day);
    });
  });

  Object.keys(byStreamer).forEach(function(streamerId) {
    const ses = byStreamer[streamerId];
    const yestSessions = ses.filter(function(r) { return formatDate_(r.day) === yesterday; });

    yestSessions.forEach(function(s) {
      // Min-volume gate
      if (Number(s.viewers) < CONFIG.MIN_VIEWERS) return;
      if (Number(s.total_bet_count || 0) < CONFIG.MIN_BETS) return;

      // Rolling-5 prior window (sessions strictly before this one)
      const priors = ses
        .filter(function(r) { return new Date(r.day) < new Date(s.session_date); })
        .slice(-CONFIG.ROLLING_WINDOW);
      if (priors.length < CONFIG.MIN_SAMPLE_FOR_DELTA) return;

      CONFIG.KPIS.forEach(function(kpi) {
        const priorVals = priors.map(function(p) { return Number(p[kpi.col]); }).filter(function(v) { return !isNaN(v); });
        const med = median_(priorVals);
        const current = Number(s[kpi.col]);
        if (med <= 0 || isNaN(current)) return;
        const delta = (current - med) / med;

        // Threshold check
        const t = CONFIG.THRESHOLDS[kpi.col];
        if (t) {
          let severity = null;
          if (delta <= t.high)   severity = 'high';
          else if (delta <= t.medium) severity = 'medium';
          if (severity) {
            alerts.push(makeAlert_({
              date: yesterday, type: 'threshold', severity: severity,
              streamer_id: streamerId, streamer_name: s.streamer,
              match_id: s.stream_id || s.SabaMatchId, match_label: matchLabel_(s),
              metric: kpi.label, metric_col: kpi.col, fmt: kpi.fmt,
              value: current, expected: med, delta: delta,
              note: 'Drop ' + (delta * 100).toFixed(0) + '% vs rolling-5 median',
            }));
            return;
          }
        }

        // Anomaly check (only if no threshold fired)
        const mad = mad_(priorVals);
        if (mad > 0 && Math.abs(current - med) > CONFIG.ANOMALY_MAD_MULTIPLIER * mad) {
          alerts.push(makeAlert_({
            date: yesterday, type: 'anomaly', severity: 'medium',
            streamer_id: streamerId, streamer_name: s.streamer,
            match_id: s.SabaMatchId, match_label: matchLabel_(s),
            metric: kpi.label, metric_col: kpi.col, fmt: kpi.fmt,
            value: current, expected: med, delta: delta,
            note: 'Outside ±' + CONFIG.ANOMALY_MAD_MULTIPLIER + '·MAD of rolling-5 median',
          }));
        }
      });
    });
  });

  return alerts;
}

function makeAlert_(a) { return a; }

function matchLabel_(s) {
  return s.stream_id || s.SabaMatchId || '';
}

// ============================================================
// Slack I/O
// ============================================================

function postSlack_(text) {
  const url = PropertiesService.getScriptProperties().getProperty('SLACK_WEBHOOK_URL');
  if (!url) throw new Error('SLACK_WEBHOOK_URL not set in Script Properties');
  return UrlFetchApp.fetch(url, {
    method: 'post', contentType: 'application/json',
    payload: JSON.stringify({ text: text }),
    muteHttpExceptions: true,
  });
}

function postSlackBlocks_(text) {
  return postSlack_(text);
}

function postAlertThread_(alerts, _parentTs) {
  // Incoming Webhooks can't post into threads — we post a follow-up message.
  // If you upgrade to a Slack app with chat.postMessage + thread_ts, replace this.
  const lines = alerts.slice(0, 30).map(function(a) {
    return '• [' + a.severity + '] ' + (a.streamer_name || a.streamer_id) +
           (a.match_label ? ' — ' + a.match_label : '') +
           ' — *' + a.metric + '*: ' + a.note +
           (a.expected !== '—' ? ' (now ' + formatVal_(a.value, a.fmt) + ', expected ' + formatVal_(a.expected, a.fmt) + ')' : '');
  });
  if (alerts.length > 30) lines.push('_…and ' + (alerts.length - 30) + ' more in Alert Log_');
  postSlack_('*Alert detail:*\n' + lines.join('\n'));
}

function postHighSeverityPing_(alerts) {
  const mention = PropertiesService.getScriptProperties().getProperty('SLACK_HIGH_MENTION') || '';
  const lines = alerts.map(function(a) {
    return '• ' + (a.streamer_name || a.streamer_id) +
           (a.match_label ? ' — ' + a.match_label : '') +
           ' — *' + a.metric + '*: ' + a.note;
  });
  postSlack_(mention + ' *🚨 ' + alerts.length + ' high-severity alert(s):*\n' + lines.join('\n'));
}

// ============================================================
// Sheet I/O
// ============================================================

function writeAlertLog_(ss, alerts, date) {
  let tab = ss.getSheetByName(CONFIG.LOG_TAB);
  if (!tab) tab = ss.insertSheet(CONFIG.LOG_TAB);
  if (tab.getLastRow() === 0) {
    tab.appendRow(['date', 'type', 'severity', 'streamer_id', 'streamer_name',
                   'match_id', 'metric', 'value', 'expected', 'delta_pct', 'note', 'acknowledged']);
  }
  alerts.forEach(function(a) {
    tab.appendRow([a.date, a.type, a.severity, a.streamer_id, a.streamer_name || '',
                   a.match_id || '', a.metric, a.value, a.expected,
                   a.delta == null ? '' : (a.delta * 100).toFixed(1) + '%', a.note, false]);
  });
}

function applyConditionalFormatting_(ss) {
  const tab = ss.getSheetByName(CONFIG.LOG_TAB);
  if (!tab) return;
  const lastRow = Math.max(tab.getLastRow() - 1, 1);
  const range = tab.getRange(2, 3, lastRow, 1); // severity column
  const rules = [
    SpreadsheetApp.newConditionalFormatRule()
      .whenTextEqualTo('high').setBackground('#f4cccc').setRanges([range]).build(),
    SpreadsheetApp.newConditionalFormatRule()
      .whenTextEqualTo('medium').setBackground('#fff2cc').setRanges([range]).build(),
    SpreadsheetApp.newConditionalFormatRule()
      .whenTextEqualTo('low').setBackground('#d9ead3').setRanges([range]).build(),
  ];
  tab.setConditionalFormatRules(rules);
}

function readTab_(ss, name) {
  const tab = ss.getSheetByName(name);
  if (!tab || tab.getLastRow() < 2) return [];
  const values = tab.getDataRange().getValues();
  const header = values[0];
  return values.slice(1).map(function(row) {
    const obj = {};
    header.forEach(function(h, i) { obj[h] = row[i]; });
    // Backwards-compat aliases for column renames in the updated SQL:
    //   anchor_id   → streamer_id
    //   stream_site → site
    //   stream_id   → SabaMatchId (Clara joins m.SabaMatchId = csp.stream_id)
    if (obj.anchor_id   != null && obj.streamer_id == null) obj.streamer_id  = obj.anchor_id;
    if (obj.stream_site != null && obj.site        == null) obj.site         = obj.stream_site;
    if (obj.stream_id   != null && obj.SabaMatchId == null) obj.SabaMatchId  = obj.stream_id;
    return obj;
  });
}

// ============================================================
// Stats helpers
// ============================================================

function median_(arr) {
  if (!arr.length) return 0;
  const s = arr.slice().sort(function(a, b) { return a - b; });
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function mad_(arr) {
  const med = median_(arr);
  return median_(arr.map(function(v) { return Math.abs(v - med); }));
}

function sum_(rows, col) {
  return rows.reduce(function(a, r) { return a + (Number(r[col]) || 0); }, 0);
}

function distinct_(rows, col) {
  const set = {};
  rows.forEach(function(r) { if (r[col] != null && r[col] !== '') set[r[col]] = 1; });
  return Object.keys(set);
}

function groupAndSum_(rows, keyCol, valueCols) {
  const acc = {};
  rows.forEach(function(r) {
    const k = r[keyCol];
    if (k == null || k === '') return;
    if (!acc[k]) { acc[k] = { }; acc[k][keyCol] = k; valueCols.forEach(function(c) { acc[k][c] = 0; }); }
    valueCols.forEach(function(c) { acc[k][c] += (Number(r[c]) || 0); });
  });
  return Object.keys(acc).map(function(k) { return acc[k]; });
}

/**
 * Streamer-weighted rolling-5 median total: for each streamer, take the
 * median of their last 5 prior sessions; sum medians across streamers.
 * Coarse but stable baseline for "is yesterday's TOTAL normal?".
 */
function streamerWeightedRollingMedian_(sessions, yesterday, col) {
  const byStreamer = {};
  sessions.forEach(function(r) {
    if (formatDate_(r.day) >= yesterday) return;
    if (!byStreamer[r.streamer_id]) byStreamer[r.streamer_id] = [];
    byStreamer[r.streamer_id].push(r);
  });
  let total = 0;
  Object.keys(byStreamer).forEach(function(k) {
    const arr = byStreamer[k]
      .sort(function(a, b) { return new Date(a.day) - new Date(b.day); })
      .slice(-CONFIG.ROLLING_WINDOW)
      .map(function(r) { return Number(r[col]) || 0; });
    if (arr.length >= CONFIG.MIN_SAMPLE_FOR_DELTA) total += median_(arr);
  });
  return total;
}

// ============================================================
// Date + formatting helpers
// ============================================================

function yesterdayInTz_(tz) {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return Utilities.formatDate(d, tz, 'yyyy-MM-dd');
}

function formatDate_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, CONFIG.TIMEZONE, 'yyyy-MM-dd');
  return String(v).slice(0, 10);
}

function formatVal_(v, fmt) {
  if (v == null || v === '' || (typeof v === 'number' && isNaN(v))) return '—';
  if (fmt === 'rm')  return 'RM '  + Math.round(Number(v)).toLocaleString();
  if (fmt === 'usd') return 'USD ' + Math.round(Number(v)).toLocaleString();
  // Back-compat: 'money' falls through to USD.
  if (fmt === 'money') return 'USD ' + Math.round(Number(v)).toLocaleString();
  if (fmt === 'int') return Math.round(Number(v)).toLocaleString();
  if (fmt === 'pct') return (Number(v) * 100).toFixed(1) + '%';
  return String(v);
}

function padR_(s, n) { s = String(s); return s.length >= n ? s : s + ' '.repeat(n - s.length); }
function padL_(s, n) { s = String(s); return s.length >= n ? s : ' '.repeat(n - s.length) + s; }

// ============================================================
// Weekly helpers
// ============================================================

/** Average weekly KPI total across all complete ISO weeks before weekStart. */
function cumulativePriorWeeksAvg_(sessions, weekStart, kpi) {
  const perWeek = {};
  sessions.forEach(function(r) {
    if (formatDate_(r.day) >= weekStart) return;
    const w = isoWeekStart_(r.day);
    if (!perWeek[w]) perWeek[w] = [];
    perWeek[w].push(r);
  });
  const weeks = Object.keys(perWeek);
  if (!weeks.length) return 0;
  const weekTotals = weeks.map(function(w) { return totalForKpi_(perWeek[w], kpi); });
  return weekTotals.reduce(function(a, x) { return a + x; }, 0) / weekTotals.length;
}

/** ISO week Monday as 'yyyy-MM-dd' for any day value (Date or string). */
function isoWeekStart_(dayVal) {
  const p = formatDate_(dayVal).split('-');
  const d = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
  const day = d.getDay();
  d.setDate(d.getDate() + (day === 0 ? -6 : 1 - day));
  return d.getFullYear() + '-' + pad2_(d.getMonth() + 1) + '-' + pad2_(d.getDate());
}

/** True when today is Monday in the configured timezone. */
function isMonday_() {
  return Utilities.formatDate(new Date(), CONFIG.TIMEZONE, 'EEEE') === 'Monday';
}

/**
 * Mon–Sun range of the ISO week that just ended.
 * Call only when isMonday_() is true — yesterday is the completed Sunday.
 */
function priorWeekRange_(yesterday) {
  const p = yesterday.split('-');
  const sun = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
  const mon = new Date(sun.getTime());
  mon.setDate(sun.getDate() - 6);
  return {
    start: mon.getFullYear() + '-' + pad2_(mon.getMonth() + 1) + '-' + pad2_(mon.getDate()),
    end: yesterday,
  };
}

/** "Jun 9–15" or "Jun 30 – Jul 6" style label for a week range. */
function weekDateLabel_(start, end) {
  const M = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const sp = start.split('-'), ep = end.split('-');
  const s = new Date(Number(sp[0]), Number(sp[1]) - 1, Number(sp[2]));
  const e = new Date(Number(ep[0]), Number(ep[1]) - 1, Number(ep[2]));
  return s.getMonth() === e.getMonth()
    ? M[s.getMonth()] + ' ' + s.getDate() + '–' + e.getDate()
    : M[s.getMonth()] + ' ' + s.getDate() + ' – ' + M[e.getMonth()] + ' ' + e.getDate();
}

function pad2_(n) { return n < 10 ? '0' + n : '' + n; }

function todayInTz_(tz) {
  return Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd');
}

/**
 * Report window — 12:00 yesterday → 12:00 today (Taipei). Matches the
 * 12pm BQ refresh cadence so each daily report covers exactly one cycle.
 */
function reportWindow_(tz) {
  const todayStr = Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd');
  const end = new Date(todayStr + 'T12:00:00+08:00');
  const start = new Date(end.getTime() - 24 * 3600 * 1000);
  return { start: start, end: end, todayStr: todayStr };
}

/** Date label for a 12pm-12pm window, e.g. "Jun 10 12:00 → Jun 11 12:00". */
function windowLabel_(win, tz) {
  const fmt = function(d) { return Utilities.formatDate(d, tz, 'MMM d HH:mm'); };
  return fmt(win.start) + ' → ' + fmt(win.end);
}

/** True if the session's start_ts falls inside the given window. */
function inWindow_(row, win) {
  const ts = row.start_ts instanceof Date ? row.start_ts : new Date(row.start_ts);
  if (isNaN(ts)) return false;
  return ts >= win.start && ts < win.end;
}

/** List of last N day-strings ending at yesterday (inclusive). */
function lastNDays_(n) {
  const out = [];
  const now = new Date();
  for (let i = 1; i <= n; i++) {
    const d = new Date(now.getTime());
    d.setDate(now.getDate() - i);
    out.push(Utilities.formatDate(d, CONFIG.TIMEZONE, 'yyyy-MM-dd'));
  }
  return out;
}

// ============================================================
// Snapshot + data-revision drift alerts
// ============================================================

/**
 * Append a snapshot row per (stream_id, site) for each day in the last
 * SNAPSHOT_LOOKBACK_DAYS window. Tomorrow's run reads these to detect
 * whether the daily refresh silently revised the numbers.
 */
function snapshotMetrics_(ss, sessions) {
  const dates = lastNDays_(CONFIG.SNAPSHOT_LOOKBACK_DAYS);
  const dateSet = {};
  dates.forEach(function(d) { dateSet[d] = true; });
  const rows = sessions.filter(function(r) { return dateSet[formatDate_(r.day)]; });
  if (!rows.length) return;

  let tab = ss.getSheetByName(CONFIG.SNAPSHOT_TAB);
  if (!tab) tab = ss.insertSheet(CONFIG.SNAPSHOT_TAB);
  if (tab.getLastRow() === 0) {
    const header = ['report_date', 'data_date', 'streamer_id', 'streamer', 'stream_id', 'site'];
    CONFIG.DISPLAY_METRICS.forEach(function(kpi) { header.push(kpi.col); });
    tab.appendRow(header);
  }
  const today = todayInTz_(CONFIG.TIMEZONE);
  const out = rows.map(function(s) {
    const row = [today, formatDate_(s.day), s.streamer_id || '', s.streamer || '',
                 s.stream_id || '', s.site || ''];
    CONFIG.DISPLAY_METRICS.forEach(function(kpi) {
      const v = valueForRow_(s, kpi);
      row.push(isNaN(v) ? '' : v);
    });
    return row;
  });
  tab.getRange(tab.getLastRow() + 1, 1, out.length, out[0].length).setValues(out);
}

/** Drop snapshot rows older than SNAPSHOT_RETENTION_DAYS to keep the tab compact. */
function pruneSnapshot_(ss) {
  const tab = ss.getSheetByName(CONFIG.SNAPSHOT_TAB);
  if (!tab || tab.getLastRow() < 2) return;
  const values = tab.getDataRange().getValues();
  const header = values[0];
  const idx = header.indexOf('report_date');
  if (idx < 0) return;
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - CONFIG.SNAPSHOT_RETENTION_DAYS);
  const keep = [header];
  for (let i = 1; i < values.length; i++) {
    const rd = new Date(values[i][idx]);
    if (!isNaN(rd) && rd >= cutoff) keep.push(values[i]);
  }
  if (keep.length === values.length) return;
  tab.clear();
  tab.getRange(1, 1, keep.length, keep[0].length).setValues(keep);
}

/**
 * Compare current sheet values against the most recent prior snapshot for
 * the same (stream_id, site, data_date). Flag any KPI whose value moved
 * ≥ DRIFT_THRESHOLD between snapshots — that's a silent data revision.
 */
function evaluateDrift_(ss, sessions) {
  const tab = ss.getSheetByName(CONFIG.SNAPSHOT_TAB);
  if (!tab || tab.getLastRow() < 2) return [];
  const values = tab.getDataRange().getValues();
  const header = values[0];
  const idx = {};
  header.forEach(function(h, i) { idx[h] = i; });

  const today = todayInTz_(CONFIG.TIMEZONE);
  // Group prior snapshots by (stream_id, site, data_date) — keep most recent BEFORE today.
  const priorByKey = {};
  for (let i = 1; i < values.length; i++) {
    const row = values[i];
    const reportDate = formatDate_(row[idx['report_date']]);
    if (reportDate >= today) continue;
    const key = row[idx['stream_id']] + '|' + (row[idx['site']] || '') + '|' + formatDate_(row[idx['data_date']]);
    const existing = priorByKey[key];
    if (!existing || formatDate_(row[idx['report_date']]) > formatDate_(existing[idx['report_date']])) {
      priorByKey[key] = row;
    }
  }
  if (!Object.keys(priorByKey).length) return [];

  const alerts = [];
  sessions.forEach(function(cur) {
    const day = formatDate_(cur.day);
    const key = (cur.stream_id || '') + '|' + (cur.site || '') + '|' + day;
    const prior = priorByKey[key];
    if (!prior) return;
    CONFIG.DISPLAY_METRICS.forEach(function(kpi) {
      const before = Number(prior[idx[kpi.col]]);
      const after  = valueForRow_(cur, kpi);
      if (!before || isNaN(before) || isNaN(after)) return;
      const delta = (after - before) / before;
      if (Math.abs(delta) < CONFIG.DRIFT_THRESHOLD) return;
      alerts.push({
        data_date: day,
        stream_id: cur.stream_id,
        streamer: cur.streamer,
        site: cur.site,
        kpi: kpi.label,
        fmt: kpi.fmt,
        before: before,
        after: after,
        delta: delta,
      });
    });
  });
  return alerts;
}

function postDriftAlerts_(alerts) {
  if (!alerts.length) return;
  // Group by data_date for readability.
  const byDate = {};
  alerts.forEach(function(a) {
    if (!byDate[a.data_date]) byDate[a.data_date] = [];
    byDate[a.data_date].push(a);
  });
  const dates = Object.keys(byDate).sort();
  const blocks = dates.map(function(d) {
    const lines = byDate[d].slice(0, 20).map(function(a) {
      const dir = a.delta >= 0 ? '🟢 ▲' : '🔴 ▼';
      const pct = (Math.abs(a.delta) * 100).toFixed(0) + '%';
      return '• ' + (a.streamer || '?') + ' / ' + (a.site || '?') +
             ' / *' + a.kpi + '*: ' +
             formatVal_(a.before, a.fmt) + ' → ' + formatVal_(a.after, a.fmt) +
             ' (' + dir + ' ' + pct + ')';
    });
    if (byDate[d].length > 20) lines.push('_…and ' + (byDate[d].length - 20) + ' more_');
    return '*' + d + '*\n' + lines.join('\n');
  });
  const threshPct = (CONFIG.DRIFT_THRESHOLD * 100).toFixed(0);
  postSlack_('*🔄 Data-revision alert*\n' +
    alerts.length + ' KPI value(s) shifted ≥ ' + threshPct +
    '% since the previous snapshot:\n\n' + blocks.join('\n\n'));
}
