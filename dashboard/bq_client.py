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
    'bdw_bet_count', 'watch_min_total', 'viewers',
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
            stream_id, streamer_id, streamer, stream_name, day, start_ts, end_ts,
            stream_type, language, site, stream_site_id, currency,
            match_id AS SabaMatchId, match_shared,
            KickOffTime, time_slot_taipei, day_of_week, match_stage,
            {', '.join(AGG_COLS)},
            watch_min_per_viewer, pcu, chat_user, message_count
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
        SELECT DISTINCT streamer_id, streamer
        FROM `{DATASET}.agg_session_metrics`
        ORDER BY streamer
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
        df.groupby(['streamer_id', 'streamer', pd.Grouper(key='day', freq='W-MON')])[sum_cols]
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
        df.groupby(['streamer_id', 'streamer', 'month_start', 'period_label'])[sum_cols]
        .sum()
        .reset_index()
        .sort_values(['streamer_id', 'month_start'])
    )

    # vs_june delta columns on July rows
    vs_cols = [
        'follow_streamer_bet_count', 'follow_streamer_bet_turnover_rm',
        'bdw_turnover_rm', 'donation_amount_usd', 'watch_min_total',
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
    """Per-match platform metrics joined with our BDW metrics from sessions.

    Returns one row per (date, match_id) with both our and platform-side
    columns populated. Matches we didn't stream get 0 on the our_* side.
    """
    key = 'platform'
    if key in _cache:
        return _cache[key]
    plat = _query(f"""
        SELECT *
        FROM `{DATASET}.agg_match_platform_compare`
        ORDER BY date, match_id
    """)
    sess = _full_session()

    ours = (
        sess.dropna(subset=['SabaMatchId'])
        .groupby('SabaMatchId', as_index=False)
        .agg(
            our_bdw_bet_count=('bdw_bet_count', 'sum'),
            our_bdw_turnover_rm=('bdw_turnover_rm', 'sum'),
            our_match_stage=('match_stage', 'first'),
            our_kickoff=('KickOffTime', 'first'),
        )
    )
    ours['SabaMatchId'] = ours['SabaMatchId'].astype('Int64')
    plat['match_id'] = plat['match_id'].astype('Int64')

    df = plat.merge(ours, left_on='match_id', right_on='SabaMatchId', how='left')
    df['our_bdw_bet_count'] = df['our_bdw_bet_count'].fillna(0)
    df['our_bdw_turnover_rm'] = df['our_bdw_turnover_rm'].fillna(0)
    df['our_avg_bet_size_rm'] = df['our_bdw_turnover_rm'] / df['our_bdw_bet_count'].replace(0, pd.NA)
    _cache.set(key, df, expire=3600)
    return df
