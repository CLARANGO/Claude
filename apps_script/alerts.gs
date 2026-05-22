/**
 * Streamer Performance Dashboard — Alert + Daily Digest Layer
 *
 * Two surfaces in Slack:
 *   1. Daily digest at 08:00 Taipei (always sent) — KPI snapshot, top
 *      streamers, top matches, alert count summary.
 *   2. Threshold + anomaly alerts (only when triggered) — threaded
 *      under the digest message for that day; high-severity gets its
 *      own channel ping.
 *
 * Setup:
 *   1. Open the Sheet connected to BQ.
 *   2. Extensions → Apps Script → paste this file.
 *   3. Project Settings → Script Properties → add:
 *        SLACK_WEBHOOK_URL  = https://hooks.slack.com/services/...
 *        SLACK_HIGH_MENTION = <!channel>   (or <!subteam^TEAMID>, or blank)
 *        REPORT_CURRENCY    = RM           (label shown in digest)
 *   4. Triggers (clock icon) → add daily trigger on `runDaily` at 08:00 Taipei.
 */

const CONFIG = {
  SESSION_TAB: 'agg_session_metrics',
  WEEKLY_TAB:  'agg_streamer_weekly',
  MATCH_COMPARE_TAB: 'agg_match_platform_compare',
  LOG_TAB: 'Alert Log',
  TIMEZONE: 'Asia/Taipei',

  // The 6 KPIs we alert on. Order = how they appear in the digest.
  KPIS: [
    { col: 'follow_streamer_bet_count',     label: 'Follow Streamer Bet Count',    fmt: 'int' },
    { col: 'follow_streamer_bet_turnover',  label: 'Follow Streamer Bet Turnover', fmt: 'money' },
    { col: 'bet_during_watch_count',        label: 'Bet During Watch Count',       fmt: 'int' },
    { col: 'bet_during_watch_turnover',     label: 'Bet During Watch Turnover',    fmt: 'money' },
    { col: 'tip_amount',                    label: 'Tip Amount',                   fmt: 'money' },
    { col: 'tip_count',                     label: 'Tip Count',                    fmt: 'int' },
  ],

  // Min-volume gate (per session)
  MIN_VIEWERS: 100,
  MIN_BETS: 10,

  // Drop thresholds vs rolling-5 median; severity tiers
  THRESHOLDS: {
    follow_streamer_bet_count:    { medium: -0.30, high: -0.50 },
    follow_streamer_bet_turnover: { medium: -0.30, high: -0.50 },
    bet_during_watch_count:       { medium: -0.30, high: -0.50 },
    bet_during_watch_turnover:    { medium: -0.30, high: -0.50 },
    tip_amount:                   { medium: -0.40, high: -0.60 },
    tip_count:                    { medium: -0.40, high: -0.60 },
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

/** Daily trigger — runs once per day at 08:00 Taipei. */
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
}

/** Test helper — posts a sample digest with today's data, no thread. */
function testDigest() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const yesterday = yesterdayInTz_(CONFIG.TIMEZONE);
  const sessions = readTab_(ss, CONFIG.SESSION_TAB);
  const alerts = evaluateAlerts_(sessions, yesterday);
  const digest = buildDigest_(sessions, yesterday, alerts);
  postSlackBlocks_(digest);
}

/** Simple webhook ping. */
function testSlack() {
  postSlack_('✅ Streamer dashboard alerts — test from Apps Script');
}

// ============================================================
// Digest builder
// ============================================================

function buildDigest_(sessions, yesterday, alerts) {
  const yest = sessions.filter(function(r) {
    return formatDate_(r.session_date) === yesterday;
  });

  // KPI snapshot: yesterday's totals + delta vs streamer-weighted rolling-5 median
  const kpiLines = CONFIG.KPIS.map(function(kpi) {
    const total = sum_(yest, kpi.col);
    const baselineTotal = streamerWeightedRollingMedian_(sessions, yesterday, kpi.col);
    const delta = baselineTotal > 0 ? (total - baselineTotal) / baselineTotal : null;
    const arrow = delta == null ? '—' : (delta >= 0 ? '▲' : '▼');
    const deltaStr = delta == null ? 'n/a' : Math.abs(delta * 100).toFixed(0) + '%';
    return '  ' + padR_(kpi.label, 32) + padL_(formatVal_(total, kpi.fmt), 14) + '   ' + arrow + ' ' + deltaStr;
  });

  // Top 3 streamers by NS Follow Streamer Bet Count
  const byStreamer = groupAndSum_(yest, 'streamer_id', ['follow_streamer_bet_count', 'follow_streamer_bet_turnover', 'tip_amount']);
  byStreamer.sort(function(a, b) { return b.follow_streamer_bet_count - a.follow_streamer_bet_count; });
  const topStreamers = byStreamer.slice(0, 3).map(function(s, i) {
    const r = yest.find(function(x) { return x.streamer_id === s.streamer_id; }) || {};
    return '  ' + (i + 1) + '. ' + (r.streamer_name || s.streamer_id) +
           ' — ' + (s.follow_streamer_bet_count || 0) + ' follow bets, ' +
           formatVal_(s.tip_amount, 'money') + ' tips';
  });

  // Top 3 matches by NS Follow Streamer Bet Count
  const byMatch = groupAndSum_(yest, 'SabaMatchId', ['follow_streamer_bet_count', 'bet_during_watch_turnover']);
  byMatch.sort(function(a, b) { return b.follow_streamer_bet_count - a.follow_streamer_bet_count; });
  const topMatches = byMatch.slice(0, 3).map(function(m, i) {
    const r = yest.find(function(x) { return x.SabaMatchId === m.SabaMatchId; }) || {};
    const label = (r.HomeCnName && r.AwayCnName) ? (r.HomeCnName + ' vs ' + r.AwayCnName) : (m.SabaMatchId || 'n/a');
    return '  ' + (i + 1) + '. ' + label + ' — ' + (m.follow_streamer_bet_count || 0) + ' follow bets';
  });

  const alertSummary = summarizeAlerts_(alerts);

  // Build Slack message
  const text =
    '*📊 World Cup Dashboard — ' + yesterday + '*\n' +
    '_' + yest.length + ' streams · ' +
      distinct_(yest, 'streamer_id').length + ' streamers · ' +
      distinct_(yest, 'SabaMatchId').length + ' matches_\n' +
    '\n*KPI snapshot (yesterday vs rolling-5 median):*\n```\n' +
    kpiLines.join('\n') + '\n```\n' +
    '\n*🏆 Top streamers:*\n' + (topStreamers.join('\n') || '  _no data_') + '\n' +
    '\n*⚽ Top matches:*\n' + (topMatches.join('\n') || '  _no data_') + '\n' +
    '\n' + alertSummary;

  return text;
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
      return new Date(a.session_date) - new Date(b.session_date);
    });
  });

  Object.keys(byStreamer).forEach(function(streamerId) {
    const ses = byStreamer[streamerId];
    const yestSessions = ses.filter(function(r) { return formatDate_(r.session_date) === yesterday; });

    yestSessions.forEach(function(s) {
      // Min-volume gate
      if (Number(s.viewers) < CONFIG.MIN_VIEWERS) return;
      if (Number(s.total_bet_count || 0) < CONFIG.MIN_BETS) return;

      // Rolling-5 prior window (sessions strictly before this one)
      const priors = ses
        .filter(function(r) { return new Date(r.session_date) < new Date(s.session_date); })
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
              streamer_id: streamerId, streamer_name: s.streamer_name,
              match_id: s.SabaMatchId, match_label: matchLabel_(s),
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
            streamer_id: streamerId, streamer_name: s.streamer_name,
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
    const yestSessions = ses.filter(function(r) { return formatDate_(r.session_date) === yesterday; });
    if (yestSessions.length > 0) return;
    const recent = ses.filter(function(r) {
      const daysAgo = (new Date(yesterday) - new Date(r.session_date)) / 86400000;
      return daysAgo > 0 && daysAgo <= CONFIG.ABSENT_LOOKBACK_DAYS;
    });
    if (recent.length === 0) return;
    const last = ses[ses.length - 1];
    alerts.push(makeAlert_({
      date: yesterday, type: 'absent', severity: 'low',
      streamer_id: streamerId, streamer_name: last.streamer_name,
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
  if (s.HomeCnName && s.AwayCnName) return s.HomeCnName + ' vs ' + s.AwayCnName;
  return s.SabaMatchId || '';
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
    if (formatDate_(r.session_date) >= yesterday) return;
    if (!byStreamer[r.streamer_id]) byStreamer[r.streamer_id] = [];
    byStreamer[r.streamer_id].push(r);
  });
  let total = 0;
  Object.keys(byStreamer).forEach(function(k) {
    const arr = byStreamer[k]
      .sort(function(a, b) { return new Date(a.session_date) - new Date(b.session_date); })
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
  if (fmt === 'money') {
    const ccy = PropertiesService.getScriptProperties().getProperty('REPORT_CURRENCY') || '';
    return ccy + ' ' + Math.round(Number(v)).toLocaleString();
  }
  if (fmt === 'int') return Math.round(Number(v)).toLocaleString();
  return String(v);
}

function padR_(s, n) { s = String(s); return s.length >= n ? s : s + ' '.repeat(n - s.length); }
function padL_(s, n) { s = String(s); return s.length >= n ? s : ' '.repeat(n - s.length) + s; }
