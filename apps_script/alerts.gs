/**
 * Streamer Performance Dashboard — Alert Layer
 *
 * Runs daily after the BQ → Sheets refresh completes (set a time-driven trigger).
 * Reads agg_session_metrics + agg_streamer_weekly tabs, evaluates threshold +
 * anomaly rules, posts to Slack, and writes the Alert Log tab with severity
 * formatting.
 *
 * Setup:
 *   1. Open the Sheet connected to BQ.
 *   2. Extensions → Apps Script → paste this file.
 *   3. Project Settings → Script Properties → add:
 *        SLACK_WEBHOOK_URL  = https://hooks.slack.com/services/...
 *        SLACK_HIGH_MENTION = @ops-team   (or <!subteam^XXX> for a user-group ping)
 *   4. Triggers → add daily trigger on `runAlertCheck` at 07:00 Taipei.
 */

const CONFIG = {
  SESSION_TAB: 'agg_session_metrics',
  WEEKLY_TAB: 'agg_streamer_weekly',
  LOG_TAB: 'Alert Log',
  MIN_VIEWERS: 100,
  MIN_BETS: 10,
  THRESHOLDS: {
    follow_streamer_bet_count: -0.30,
    donation_amount_total:     -0.40,
    watch_seconds_total:       -0.25,
  },
  WEEKLY_STREAM_COUNT_DROP: -0.20,
  PLATFORM_SHARE_DROP_PP:   -0.05,
  ANOMALY_MAD_MULTIPLIER:   2,
};

function runAlertCheck() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const alerts = [];

  alerts.push(...evaluateSessionAlerts_(ss));
  alerts.push(...evaluateWeeklyAlerts_(ss));

  writeAlertLog_(ss, alerts);
  postToSlack_(alerts);
  applyConditionalFormatting_(ss);
}

function evaluateSessionAlerts_(ss) {
  const rows = readTab_(ss, CONFIG.SESSION_TAB);
  if (!rows.length) return [];

  // Group by streamer_id, sorted by session_date ASC
  const byStreamer = {};
  rows.forEach(function(r) {
    if (!byStreamer[r.streamer_id]) byStreamer[r.streamer_id] = [];
    byStreamer[r.streamer_id].push(r);
  });
  Object.values(byStreamer).forEach(arr =>
    arr.sort((a, b) => new Date(a.session_date) - new Date(b.session_date))
  );

  const alerts = [];
  const today = new Date();
  const todayStr = Utilities.formatDate(today, 'Asia/Taipei', 'yyyy-MM-dd');

  Object.entries(byStreamer).forEach(([streamerId, sessions]) => {
    // Find the most recent session — alert only on yesterday/today
    const latest = sessions[sessions.length - 1];
    if (!latest) return;
    if (Number(latest.viewers) < CONFIG.MIN_VIEWERS) return;

    // Build rolling-5 prior window (excluding latest)
    const priors = sessions.slice(-6, -1);
    if (priors.length < 3) return; // sample-size guard

    Object.entries(CONFIG.THRESHOLDS).forEach(([metric, threshold]) => {
      const priorVals = priors.map(p => Number(p[metric])).filter(v => !isNaN(v));
      const med = median_(priorVals);
      const current = Number(latest[metric]);
      if (!med) return;

      const delta = (current - med) / med;
      if (delta <= threshold) {
        alerts.push({
          date: todayStr,
          type: 'threshold',
          severity: Math.abs(delta) >= Math.abs(threshold) * 1.5 ? 'high' : 'medium',
          streamer_id: streamerId,
          match_id: latest.match_id,
          metric,
          value: current,
          expected: med,
          delta_pct: (delta * 100).toFixed(1) + '%',
          note: `Drop ${(delta * 100).toFixed(1)}% vs rolling-5 median`,
        });
      }

      // Anomaly: outside median ± 2 * MAD
      const mad = medianAbsoluteDeviation_(priorVals);
      if (mad > 0 && Math.abs(current - med) > CONFIG.ANOMALY_MAD_MULTIPLIER * mad) {
        alerts.push({
          date: todayStr,
          type: 'anomaly',
          severity: 'medium',
          streamer_id: streamerId,
          match_id: latest.match_id,
          metric,
          value: current,
          expected: med,
          delta_pct: ((current - med) / med * 100).toFixed(1) + '%',
          note: `Outside ±${CONFIG.ANOMALY_MAD_MULTIPLIER}·MAD of rolling-5 median`,
        });
      }
    });
  });

  return alerts;
}

function evaluateWeeklyAlerts_(ss) {
  const rows = readTab_(ss, CONFIG.WEEKLY_TAB);
  if (!rows.length) return [];

  const alerts = [];
  const todayStr = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd');

  rows.forEach(r => {
    const current = Number(r.stream_count);
    // The weekly agg already exposes a 4-week rolling median for some metrics;
    // for stream_count we compare to the *_prev column (WoW) — extend if a med4 col is added
    const prev = Number(r.stream_count_prev || 0);
    if (!prev) return;
    const delta = (current - prev) / prev;
    if (delta <= CONFIG.WEEKLY_STREAM_COUNT_DROP) {
      alerts.push({
        date: todayStr,
        type: 'threshold',
        severity: 'medium',
        streamer_id: r.streamer_id,
        match_id: '',
        metric: 'weekly_stream_count',
        value: current,
        expected: prev,
        delta_pct: (delta * 100).toFixed(1) + '%',
        note: `Weekly stream count dropped ${(delta * 100).toFixed(1)}% WoW`,
      });
    }
  });

  return alerts;
}

function writeAlertLog_(ss, alerts) {
  let tab = ss.getSheetByName(CONFIG.LOG_TAB);
  if (!tab) tab = ss.insertSheet(CONFIG.LOG_TAB);

  if (tab.getLastRow() === 0) {
    tab.appendRow(['date', 'type', 'severity', 'streamer_id', 'match_id',
                   'metric', 'value', 'expected', 'delta_pct', 'note', 'acknowledged']);
  }

  alerts.forEach(a => {
    tab.appendRow([a.date, a.type, a.severity, a.streamer_id, a.match_id,
                   a.metric, a.value, a.expected, a.delta_pct, a.note, false]);
  });
}

function postToSlack_(alerts) {
  if (!alerts.length) return;
  const props = PropertiesService.getScriptProperties();
  const url = props.getProperty('SLACK_WEBHOOK_URL');
  if (!url) return;
  const mention = props.getProperty('SLACK_HIGH_MENTION') || '';

  const high = alerts.filter(a => a.severity === 'high');
  const med  = alerts.filter(a => a.severity === 'medium');

  const lines = [];
  if (high.length) {
    lines.push(`*${mention} ${high.length} high-severity alert(s):*`);
    high.forEach(a => lines.push(`• ${a.streamer_id} — ${a.metric}: ${a.note} (now ${a.value}, expected ${a.expected})`));
  }
  if (med.length) {
    lines.push(`*${med.length} medium-severity alert(s):*`);
    med.slice(0, 10).forEach(a => lines.push(`• ${a.streamer_id} — ${a.metric}: ${a.note}`));
    if (med.length > 10) lines.push(`…and ${med.length - 10} more — see Alert Log tab`);
  }

  UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({ text: lines.join('\n') }),
  });
}

function applyConditionalFormatting_(ss) {
  const tab = ss.getSheetByName(CONFIG.LOG_TAB);
  if (!tab) return;
  const range = tab.getRange(2, 3, Math.max(tab.getLastRow() - 1, 1), 1); // severity column
  const rules = [
    SpreadsheetApp.newConditionalFormatRule()
      .whenTextEqualTo('high').setBackground('#f4cccc').setRanges([range]).build(),
    SpreadsheetApp.newConditionalFormatRule()
      .whenTextEqualTo('medium').setBackground('#fff2cc').setRanges([range]).build(),
  ];
  tab.setConditionalFormatRules(rules);
}

// ---------- helpers ----------

function readTab_(ss, name) {
  const tab = ss.getSheetByName(name);
  if (!tab || tab.getLastRow() < 2) return [];
  const values = tab.getDataRange().getValues();
  const header = values[0];
  return values.slice(1).map(row => {
    const obj = {};
    header.forEach((h, i) => obj[h] = row[i]);
    return obj;
  });
}

function median_(arr) {
  if (!arr.length) return 0;
  const s = arr.slice().sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function medianAbsoluteDeviation_(arr) {
  const med = median_(arr);
  return median_(arr.map(v => Math.abs(v - med)));
}
