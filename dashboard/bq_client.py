import diskcache
import pandas as pd
from google.cloud import bigquery

_cache = diskcache.Cache('.dash_cache')
_bq = None

DATASET = 'nf-muses.worldcup'


def _client():
    global _bq
    if _bq is None:
        _bq = bigquery.Client(project='nf-muses')
    return _bq


def _query(sql):
    return _client().query(sql).to_dataframe()


def _cache_key(*args):
    return str(args)


def get_streamers():
    key = 'streamers'
    if key in _cache:
        return _cache[key]
    df = _query(f"""
        SELECT streamer_id, streamer_name, supplier
        FROM `{DATASET}.dim_streamer`
        ORDER BY streamer_name
    """)
    _cache.set(key, df, expire=3600)
    return df


def get_session_metrics(start_date, end_date, streamer_ids=None):
    key = _cache_key('session', start_date, end_date, tuple(streamer_ids or []))
    if key in _cache:
        return _cache[key]
    streamer_filter = ''
    if streamer_ids:
        ids = ','.join(str(i) for i in streamer_ids)
        streamer_filter = f'AND streamer_id IN ({ids})'
    df = _query(f"""
        SELECT
            stream_id, streamer_id, streamer_name, day, start_ts, end_ts,
            stream_type, language, site, currency,
            SabaMatchId, HomeCnName, AwayCnName, KickOffTime, time_slot_taipei,
            follow_streamer_bet_count, bdw_turnover_rm, donation_amount_usd,
            recommend_bet_count, follow_streamer_bet_turnover_rm,
            follow_user_count, donation_user_count, donation_count,
            tip_amount_usd, tip_count, box_amount_usd, box_count,
            wheel_amount_usd, wheel_count,
            self_bet_count, follow_user_bet_count,
            self_bet_turnover_rm, follow_user_bet_turnover_rm,
            bdw_bet_count, watch_seconds_total, viewers,
            watch_over_10min_user, watch_seconds_per_viewer, pcu,
            chatters, message_count, total_bet_count, total_bet_turnover_rm
        FROM `{DATASET}.agg_session_metrics`
        WHERE day BETWEEN DATE '{start_date}' AND DATE '{end_date}'
        {streamer_filter}
        ORDER BY day DESC, start_ts DESC
    """)
    _cache.set(key, df, expire=3600)
    return df


def get_weekly(streamer_ids=None):
    key = _cache_key('weekly', tuple(streamer_ids or []))
    if key in _cache:
        return _cache[key]
    streamer_filter = ''
    if streamer_ids:
        ids = ','.join(str(i) for i in streamer_ids)
        streamer_filter = f'WHERE streamer_id IN ({ids})'
    df = _query(f"""
        SELECT *
        FROM `{DATASET}.agg_streamer_weekly`
        {streamer_filter}
        ORDER BY streamer_id, iso_week_start
    """)
    _cache.set(key, df, expire=3600)
    return df


def get_monthly(streamer_ids=None):
    key = _cache_key('monthly', tuple(streamer_ids or []))
    if key in _cache:
        return _cache[key]
    streamer_filter = ''
    if streamer_ids:
        ids = ','.join(str(i) for i in streamer_ids)
        streamer_filter = f'WHERE streamer_id IN ({ids})'
    df = _query(f"""
        SELECT *
        FROM `{DATASET}.agg_streamer_monthly`
        {streamer_filter}
        ORDER BY streamer_id, month_start
    """)
    _cache.set(key, df, expire=3600)
    return df


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
