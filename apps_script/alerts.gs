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
  TIMEZONE: 'Asia/Taipei',

  // The 6 [ALERT] KPIs (per revised metrics tree). Order = how they appear in the digest.
  KPIS: [
    { col: 'follow_streamer_bet_count',        label: 'Follow Streamer Bet Count (NS)',    fmt: 'int' },
    { col: 'bdw_turnover_rm',                  label: 'Bet During Watch Turnover (NS)',    fmt: 'rm'  },
    { col: 'donation_amount_usd',              label: 'Donation Amount (NS)',              fmt: 'usd' },
    { col: 'follow_streamer_bet_turnover_rm',  label: 'Follow Streamer Bet Turnover (L1)', fmt: 'rm'  },
    { col: 'donation_user_count',              label: 'Donation User Count (L1)',          fmt: 'int' },
    { col: 'bdw_bet_count',                    label: 'Bet During Watch Count (L2)',       fmt: 'int' },
  ],

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
};

// ============================================================
// Entry points
// ============================================================

/** Daily trigger — runs once per day at ~15:00 Taipei (13:00 batch + 2h buffer). */
function runDaily() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const yesterday = yesterdayInTz_(CONFIG.TIMEZONE);

  const sessions = readTab_(ss, CONFIG.SESSION_TAB);
  if (!sessions.length) {
    postSlack_('No data found in `' + CONFIG.SESSION_TAB + '` tab — skipping today.');
    return;
  }

  const alerts = evaluateAlerts_(sessions, yesterday);
  const digest = buildDigest_(sessions, yesterday, alerts);

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
}

/** Test helper — posts a sample daily digest, no thread. */
function testDigest() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const yesterday = yesterdayInTz_(CONFIG.TIMEZONE);
  const sessions = readTab_(ss, CONFIG.SESSION_TAB);
  const alerts = evaluateAlerts_(sessions, yesterday);
  const digest = buildDigest_(sessions, yesterday, alerts);
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

function buildDigest_(sessions, yesterday, alerts) {
  // One stream session = one streamer covering one match.
  const yest = sessions.filter(function(r) {
    return formatDate_(r.day) === yesterday;
  });
  yest.sort(function(a, b) {
    return (Number(b.follow_streamer_bet_count) || 0) - (Number(a.follow_streamer_bet_count) || 0);
  });

  const matchBlocks = yest.map(function(s) { return buildMatchBlock_(sessions, s); });
  const alertSummary = summarizeAlerts_(alerts);

  return '*📊 World Cup Dashboard — ' + yesterday + '*\n' +
    '_' + yest.length + ' matches · ' +
      distinct_(yest, 'streamer_id').length + ' streamers_\n\n' +
    (matchBlocks.length ? matchBlocks.join('\n\n') : '_No matches yesterday._') +
    '\n\n' + alertSummary;
}

/** Per-match block: header + 2 comparison tables (last 2 matches, May avg). */
function buildMatchBlock_(sessions, s) {
  const streamId = s.stream_id || s.SabaMatchId || 'n/a';
  const streamer = s.streamer || s.streamer_id || '?';
  const matchName = cleanStreamName_(s.stream_name || '');
  const stage = s.match_stage || 'n/a';
  const pcu = (s.pcu != null && s.pcu !== '')
    ? Math.round(Number(s.pcu)).toLocaleString() : 'n/a';

  // Baseline 1 — streamer's own last 2 matches (any stage)
  const last2 = sessions
    .filter(function(p) {
      return p.streamer_id === s.streamer_id && new Date(p.day) < new Date(s.day);
    })
    .sort(function(a, b) { return new Date(a.day) - new Date(b.day); })
    .slice(-2);
  const last2Lines = comparisonLines_(s, last2);

  // Baseline 2 — streamer's average over all May 2026 sessions
  const may = sessions.filter(function(p) {
    return p.streamer_id === s.streamer_id && extractMonth_(p.day) === 5;
  });
  const mayLines = comparisonLines_(s, may);

  return '*⚽ Match ' + streamId + ' · ' + streamer + '*\n' +
    (matchName ? '_' + matchName + '_\n' : '') +
    '_Stage: ' + stage + ' · PCU: ' + pcu + '_\n' +
    '\n*vs Streamer\'s Last 2 Matches:*\n```\n' + last2Lines.join('\n') + '\n```\n' +
    '*vs Streamer\'s May Avg:*\n```\n' + mayLines.join('\n') + '\n```';
}

/** Render one KPI delta-table comparing `current` row against an array of prior sessions. */
function comparisonLines_(current, priors) {
  return CONFIG.KPIS.map(function(kpi) {
    const v = Number(current[kpi.col]);
    const vals = (priors || [])
      .map(function(r) { return Number(r[kpi.col]); })
      .filter(function(n) { return !isNaN(n); });
    const baseline = vals.length ? vals.reduce(function(a, x) { return a + x; }, 0) / vals.length : 0;
    const delta = (baseline > 0 && !isNaN(v)) ? (v - baseline) / baseline : null;
    const arrow = delta == null ? '—' : (delta >= 0 ? '▲' : '▼');
    const deltaStr = delta == null ? 'n/a' : Math.abs(delta * 100).toFixed(0) + '%';
    return '  ' + padR_(kpi.label, 32) + padL_(formatVal_(v, kpi.fmt), 14) + '   ' + arrow + ' ' + deltaStr;
  });
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
  const weekSessions = sessions.filter(function(r) {
    const d = formatDate_(r.day);
    return d >= weekStart && d <= weekEnd;
  });
  const matchCount = distinct_(weekSessions, 'stream_id').length;

  // 1) Weekly totals vs cumulative prior-weeks avg
  const totalLines = CONFIG.KPIS.map(function(kpi) {
    const total = sum_(weekSessions, kpi.col);
    const baseline = cumulativePriorWeeksAvg_(sessions, weekStart, kpi.col);
    return deltaLine_(kpi, total, baseline);
  });

  // 2) Per-match avg vs prior-weeks per-match avg
  const avgLines = CONFIG.KPIS.map(function(kpi) {
    const thisAvg = matchCount > 0 ? sum_(weekSessions, kpi.col) / matchCount : 0;
    const baseline = priorWeeksPerMatchAvg_(sessions, weekStart, kpi.col);
    return deltaLine_(kpi, thisAvg, baseline);
  });

  // 3) Per-language block (sum the 3 NS metrics)
  const byLang = groupAndSum_(weekSessions, 'language',
    ['follow_streamer_bet_count', 'bdw_turnover_rm', 'donation_amount_usd']);
  byLang.sort(function(a, b) { return b.follow_streamer_bet_count - a.follow_streamer_bet_count; });
  const langLines = byLang.map(function(l) {
    const lang = l.language || 'unknown';
    return '  ' + padR_(String(lang), 8) +
      ' — Follow: ' + (Math.round(l.follow_streamer_bet_count) || 0).toLocaleString() +
      ' · BDW: ' + formatVal_(l.bdw_turnover_rm, 'rm') +
      ' · Donations: ' + formatVal_(l.donation_amount_usd, 'usd');
  });

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
    '\n*NS Weekly Totals (vs cumulative prior-weeks avg):*\n```\n' +
    totalLines.join('\n') + '\n```\n' +
    '\n*NS Avg per Match (vs prior-weeks per-match avg):*\n```\n' +
    avgLines.join('\n') + '\n```\n' +
    '\n*🌐 By Language:*\n' +
    (langLines.length ? langLines.join('\n') : '  _no data_') + '\n' +
    '\n*🏆 Top 5 streamers (Follow Bet Count):*\n' +
      (top5Streamers.length ? top5Streamers.join('\n') : '  _no data_') + '\n' +
    '\n*⚽ Top 5 matches (Follow Bet Count):*\n' +
      (top5Matches.length ? top5Matches.join('\n') : '  _no data_') + '\n';
}

/** Shared formatter: value column + delta arrow + pct vs baseline. */
function deltaLine_(kpi, value, baseline) {
  const delta = baseline > 0 ? (value - baseline) / baseline : null;
  const arrow = delta == null ? '—' : (delta >= 0 ? '▲' : '▼');
  const deltaStr = delta == null ? 'n/a' : Math.abs(delta * 100).toFixed(0) + '%';
  return '  ' + padR_(kpi.label, 32) + padL_(formatVal_(value, kpi.fmt), 14) + '   ' + arrow + ' ' + deltaStr;
}

/** Average of (weekly metric / weekly match count) across all complete prior weeks. */
function priorWeeksPerMatchAvg_(sessions, weekStart, col) {
  const perWeek = {};
  sessions.forEach(function(r) {
    if (formatDate_(r.day) >= weekStart) return;
    const w = isoWeekStart_(r.day);
    if (!perWeek[w]) perWeek[w] = { total: 0, count: 0 };
    perWeek[w].total += (Number(r[col]) || 0);
    perWeek[w].count += 1;   // 1 stream row = 1 match
  });
  const weeks = Object.keys(perWeek);
  if (!weeks.length) return 0;
  const avgs = weeks.map(function(w) {
    return perWeek[w].count > 0 ? perWeek[w].total / perWeek[w].count : 0;
  });
  return avgs.reduce(function(a, x) { return a + x; }, 0) / avgs.length;
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

  // Streamer-absent alerts
  Object.keys(byStreamer).forEach(function(streamerId) {
    const ses = byStreamer[streamerId];
    const yestSessions = ses.filter(function(r) { return formatDate_(r.day) === yesterday; });
    if (yestSessions.length > 0) return;
    const recent = ses.filter(function(r) {
      const daysAgo = (new Date(yesterday) - new Date(r.day)) / 86400000;
      return daysAgo > 0 && daysAgo <= CONFIG.ABSENT_LOOKBACK_DAYS;
    });
    if (recent.length === 0) return;
    const last = ses[ses.length - 1];
    alerts.push(makeAlert_({
      date: yesterday, type: 'absent', severity: 'low',
      streamer_id: streamerId, streamer_name: last.streamer,
      match_id: '', match_label: '',
      metric: 'Streamer absent', metric_col: 'absent', fmt: 'int',
      value: 0, expected: '—', delta: null,
      note: 'Active in last ' + CONFIG.ABSENT_LOOKBACK_DAYS + 'd but no stream yesterday',
    }));
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
  if (v == null || v === '') return '—';
  if (fmt === 'rm')  return 'RM '  + Math.round(Number(v)).toLocaleString();
  if (fmt === 'usd') return 'USD ' + Math.round(Number(v)).toLocaleString();
  // Back-compat: 'money' falls through to USD.
  if (fmt === 'money') return 'USD ' + Math.round(Number(v)).toLocaleString();
  if (fmt === 'int') return Math.round(Number(v)).toLocaleString();
  return String(v);
}

function padR_(s, n) { s = String(s); return s.length >= n ? s : s + ' '.repeat(n - s.length); }
function padL_(s, n) { s = String(s); return s.length >= n ? s : ' '.repeat(n - s.length) + s; }

// ============================================================
// Weekly helpers
// ============================================================

/** Average weekly total of `col` across all complete ISO weeks before weekStart. */
function cumulativePriorWeeksAvg_(sessions, weekStart, col) {
  const weekTotals = {};
  sessions.forEach(function(r) {
    if (formatDate_(r.day) >= weekStart) return;
    const wStart = isoWeekStart_(r.day);
    if (!weekTotals[wStart]) weekTotals[wStart] = 0;
    weekTotals[wStart] += (Number(r[col]) || 0);
  });
  const weeks = Object.keys(weekTotals);
  if (!weeks.length) return 0;
  return weeks.reduce(function(s, w) { return s + weekTotals[w]; }, 0) / weeks.length;
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
