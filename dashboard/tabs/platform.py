import pandas as pd
from dash import dash_table, dcc, html
import dash_bootstrap_components as dbc
import plotly.express as px
import plotly.graph_objects as go


# Three comparison views.
# Each view is: filter on the per-match dataframe + a "their" turnover/count/user
# column triplet to compare our BDW against.
VIEWS = [
    {
        'key': 'match_streamer_site_streamer',
        'title': '1. Matches with streamer × Sites with streamer',
        'subtitle': 'Our BDW vs general bet metrics from sites that host the streamer function',
        'filter': lambda df: df[df['has_streamer'] == True],
        'their_turnover': 'streamer_site_total_turnover',
        'their_count':    'streamer_site_total_bet_count',
        'their_user':     'streamer_site_bet_user',
        'their_avg':      'streamer_site_avg_bet_size',
    },
    {
        'key': 'match_streamer_all_site',
        'title': '2. Matches with streamer × All sites',
        'subtitle': 'Our BDW vs all-site bet metrics, restricted to matches that have a streamer',
        'filter': lambda df: df[df['has_streamer'] == True],
        'their_turnover': 'overall_total_turnover',
        'their_count':    'overall_total_bet_count',
        'their_user':     'overall_bet_user',
        'their_avg':      'overall_avg_bet_size_per_ticket',
    },
    {
        'key': 'all_matches_all_site',
        'title': '3. All matches × All sites',
        'subtitle': 'Our BDW vs all-site bet metrics across every match in the period',
        'filter': lambda df: df,
        'their_turnover': 'overall_total_turnover',
        'their_count':    'overall_total_bet_count',
        'their_user':     'overall_bet_user',
        'their_avg':      'overall_avg_bet_size_per_ticket',
    },
]


def _card(label, value, fmt='rm'):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        display = 'n/a'
    elif fmt == 'rm':
        display = f'RM {value:,.0f}'
    elif fmt == 'usd':
        display = f'USD {value:,.0f}'
    elif fmt == 'pct':
        display = f'{value:.2%}'
    elif fmt == 'int':
        display = f'{value:,.0f}'
    else:
        display = str(value)
    return dbc.Col(
        dbc.Card(dbc.CardBody([
            html.P(label, className='text-muted mb-1', style={'fontSize': '0.75rem'}),
            html.H6(display, className='mb-0'),
        ]), className='text-center'),
        width=2,
    )


def _period_cards(sub, v):
    our_to = sub['our_bdw_turnover_rm'].sum()
    our_bc = sub['our_bdw_bet_count'].sum()
    their_to = sub[v['their_turnover']].sum()
    their_bc = sub[v['their_count']].sum()
    their_users = sub[v['their_user']].sum()
    sow = our_to / their_to if their_to else None
    sob = our_bc / their_bc if their_bc else None
    return dbc.Row([
        _card('Our BDW Turnover', our_to, 'rm'),
        _card('Platform Turnover', their_to, 'rm'),
        _card('Share of Wallet', sow, 'pct'),
        _card('Our BDW Bet Count', our_bc, 'int'),
        _card('Platform Bet Count', their_bc, 'int'),
        _card('Share of Bets', sob, 'pct'),
    ], className='g-2 mb-2')


_MATCH_COL_DEFS = [
    ('date',                  'Date',            None),
    ('match_id',              'Match ID',        None),
    ('match_name',            'Match',           None),
    ('has_streamer',          'Streamer?',       None),
    ('our_bdw_turnover_rm',   'Our BDW (RM)',    ',.0f'),
    ('our_bdw_bet_count',     'Our BDW Bets',    ',.0f'),
    ('their_turnover',        'Plat Turnover',   ',.0f'),
    ('their_count',           'Plat Bets',       ',.0f'),
    ('their_user',            'Plat Users',      ',.0f'),
    ('share_of_wallet',       'Share of Wallet', '.2%'),
    ('share_of_bets',         'Share of Bets',   '.2%'),
]


def _match_table(sub, v):
    df = sub.copy()
    df['their_turnover'] = df[v['their_turnover']]
    df['their_count']    = df[v['their_count']]
    df['their_user']     = df[v['their_user']]
    df['share_of_wallet'] = df['our_bdw_turnover_rm'] / df['their_turnover'].replace(0, pd.NA)
    df['share_of_bets']   = df['our_bdw_bet_count']   / df['their_count'].replace(0, pd.NA)
    df = df.sort_values(['date', 'our_bdw_turnover_rm'], ascending=[False, False])

    cols_def = []
    data_cols = []
    for col, label, spec in _MATCH_COL_DEFS:
        if col not in df.columns:
            continue
        data_cols.append(col)
        d = {'name': label, 'id': col}
        if spec:
            d['type'] = 'numeric'
            d['format'] = {'specifier': spec}
        cols_def.append(d)

    return dash_table.DataTable(
        data=df[data_cols].to_dict('records'),
        columns=cols_def,
        sort_action='native',
        filter_action='native',
        page_size=15,
        style_table={'overflowX': 'auto'},
        style_cell={'fontSize': '12px', 'padding': '4px 8px'},
        style_header={'fontWeight': 'bold', 'backgroundColor': '#f8f9fa'},
        style_data_conditional=[
            {'if': {'row_index': 'odd'}, 'backgroundColor': '#fafafa'},
        ],
    )


def _sow_chart(sub, v, title):
    df = sub.copy()
    df['share_of_wallet'] = df['our_bdw_turnover_rm'] / df[v['their_turnover']].replace(0, pd.NA)
    df = df.dropna(subset=['share_of_wallet']).sort_values('date')
    if df.empty:
        return dcc.Graph(figure=go.Figure())
    df['match_label'] = df['match_name'].fillna(df['match_id'].astype(str))
    fig = px.bar(
        df, x='match_label', y='share_of_wallet',
        labels={'match_label': 'Match', 'share_of_wallet': 'Share of Wallet'},
        title=title,
    )
    fig.update_layout(xaxis_tickangle=-45, margin=dict(t=40, b=120), yaxis_tickformat='.1%')
    return dcc.Graph(figure=fig)


def _view_section(df, v):
    sub = v['filter'](df)
    if sub.empty:
        return html.Div([
            html.H6(v['title'], className='mt-3 mb-1'),
            html.P(v['subtitle'], className='text-muted', style={'fontSize': '0.8rem'}),
            html.Div('No matches in this scope.', className='text-muted'),
        ])
    return html.Div([
        html.H6(v['title'], className='mt-3 mb-1'),
        html.P(v['subtitle'], className='text-muted', style={'fontSize': '0.8rem'}),
        html.Small('Period Total', className='text-muted'),
        _period_cards(sub, v),
        _sow_chart(sub, v, 'Share of Wallet per Match'),
        html.Small('Match-by-match', className='text-muted'),
        _match_table(sub, v),
        html.Hr(),
    ])


def render(df):
    if df is None or df.empty:
        return html.Div('No platform comparison data available.', className='text-muted p-3')
    return html.Div([_view_section(df, v) for v in VIEWS])
