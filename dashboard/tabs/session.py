import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import dash_table, dcc, html
import dash_bootstrap_components as dbc

NS_METRICS = [
    ('follow_streamer_bet_count', 'Follow Streamer Bet Count', ''),
    ('bdw_turnover_rm',           'BDW Turnover',              'RM'),
    ('donation_amount_usd',       'Donation Amount',           'USD'),
]


def _kpi_card(label, value, unit):
    if pd.isna(value):
        display = 'n/a'
    elif unit == 'RM':
        display = f'RM {value:,.0f}'
    elif unit == 'USD':
        display = f'USD {value:,.0f}'
    else:
        display = f'{value:,.0f}'
    return dbc.Card(
        dbc.CardBody([
            html.P(label, className='text-muted mb-1', style={'fontSize': '0.8rem'}),
            html.H5(display, className='mb-0'),
        ]),
        className='text-center',
    )


def _kpi_row(df):
    cards = []
    for col, label, unit in NS_METRICS:
        val = df[col].sum() if col in df.columns else None
        cards.append(dbc.Col(_kpi_card(label, val, unit), width=4))
    return dbc.Row(cards, className='mb-3 g-2')


def _daily_bar(df):
    if df.empty:
        return dcc.Graph(figure=go.Figure())
    daily = (
        df.groupby('day')[['follow_streamer_bet_count', 'bdw_turnover_rm', 'donation_amount_usd']]
        .sum()
        .reset_index()
    )
    daily['day'] = pd.to_datetime(daily['day'])
    fig = px.bar(
        daily,
        x='day',
        y='follow_streamer_bet_count',
        labels={'day': 'Date', 'follow_streamer_bet_count': 'Follow Streamer Bet Count'},
        title='Daily Follow Streamer Bet Count',
    )
    fig.update_layout(margin=dict(t=40, b=20))
    return dcc.Graph(figure=fig)


_TABLE_COLS = [
    'streamer', 'stream_name', 'day', 'start_ts', 'match_stage', 'time_slot_taipei',
    'follow_streamer_bet_count', 'bdw_turnover_rm', 'donation_amount_usd',
    'recommend_bet_count', 'follow_streamer_bet_turnover_rm',
    'follow_user_count', 'donation_user_count', 'donation_count',
    'bdw_bet_count', 'viewers', 'watch_over_10min_user', 'pcu', 'watch_min_per_viewer',
    'chat_user', 'message_count',
    'total_bet_count', 'total_bet_turnover_rm',
]


def _session_table(df):
    cols_present = [c for c in _TABLE_COLS if c in df.columns]
    cols_def = [{'name': c, 'id': c, 'type': 'numeric', 'format': {'specifier': ',.1f'}}
                if df[c].dtype in ['float64', 'int64'] else {'name': c, 'id': c}
                for c in cols_present]
    return dash_table.DataTable(
        data=df[cols_present].to_dict('records'),
        columns=cols_def,
        page_size=20,
        sort_action='native',
        filter_action='native',
        style_table={'overflowX': 'auto'},
        style_cell={'fontSize': '12px', 'padding': '4px 8px'},
        style_header={'fontWeight': 'bold', 'backgroundColor': '#f8f9fa'},
        style_data_conditional=[
            {'if': {'row_index': 'odd'}, 'backgroundColor': '#fafafa'},
        ],
    )


def render(df):
    if df is None or df.empty:
        return html.Div('No data for selected filters.', className='text-muted p-3')
    return html.Div([
        _kpi_row(df),
        _daily_bar(df),
        html.H6('Session Detail', className='mt-3 mb-2'),
        _session_table(df),
    ])
