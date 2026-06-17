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

  // NS metrics — get baselines in the daily block; drive the day-avg alert.
  NS_METRICS: [
    { col: 'follow_streamer_bet_count', label: 'Follow Bet Count',    fmt: 'int', agg: 'sum' },
    { col: 'bdw_turnover_rm',           label: 'BDW Turnover',        fmt: 'rm',  agg: 'sum' },
    { col: 'donation_amount_usd',       label: 'Donation Amount',     fmt: 'usd', agg: 'sum' },
  ],

  // Supporting metrics — shown without baselines in the daily block.
  SUPPORTING_METRICS: [
    { col: 'follow_streamer_bet_turnover_rm', label: 'Follow Bet Turnover',  fmt: 'rm',  agg: 'sum' },
    { col: 'follow_user_count',               label: 'Follow Bet User',      fmt: 'int', agg: 'sum' },
    { col: 'follow_bet_user_rate',            label: 'Follow Bet User Rate', fmt: 'pct', agg: 'rate',
      rateNum: 'follow_user_count', rateDen: 'bdw_user_count' },
    { col: 'donation_user_count',             label: 'Donation Users',       fmt: 'int', agg: 'sum' },
    { col: 'bdw_bet_count',                   label: 'BDW Count',            fmt: 'int', agg: 'sum' },
    { col: 'during_watch_user_rate',          label: 'BDW User Rate',        fmt: 'pct', agg: 'rate',
      rateNum: 'bdw_user_count', rateDen: 'viewers' },
    { col: 'viewers',                         label: 'Viewers',              fmt: 'int', agg: 'sum' },
    { col: 'watch_over_10min_user',           label: 'Viewers >10min',       fmt: 'int', agg: 'sum' },
    { col: 'pcu',                             label: 'PCU',                  fmt: 'int', agg: 'max' },
  ],

  // DISPLAY_METRICS = NS + Supporting; set after the literal (see below).
  DISPLAY_METRICS: [],

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

  // Daily NS spike alert: fire when any NS metric on a (match × streamer × site)
  // row is more than SPIKE_MULTIPLIER × the day's avg of that metric.
  SPIKE_MULTIPLIER: 3,

  // Pack this many match attachments into a single Slack message so 60+
  // matches don't generate 60+ notifications.
  MATCH_CHUNK: 10,

  // Block Kit color sidebars
  COLOR_OK:     '#36a64f',
  COLOR_WARN:   '#f2c744',
  COLOR_ALERT:  '#dc3545',
};
CONFIG.DISPLAY_METRICS = CONFIG.NS_METRICS.concat(CONFIG.SUPPORTING_METRICS);

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

  const alerts = evaluateAlerts_(sessions, win);
  const digest = buildDigest_(sessions, win, alerts);

  // Post the digest as separate Slack messages (header + per-match chunks),
  // then post the alert detail as its own message.
  postSlackMessages_(digest);
  if (alerts.length) {
    postAlertThread_(alerts, null);
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
    postSlackMessages_(weekReport);
  }

}

/** Test helper — posts a sample daily digest, no thread. */
function testDigest() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const yesterday = yesterdayInTz_(CONFIG.TIMEZONE);
  const win = reportWindow_(CONFIG.TIMEZONE);
  const sessions = readTab_(ss, CONFIG.SESSION_TAB);
  const alerts = evaluateAlerts_(sessions, win);
  const digest = buildDigest_(sessions, win, alerts);
  postSlackMessages_(digest);
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
  postSlackMessages_(report);
}

/** Simple webhook ping. */
function testSlack() {
  postSlack_('✅ Streamer dashboard alerts — test from Apps Script');
}

// ============================================================
// Digest builder
// ============================================================

/**
 * Build the daily digest as an array of Slack Block Kit payloads:
 *   1. Header message (title + window + alert summary + top 3)
 *   2. One message per match (each match = an attachment with color sidebar)
 *
 * Each match's color reflects whether it triggered any spike alert.
 */
function buildDigest_(sessions, win, alerts) {
  const yest = dedupedWindowRows_(sessions, win).sort(function(a, b) {
    return (Number(b.follow_streamer_bet_count) || 0) - (Number(a.follow_streamer_bet_count) || 0);
  });

  // Build a lookup of alerts per (stream_id|anchor_id|stream_site_id) so the
  // match block can color itself + render alert chips.
  const alertsByRow = {};
  alerts.forEach(function(a) {
    const k = (a.match_id || '') + '|' + (a.streamer_id || '') + '|' + (a.site || '');
    if (!alertsByRow[k]) alertsByRow[k] = [];
    alertsByRow[k].push(a);
  });

  const headerPayload = buildHeaderMessage_(yest, win, alerts);
  if (!yest.length) return [headerPayload];

  // Build one attachment per match, then group into messages of MATCH_CHUNK
  // matches each to avoid flooding Slack (60+ matches × 1 msg each is noisy).
  const attachments = yest.map(function(s) {
    const k = (s.stream_id || '') + '|' +
              (s.anchor_id || s.streamer_id || '') + '|' +
              (s.stream_site || s.site || '');
    return buildMatchAttachment_(sessions, s, alertsByRow[k] || []);
  });

  const chunkSize = CONFIG.MATCH_CHUNK || 10;
  const matchPayloads = [];
  for (let i = 0; i < attachments.length; i += chunkSize) {
    matchPayloads.push({ attachments: attachments.slice(i, i + chunkSize) });
  }

  return [headerPayload].concat(matchPayloads);
}

function buildHeaderMessage_(yest, win, alerts) {
  const matchCount = distinct_(yest, 'stream_id').length;
  const streamerCount = distinct_(yest, 'streamer_id').length;

  const blocks = [];
  blocks.push(bkHeader_('📊 World Cup Dashboard'));
  blocks.push(bkContext_(
    '*' + windowLabel_(win, CONFIG.TIMEZONE) + '* · ' +
    '`' + matchCount + '` matches · `' + streamerCount + '` streamers · `' + yest.length + '` rows'
  ));

  // Alert summary attachment (color + line)
  const high = alerts.filter(function(a) { return a.severity === 'high'; }).length;
  const med  = alerts.filter(function(a) { return a.severity === 'medium'; }).length;
  let alertColor, alertText;
  if (!alerts.length) {
    alertColor = CONFIG.COLOR_OK;
    alertText = '✅ *No NS spikes today*';
  } else {
    alertColor = high ? CONFIG.COLOR_ALERT : CONFIG.COLOR_WARN;
    const parts = [];
    if (high) parts.push('`' + high + '` high');
    if (med)  parts.push('`' + med + '` medium');
    alertText = '⚠️ *' + alerts.length + ' NS spike alert' + (alerts.length > 1 ? 's' : '') + '* (' + parts.join(' · ') + ')';
  }

  // Top 3 by Follow Bet Count
  const top = yest.slice(0, 3);
  const topBlocks = [];
  if (top.length) {
    topBlocks.push(bkHeader_('🏆 Top ' + top.length + ' matches'));
    top.forEach(function(s, i) {
      const streamer = s.streamer || s.anchor_id || '?';
      const site = s.stream_site || s.site || '';
      const matchId = s.stream_id || 'n/a';
      const stage = CONFIG.STAGE_LABELS[s.match_stage] || s.match_stage || '';
      const matchName = cleanStreamName_(s.stream_name || '');
      topBlocks.push(bkSection_(
        '*#' + (i + 1) + '  ' + streamer + '* · ' + site + '\n' +
        'Match `' + matchId + '`' + (stage ? ' · ' + stage : '') + '\n' +
        (matchName ? '_' + matchName + '_' : '')
      ));
      topBlocks.push(bkSectionFields_([
        bkField_('*Follow Bets*\n`' + formatVal_(s.follow_streamer_bet_count, 'int') + '`'),
        bkField_('*BDW Turnover*\n`' + formatVal_(s.bdw_turnover_rm, 'rm') + '`'),
        bkField_('*Donations*\n`' + formatVal_(s.donation_amount_usd, 'usd') + '`'),
        bkField_('*Viewers >10min*\n`' + formatVal_(s.watch_over_10min_user, 'int') + '`'),
      ]));
    });
  }

  return {
    blocks: blocks,
    attachments: [
      bkAttachment_(alertColor, [bkSection_(alertText)]),
    ].concat(topBlocks.length ? [bkAttachment_(CONFIG.COLOR_OK, topBlocks)] : []),
  };
}

/**
 * Build a single Slack Block Kit attachment for one match. Multiple
 * attachments are then packed into one Slack message (see CONFIG.MATCH_CHUNK).
 * Color sidebar: green = no alert, yellow = medium, red = high.
 */
function buildMatchAttachment_(sessions, s, alertsForMatch) {
  const streamId = s.stream_id || 'n/a';
  const streamer = s.streamer || s.streamer_id || '?';
  const site = s.stream_site || s.site || 'unknown';
  const matchName = cleanStreamName_(s.stream_name || '');
  const stageLabel = CONFIG.STAGE_LABELS[s.match_stage] || s.match_stage || '';
  const titleLine = stageLabel + (matchName ? ' — ' + matchName : '');

  // Baseline pool: same streamer + same site.
  const samePool = sessions.filter(function(p) {
    return p.streamer_id === s.streamer_id && (p.stream_site || p.site || '') === site;
  });
  const last2 = samePool
    .filter(function(p) { return new Date(p.start_ts) < new Date(s.start_ts); })
    .sort(function(a, b) { return new Date(a.start_ts) - new Date(b.start_ts); })
    .slice(-2);
  const may = samePool.filter(function(p) { return extractMonth_(p.day) === 5; });

  // Color based on the highest severity alert on this match.
  let color = CONFIG.COLOR_OK;
  if (alertsForMatch.some(function(a) { return a.severity === 'high'; })) color = CONFIG.COLOR_ALERT;
  else if (alertsForMatch.length) color = CONFIG.COLOR_WARN;

  const blocks = [];
  blocks.push(bkSection_(
    '*⚽ Match `' + streamId + '`*\n' +
    '*' + streamer + '* · ' + site +
    (titleLine ? '\n_' + titleLine + '_' : '')
  ));

  // Spike alert chips (if any)
  if (alertsForMatch.length) {
    const chips = alertsForMatch.map(function(a) {
      return '`' + a.metric + ' ' + a.ratio.toFixed(1) + '×`';
    });
    blocks.push(bkContext_('🚨 *Spikes*: ' + chips.join(' · ')));
  }

  blocks.push(bkDivider_());
  blocks.push(bkContext_('🎯 *NS Metrics*'));
  blocks.push(bkSectionFields_(CONFIG.NS_METRICS.map(function(kpi) {
    const v = valueForRow_(s, kpi);
    const valStr = isNaN(v) ? 'n/a' : formatVal_(v, kpi.fmt);
    const dL2  = arrowDelta_(v, avgForKpi_(last2, kpi));
    const dMay = arrowDelta_(v, avgForKpi_(may, kpi));
    return bkField_(
      '*' + kpi.label + '*\n' +
      '`' + valStr + '`\n' +
      '_L2 ' + dL2 + ' · May ' + dMay + '_'
    );
  })));

  blocks.push(bkDivider_());
  blocks.push(bkContext_('📊 *Supporting Metrics*'));
  // Slack section fields max = 10; we have ≤9 supporting metrics. Fits.
  blocks.push(bkSectionFields_(CONFIG.SUPPORTING_METRICS.map(function(kpi) {
    const v = valueForRow_(s, kpi);
    const valStr = isNaN(v) ? 'n/a' : formatVal_(v, kpi.fmt);
    return bkField_('*' + kpi.label + '*\n`' + valStr + '`');
  })));

  return bkAttachment_(color, blocks);
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

  const overview =
    '*📅 Weekly Report — ' + weekDateLabel_(weekStart, weekEnd) + '*\n' +
    '_' + matchCount + ' matches · ' +
      distinct_(weekSessions, 'streamer_id').length + ' streamers_\n' +
    '\n*NS Weekly KPIs:*\n```\n' + kpiTable + '\n```';

  const langHeader = '*🌐 By Language:*';
  const langChunks = langBlocks.length
    ? chunkBlocks_(langBlocks)
    : ['  _no data_'];
  // Attach the header to the first language chunk so it appears once.
  const langMessages = langChunks.map(function(c, i) {
    return i === 0 ? langHeader + '\n' + c : c;
  });

  const tops =
    '*🏆 Top 5 streamers (Follow Bet Count):*\n' +
      (top5Streamers.length ? top5Streamers.join('\n') : '  _no data_') +
    '\n\n*⚽ Top 5 matches (Follow Bet Count):*\n' +
      (top5Matches.length ? top5Matches.join('\n') : '  _no data_');

  return [overview].concat(langMessages).concat([tops]);
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

// ============================================================
// Alert evaluation
// ============================================================

/**
 * Daily NS spike alert: for each NS metric, compute the avg across all
 * (match × streamer × site) rows in the window; fire on any row whose
 * value exceeds SPIKE_MULTIPLIER × that avg.
 */
function evaluateAlerts_(sessions, win) {
  const winRows = dedupedWindowRows_(sessions, win);
  if (!winRows.length) return [];

  const reportLabel = windowLabel_(win, CONFIG.TIMEZONE);
  const dayAvg = {};
  CONFIG.NS_METRICS.forEach(function(ns) {
    const vals = winRows.map(function(r) { return Number(r[ns.col]) || 0; });
    const sum = vals.reduce(function(a, x) { return a + x; }, 0);
    dayAvg[ns.col] = vals.length ? sum / vals.length : 0;
  });

  const alerts = [];
  winRows.forEach(function(r) {
    CONFIG.NS_METRICS.forEach(function(ns) {
      const v = Number(r[ns.col]) || 0;
      const avg = dayAvg[ns.col];
      if (avg <= 0 || v < CONFIG.SPIKE_MULTIPLIER * avg) return;
      const ratio = v / avg;
      alerts.push({
        date: reportLabel,
        type: 'spike',
        severity: 'medium',
        streamer_id: r.anchor_id || r.streamer_id,
        streamer_name: r.streamer,
        match_id: r.stream_id,
        site: r.stream_site || r.site || '',
        match_label: matchLabel_(r),
        metric: ns.label,
        metric_col: ns.col,
        fmt: ns.fmt,
        value: v,
        expected: avg,
        delta: ratio - 1,
        ratio: ratio,
        note: ratio.toFixed(1) + '× day avg (' + formatVal_(avg, ns.fmt) + ')',
      });
    });
  });

  return alerts;
}

/** Sessions in the window, deduped by (stream_id, anchor_id, stream_site_id). */
function dedupedWindowRows_(sessions, win) {
  const inWin = sessions.filter(function(r) { return inWindow_(r, win); });
  const seen = {};
  const out = [];
  inWin.forEach(function(r) {
    const key = (r.stream_id || '') + '|' +
                (r.anchor_id || r.streamer_id || '') + '|' +
                (r.stream_site_id == null ? '' : r.stream_site_id);
    if (seen[key]) return;
    seen[key] = true;
    out.push(r);
  });
  return out;
}

function makeAlert_(a) { return a; }

function matchLabel_(s) {
  return s.stream_id || s.SabaMatchId || '';
}

// ============================================================
// Slack I/O
// ============================================================

function postSlack_(payloadOrText) {
  const url = PropertiesService.getScriptProperties().getProperty('SLACK_WEBHOOK_URL');
  if (!url) throw new Error('SLACK_WEBHOOK_URL not set in Script Properties');
  const payload = typeof payloadOrText === 'string'
    ? { text: payloadOrText }
    : payloadOrText;
  return UrlFetchApp.fetch(url, {
    method: 'post', contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });
}

// ============================================================
// Block Kit helpers
// ============================================================

function bkHeader_(text) {
  return { type: 'header', text: { type: 'plain_text', text: text, emoji: true } };
}
function bkSection_(mrkdwn) {
  return { type: 'section', text: { type: 'mrkdwn', text: mrkdwn } };
}
function bkSectionFields_(fields) {
  // Slack allows up to 10 fields per section.
  return { type: 'section', fields: fields };
}
function bkContext_(mrkdwn) {
  return { type: 'context', elements: [{ type: 'mrkdwn', text: mrkdwn }] };
}
function bkDivider_() { return { type: 'divider' }; }
function bkField_(text) { return { type: 'mrkdwn', text: text }; }
function bkAttachment_(color, blocks) { return { color: color, blocks: blocks }; }

/** Post one or more Slack messages. Accepts string or array of strings. */
function postSlackMessages_(parts) {
  if (!parts) return;
  if (typeof parts === 'string') { postSlack_(parts); return; }
  parts.forEach(function(p) { if (p) postSlack_(p); });
}

/**
 * Pack a list of block strings into chunks ≤ maxChars total. Stops a chunk
 * before adding a block that would push it over. Prevents Slack's mrkdwn
 * parser from breaking mid-codeblock on long messages.
 */
function chunkBlocks_(blocks, maxChars) {
  maxChars = maxChars || 2800;
  const chunks = [];
  let cur = '';
  blocks.forEach(function(b) {
    if (!b) return;
    if (cur && (cur.length + b.length + 2) > maxChars) {
      chunks.push(cur);
      cur = '';
    }
    cur = cur ? cur + '\n\n' + b : b;
  });
  if (cur) chunks.push(cur);
  return chunks;
}

function postSlackBlocks_(text) {
  return postSlack_(text);
}

function postAlertThread_(alerts, _parentTs) {
  // Spike alert detail message — one bullet per (streamer × metric).
  const lines = alerts.slice(0, 30).map(function(a) {
    return '• ' + (a.streamer_name || a.streamer_id) +
           (a.site ? ' (' + a.site + ')' : '') +
           (a.match_id ? ' · Match `' + a.match_id + '`' : '') +
           ' — *' + a.metric + '*: ' + formatVal_(a.value, a.fmt) +
           '  _(day avg ' + formatVal_(a.expected, a.fmt) +
           ' · ' + (a.ratio != null ? a.ratio.toFixed(1) + '×' : '') + ')_';
  });
  if (alerts.length > 30) lines.push('_…and ' + (alerts.length - 30) + ' more in Alert Log_');
  postSlack_('*🚨 NS Spike Detail:*\n' + lines.join('\n'));
}

function postHighSeverityPing_(alerts) {
  const mention = PropertiesService.getScriptProperties().getProperty('SLACK_HIGH_MENTION') || '';
  const lines = alerts.map(function(a) {
    return '• ' + (a.streamer_name || a.streamer_id) +
           (a.site ? ' (' + a.site + ')' : '') +
           ' — *' + a.metric + '*: ' + formatVal_(a.value, a.fmt) +
           ' (' + (a.ratio != null ? a.ratio.toFixed(1) + '×' : '') + ' day avg)';
  });
  postSlack_(mention + ' *🚨 ' + alerts.length + ' high-severity spike(s):*\n' + lines.join('\n'));
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
