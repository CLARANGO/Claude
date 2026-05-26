import diskcache
import pandas as pd
from google.cloud import bigquery

_cache = diskcache.Cache('.dash_cache')
_bq = None

DATASET = 'nf-muses.worldcup'

AGG_COLS = [
    'follow_streamer_bet_count', 'bdw_turnover_rm', 'donation_amount_usd',
    'recommend_bet_count', 'follow_streamer_bet_turnover_rm',
    'follow_user_count', 'donation_user_count', 'donation_count',
    'tip_amount_usd', 'tip_count', 'box_amount_usd', 'box_count',
    'wheel_amount_usd', 'wheel_count',
    'self_bet_count', 'follow_user_bet_count',
    'self_bet_turnover_rm', 'follow_user_bet_turnover_rm',
    'bdw_bet_count', 'watch_seconds_total', 'viewers',
    'watch_over_10min_user', 'total_bet_count', 'total_bet_turnover_rm',
]


def _client():
    global _bq
    if _bq is None:
        _bq = bigquery.Client(project='nf-muses')
    return _bq


def _query(sql):
    return _client().query(sql).to_dataframe()


def _cache_key(*args):
    return str(args)


def _full_session():
    """Full season pull (Jun–Jul) used for weekly/monthly derivations."""
    key = 'session_full'
    if key in _cache:
        return _cache[key]
    df = _query(f"""
        SELECT
            stream_id, streamer_id, streamer_name, day, start_ts, end_ts,
            stream_type, language, site, currency,
            SabaMatchId, HomeCnName, AwayCnName, KickOffTime, time_slot_taipei,
            {', '.join(AGG_COLS)},
            watch_seconds_per_viewer, pcu, chatters, message_count
        FROM `{DATASET}.agg_session_metrics`
        WHERE day BETWEEN DATE '2026-06-01' AND DATE '2026-07-31'
        ORDER BY day, start_ts
    """)
    df['day'] = pd.to_datetime(df['day'])
    _cache.set(key, df, expire=3600)
    return df


def get_streamers():
    key = 'streamers'
    if key in _cache:
        return _cache[key]
    df = _query(f"""
        SELECT DISTINCT streamer_id, streamer_name
        FROM `{DATASET}.agg_session_metrics`
        ORDER BY streamer_name
    """)
    _cache.set(key, df, expire=3600)
    return df


def get_session_metrics(start_date, end_date, streamer_ids=None):
    key = _cache_key('session', start_date, end_date, tuple(streamer_ids or []))
    if key in _cache:
        return _cache[key]
    df = _full_session()
    mask = (df['day'] >= pd.Timestamp(start_date)) & (df['day'] <= pd.Timestamp(end_date))
    if streamer_ids:
        mask &= df['streamer_id'].isin(streamer_ids)
    result = df[mask].sort_values(['day', 'start_ts'], ascending=False).reset_index(drop=True)
    _cache.set(key, result, expire=3600)
    return result


def get_weekly(streamer_ids=None):
    key = _cache_key('weekly', tuple(streamer_ids or []))
    if key in _cache:
        return _cache[key]
    df = _full_session()
    if streamer_ids:
        df = df[df['streamer_id'].isin(streamer_ids)]

    sum_cols = [c for c in AGG_COLS if c in df.columns]
    weekly = (
        df.groupby(['streamer_id', 'streamer_name', pd.Grouper(key='day', freq='W-MON')])[sum_cols]
        .sum()
        .reset_index()
        .rename(columns={'day': 'iso_week_start'})
    )
    weekly = weekly.sort_values(['streamer_id', 'iso_week_start'])

    # cumulative-prior-weeks average baseline for each alert metric
    alert_cols = [
        'follow_streamer_bet_count', 'bdw_turnover_rm', 'donation_amount_usd',
        'follow_streamer_bet_turnover_rm', 'donation_user_count', 'bdw_bet_count',
    ]
    for col in alert_cols:
        if col not in weekly.columns:
            continue
        weekly[f'{col}_cumavg_prior'] = (
            weekly.groupby('streamer_id')[col]
            .transform(lambda s: s.shift(1).expanding().mean())
        )

    _cache.set(key, weekly, expire=3600)
    return weekly


def get_monthly(streamer_ids=None):
    key = _cache_key('monthly', tuple(streamer_ids or []))
    if key in _cache:
        return _cache[key]
    df = _full_session()
    if streamer_ids:
        df = df[df['streamer_id'].isin(streamer_ids)]

    df = df[df['day'].dt.month.isin([6, 7])]
    df['month_start'] = df['day'].dt.to_period('M').dt.to_timestamp()
    df['period_label'] = df['day'].dt.month.map({6: 'June', 7: 'July'})

    sum_cols = [c for c in AGG_COLS if c in df.columns]
    monthly = (
        df.groupby(['streamer_id', 'streamer_name', 'month_start', 'period_label'])[sum_cols]
        .sum()
        .reset_index()
        .sort_values(['streamer_id', 'month_start'])
    )

    # vs_june delta columns on July rows
    vs_cols = [
        'follow_streamer_bet_count', 'follow_streamer_bet_turnover_rm',
        'bdw_turnover_rm', 'donation_amount_usd', 'watch_seconds_total',
    ]
    june = monthly[monthly['period_label'] == 'June'].set_index('streamer_id')[vs_cols]
    july_mask = monthly['period_label'] == 'July'
    for col in vs_cols:
        if col not in monthly.columns:
            continue
        june_vals = monthly.loc[july_mask, 'streamer_id'].map(june[col])
        monthly.loc[july_mask, f'{col}_vs_june'] = (
            (monthly.loc[july_mask, col] - june_vals) / june_vals.abs()
        )

    _cache.set(key, monthly, expire=3600)
    return monthly


def get_platform_compare():
    key = 'platform'
    if key in _cache:
        return _cache[key]
    df = _query(f"""
        SELECT *
        FROM `{DATASET}.agg_match_platform_compare`
        ORDER BY KickOffTime
    """)
    _cache.set(key, df, expire=3600)
    return df
