import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import dash_table, dcc, html
import dash_bootstrap_components as dbc


def _scope_b_cards(df):
    if df.empty:
        return html.Div()
    row = df.iloc[0]

    def card(label, value, fmt='rm'):
        if pd.isna(value):
            display = 'n/a'
        elif fmt == 'rm':
            display = f'RM {value:,.0f}'
        elif fmt == 'pct':
            display = f'{value:.1%}'
        else:
            display = f'{value:,.0f}'
        return dbc.Col(
            dbc.Card(dbc.CardBody([
                html.P(label, className='text-muted mb-1', style={'fontSize': '0.8rem'}),
                html.H5(display, className='mb-0'),
            ]), className='text-center'),
            width=3,
        )

    return dbc.Row([
        card('Our Season BDW Turnover', row.get('our_season_bdw_turnover_rm'), 'rm'),
        card('Platform Season Turnover', row.get('platform_season_bet_turnover_rm'), 'rm'),
        card('Season Share of Wallet', row.get('season_share_of_wallet'), 'pct'),
        card('Season Share of Bets', row.get('season_share_of_bets'), 'pct'),
    ], className='g-2 mb-3')


def _scope_a_chart(df):
    if df.empty:
        return dcc.Graph(figure=go.Figure())
    df = df.copy()
    df['match_label'] = df.apply(
        lambda r: f"{r.get('HomeCnName','?')} v {r.get('AwayCnName','?')}", axis=1
    )
    df = df.sort_values('KickOffTime')
    fig = px.bar(
        df, x='match_label', y='match_share_of_wallet',
        labels={'match_label': 'Match', 'match_share_of_wallet': 'Share of Wallet'},
        title='Per-Match Share of Wallet (BDW Turnover / Platform Turnover)',
    )
    fig.update_layout(xaxis_tickangle=-45, margin=dict(t=40, b=120))
    return dcc.Graph(figure=fig)


_TABLE_COLS = [
    'HomeCnName', 'AwayCnName', 'KickOffTime',
    'our_bdw_bet_count', 'our_bdw_turnover_rm', 'our_avg_bet_size_rm',
    'platform_bet_count', 'platform_bet_turnover_rm', 'platform_avg_bet_size_rm',
    'match_share_of_wallet', 'match_share_of_bets',
]


def _scope_a_table(df):
    cols_present = [c for c in _TABLE_COLS if c in df.columns]
    cols_def = []
    for c in cols_present:
        if df[c].dtype in ['float64', 'int64']:
            cols_def.append({'name': c, 'id': c, 'type': 'numeric',
                             'format': {'specifier': ',.2f'}})
        else:
            cols_def.append({'name': c, 'id': c})

    return dash_table.DataTable(
        data=df[cols_present].to_dict('records'),
        columns=cols_def,
        sort_action='native',
        page_size=20,
        style_table={'overflowX': 'auto'},
        style_cell={'fontSize': '12px', 'padding': '4px 8px'},
        style_header={'fontWeight': 'bold', 'backgroundColor': '#f8f9fa'},
        style_data_conditional=[
            {'if': {'row_index': 'odd'}, 'backgroundColor': '#fafafa'},
        ],
    )


def render(df):
    if df is None or df.empty:
        return html.Div('No platform comparison data available.', className='text-muted p-3')
    return html.Div([
        html.H6('Season Aggregate (Scope B)', className='mb-2'),
        _scope_b_cards(df),
        html.Hr(),
        html.H6('Per-Match Comparison (Scope A)', className='mt-3 mb-2'),
        _scope_a_chart(df),
        _scope_a_table(df),
    ])
