import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html
import dash_bootstrap_components as dbc

WEEKLY_METRICS = [
    ('follow_streamer_bet_count', 'follow_streamer_bet_count_cumavg_prior',
     'Follow Streamer Bet Count', ''),
    ('bdw_turnover_rm', 'bdw_turnover_rm_cumavg_prior',
     'BDW Turnover', 'RM'),
    ('donation_amount_usd', 'donation_amount_usd_cumavg_prior',
     'Donation Amount', 'USD'),
]


def _weekly_chart(df_weekly, metric_col, baseline_col, label, unit):
    if df_weekly.empty or metric_col not in df_weekly.columns:
        return dcc.Graph(figure=go.Figure())

    streamers = df_weekly['streamer_id'].unique()
    fig = go.Figure()

    for sid in streamers:
        sub = df_weekly[df_weekly['streamer_id'] == sid].sort_values('iso_week_start')
        name = sub['streamer_name'].iloc[0] if 'streamer_name' in sub.columns else str(sid)
        fig.add_trace(go.Scatter(
            x=sub['iso_week_start'], y=sub[metric_col],
            mode='lines+markers', name=name,
        ))
        if baseline_col in sub.columns:
            fig.add_trace(go.Scatter(
                x=sub['iso_week_start'], y=sub[baseline_col],
                mode='lines', line=dict(dash='dot'),
                name=f'{name} (prior avg)', showlegend=False,
            ))

    prefix = f'{unit} ' if unit else ''
    fig.update_layout(
        title=f'Weekly {label}',
        yaxis_title=f'{prefix}{label}',
        xaxis_title='Week',
        legend=dict(orientation='h', y=-0.2),
        margin=dict(t=40, b=60),
    )
    return dcc.Graph(figure=fig)


def _monthly_bars(df_monthly):
    if df_monthly.empty:
        return html.Div('No monthly data.', className='text-muted')

    metrics = [
        ('follow_streamer_bet_count', 'Follow Streamer Bet Count', ''),
        ('bdw_turnover_rm', 'BDW Turnover (RM)', 'RM'),
        ('donation_amount_usd', 'Donation Amount (USD)', 'USD'),
    ]

    vs_cols = {
        'follow_streamer_bet_count': 'follow_streamer_bet_count_vs_june',
        'bdw_turnover_rm': 'bdw_turnover_rm_vs_june',
        'donation_amount_usd': 'donation_amount_usd_vs_june',
    }

    charts = []
    for col, label, unit in metrics:
        if col not in df_monthly.columns:
            continue
        fig = go.Figure()
        for period in ['June', 'July']:
            sub = df_monthly[df_monthly['period_label'] == period]
            if sub.empty:
                continue
            # aggregate across streamers
            agg = sub.groupby('streamer_name')[col].sum().reset_index() if 'streamer_name' in sub.columns \
                else sub.groupby('streamer_id')[col].sum().reset_index()
            x_col = 'streamer_name' if 'streamer_name' in agg.columns else 'streamer_id'

            # attach vs_june pct for July bars
            text = None
            if period == 'July':
                vs_col = vs_cols.get(col)
                if vs_col and vs_col in sub.columns:
                    vs = sub.groupby(x_col)[vs_col].mean().reset_index()
                    agg = agg.merge(vs, on=x_col, how='left')
                    text = agg[vs_col].apply(
                        lambda v: f'{v:+.0%}' if pd.notna(v) else ''
                    ).tolist()

            fig.add_trace(go.Bar(
                x=agg[x_col], y=agg[col],
                name=period,
                text=text,
                textposition='outside',
            ))

        prefix = f'{unit} ' if unit else ''
        fig.update_layout(
            title=f'{label} — June vs July',
            barmode='group',
            yaxis_title=f'{prefix}{label}',
            margin=dict(t=40, b=40),
        )
        charts.append(dcc.Graph(figure=fig))

    return html.Div(charts)


def render(df_weekly, df_monthly):
    weekly_charts = []
    if df_weekly is not None and not df_weekly.empty:
        for metric_col, baseline_col, label, unit in WEEKLY_METRICS:
            weekly_charts.append(_weekly_chart(df_weekly, metric_col, baseline_col, label, unit))
    else:
        weekly_charts = [html.Div('No weekly data.', className='text-muted p-2')]

    monthly_content = _monthly_bars(df_monthly) if df_monthly is not None else \
        html.Div('No monthly data.', className='text-muted p-2')

    return html.Div([
        html.H6('Weekly Trends (vs cumulative prior-weeks average)', className='mb-2'),
        *weekly_charts,
        html.Hr(),
        html.H6('Monthly — June vs July', className='mt-3 mb-2'),
        monthly_content,
    ])
