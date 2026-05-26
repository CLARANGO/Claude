import pandas as pd
from dash import dash_table, html
import dash_bootstrap_components as dbc

# Mirrors THRESHOLDS in alerts.gs
THRESHOLDS = {
    'follow_streamer_bet_count':       {'warn': -0.30, 'crit': -0.50, 'label': 'Follow Streamer Bet Count (NS)',    'fmt': 'int'},
    'bdw_turnover_rm':                 {'warn': -0.30, 'crit': -0.50, 'label': 'BDW Turnover RM (NS)',              'fmt': 'rm'},
    'donation_amount_usd':             {'warn': -0.40, 'crit': -0.60, 'label': 'Donation Amount USD (NS)',          'fmt': 'usd'},
    'follow_streamer_bet_turnover_rm': {'warn': -0.30, 'crit': -0.50, 'label': 'Follow Streamer Bet Turnover (L1)', 'fmt': 'rm'},
    'donation_user_count':             {'warn': -0.30, 'crit': -0.50, 'label': 'Donation User Count (L1)',          'fmt': 'int'},
    'bdw_bet_count':                   {'warn': -0.30, 'crit': -0.50, 'label': 'BDW Bet Count (L2)',                'fmt': 'int'},
}

BASELINE_COLS = {
    'follow_streamer_bet_count':       'follow_streamer_bet_count_cumavg_prior',
    'bdw_turnover_rm':                 'bdw_turnover_rm_cumavg_prior',
    'donation_amount_usd':             'donation_amount_usd_cumavg_prior',
    'follow_streamer_bet_turnover_rm': 'follow_streamer_bet_turnover_rm_cumavg_prior',
    'donation_user_count':             'donation_user_count_cumavg_prior',
    'bdw_bet_count':                   'bdw_bet_count_cumavg_prior',
}


def _fmt(v, fmt):
    if pd.isna(v):
        return 'n/a'
    if fmt == 'rm':
        return f'RM {v:,.0f}'
    if fmt == 'usd':
        return f'USD {v:,.0f}'
    return f'{v:,.0f}'


def _build_alerts(df_weekly):
    rows = []
    if df_weekly is None or df_weekly.empty:
        return rows

    for _, row in df_weekly.iterrows():
        for col, cfg in THRESHOLDS.items():
            if col not in df_weekly.columns:
                continue
            val = row.get(col)
            baseline_col = BASELINE_COLS.get(col)
            baseline = row.get(baseline_col) if baseline_col else None
            if pd.isna(val) or pd.isna(baseline) or baseline == 0:
                continue
            delta = (val - baseline) / abs(baseline)
            if delta > cfg['warn']:
                continue
            severity = 'CRIT' if delta <= cfg['crit'] else 'WARN'
            streamer = row.get('streamer_name', row.get('streamer_id', ''))
            week = str(row.get('iso_week_start', ''))
            rows.append({
                'week': week,
                'streamer': streamer,
                'metric': cfg['label'],
                'value': _fmt(val, cfg['fmt']),
                'baseline': _fmt(baseline, cfg['fmt']),
                'delta_pct': f'{delta:+.1%}',
                'severity': severity,
            })
    return rows


def render(df_weekly):
    rows = _build_alerts(df_weekly)

    if not rows:
        return html.Div('No threshold breaches in current data.', className='text-muted p-3')

    df = pd.DataFrame(rows)
    cols = ['week', 'streamer', 'metric', 'value', 'baseline', 'delta_pct', 'severity']
    cols_def = [{'name': c, 'id': c} for c in cols]

    return html.Div([
        html.H6(f'{len(rows)} threshold breach(es)', className='mb-2 text-danger'),
        dash_table.DataTable(
            data=df[cols].to_dict('records'),
            columns=cols_def,
            sort_action='native',
            page_size=50,
            style_table={'overflowX': 'auto'},
            style_cell={'fontSize': '12px', 'padding': '4px 8px'},
            style_header={'fontWeight': 'bold', 'backgroundColor': '#f8f9fa'},
            style_data_conditional=[
                {
                    'if': {'filter_query': '{severity} = "CRIT"'},
                    'backgroundColor': '#ffe0e0',
                    'color': '#a00000',
                },
                {
                    'if': {'filter_query': '{severity} = "WARN"'},
                    'backgroundColor': '#fff3cd',
                    'color': '#856404',
                },
            ],
        ),
    ])
