import dash_bootstrap_components as dbc
from dash import dcc, html
import pandas as pd

_FILTERS = dbc.Row(
    [
        dbc.Col(
            [
                dbc.Label('Date Range', size='sm'),
                dcc.DatePickerRange(
                    id='date-range',
                    min_date_allowed='2026-06-01',
                    max_date_allowed='2026-07-31',
                    start_date='2026-06-11',
                    end_date='2026-07-20',
                    display_format='YYYY-MM-DD',
                ),
            ],
            width='auto',
        ),
        dbc.Col(
            [
                dbc.Label('Streamers', size='sm'),
                dcc.Dropdown(
                    id='streamer-filter',
                    multi=True,
                    placeholder='All streamers',
                    style={'minWidth': '280px'},
                ),
            ],
            width='auto',
        ),
        dbc.Col(
            dbc.Button('Refresh', id='refresh-btn', color='primary', size='sm', className='mt-3'),
            width='auto',
        ),
    ],
    className='mb-3 align-items-end g-3',
)

_TABS = dbc.Tabs(
    [
        dbc.Tab(label='Session View',      tab_id='tab-session'),
        dbc.Tab(label='Weekly / Monthly',  tab_id='tab-trends'),
        dbc.Tab(label='Platform Compare',  tab_id='tab-platform'),
        dbc.Tab(label='Alert Log',         tab_id='tab-alerts'),
    ],
    id='main-tabs',
    active_tab='tab-session',
    className='mb-3',
)


def build():
    return dbc.Container(
        [
            html.H4('World Cup 2026 — Streamer Performance', className='mt-3 mb-1'),
            html.Hr(className='mb-3'),
            _FILTERS,
            _TABS,
            html.Div(id='tab-content'),
            dcc.Store(id='streamer-options-store'),
        ],
        fluid=True,
    )
