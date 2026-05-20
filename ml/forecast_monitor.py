"""
ml/forecast_monitor.py — Monthly forecast + monitoring pipeline.

Monthly cadence:
  1. Score all current-month users with champion models
  2. Export top-K intervention list per analysis
  3. Snapshot KPIs
  4. Drift alerts (>30% rate change, >25% population change)

Quarterly cadence:
  - Rolling 6-month retrain
  - AUC comparison vs prior quarter

A/B test tracking:
  - 50% holdout on Donated and FollowBet segments
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, hashlib
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from ml.utils import (
    load_table, get_bq_client, ML_FEATURES, ARTIFACTS_DIR,
    build_pairs, run_model_ladder, save_artifacts,
)
from ml.compare_select import load_champion_models, run_compare_select


BQ_PROJECT  = 'nf-muses'
BQ_TABLE    = '`nf-muses.muses.tfu_user_monthly`'
TOP_K       = 200            # intervention list size per analysis
KPI_PATH    = os.path.join(ARTIFACTS_DIR, 'kpi_snapshots.json')

DRIFT_RATE_THRESHOLD = 0.30   # 30% relative change in conversion rate
DRIFT_POP_THRESHOLD  = 0.25   # 25% relative change in population size


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ab_flag(cust_id: str) -> str:
    """Deterministic 50/50 A/B split based on cust_id hash."""
    return 'holdout' if int(hashlib.md5(str(cust_id).encode()).hexdigest(), 16) % 2 == 0 else 'treatment'


def _latest_month(df):
    return df['month'].max()


# ── Monthly scoring ───────────────────────────────────────────────────────────

def score_monthly(df=None):
    """
    Score all users in the latest month with champion models.
    Returns a DataFrame with cust_id, analysis, score, rank, ab_flag.
    """
    if df is None:
        print("Loading tfu_user_monthly …")
        df = load_table()

    champion_models = load_champion_models()
    latest = _latest_month(df)
    latest_df = df[df['month'] == latest].copy()

    print(f"\nScoring month: {latest.date()}  |  {len(latest_df):,} users")

    score_records = []

    # Analysis 1a — Donated population
    for tag, pop_mask_fn, label in [
        ('analysis1a', lambda d: d['is_donated'] == 1,    'Donated'),
        ('analysis1b', lambda d: d['is_follow_bet'] == 1, 'FollowBet'),
    ]:
        model = champion_models.get(tag)
        if model is None:
            print(f"  [{tag}] no champion model — skipping")
            continue
        pop = latest_df[pop_mask_fn(latest_df)].copy()
        if pop.empty:
            continue
        X = pop[ML_FEATURES].fillna(0).astype(float)
        pop['score'] = model.predict_proba(X)[:, 1]
        pop['analysis'] = tag
        pop['segment']  = label
        pop['ab_flag']  = pop['cust_id'].apply(_ab_flag)
        score_records.append(pop[['cust_id', 'analysis', 'segment', 'score', 'ab_flag']])

    # Analysis 2 — Bettor population
    model2 = champion_models.get('analysis2')
    if model2 is not None:
        pop = latest_df[latest_df['total_bet_count'] > 0].copy()
        if not pop.empty:
            X = pop[ML_FEATURES].fillna(0).astype(float)
            pop['score']    = model2.predict_proba(X)[:, 1]
            pop['analysis'] = 'analysis2'
            pop['segment']  = 'Bettor'
            pop['ab_flag']  = pop['cust_id'].apply(_ab_flag)
            score_records.append(pop[['cust_id', 'analysis', 'segment', 'score', 'ab_flag']])

    # Analysis 3 — Watcher population
    model3 = champion_models.get('analysis3')
    if model3 is not None:
        pop = latest_df[latest_df['if_watch'] == 1].copy()
        if not pop.empty:
            X = pop[ML_FEATURES].fillna(0).astype(float)
            pop['score']    = model3.predict_proba(X)[:, 1]
            pop['analysis'] = 'analysis3'
            pop['segment']  = 'Watcher'
            pop['ab_flag']  = pop['cust_id'].apply(_ab_flag)
            score_records.append(pop[['cust_id', 'analysis', 'segment', 'score', 'ab_flag']])

    if not score_records:
        print("No scores generated.")
        return pd.DataFrame()

    all_scores = pd.concat(score_records, ignore_index=True)
    all_scores['rank'] = (
        all_scores.groupby('analysis')['score']
        .rank(ascending=False, method='first')
        .astype(int)
    )
    return all_scores


def export_intervention_list(scores_df, top_k=TOP_K):
    """Return treatment-arm users ranked in top-K per analysis."""
    if scores_df.empty:
        return pd.DataFrame()
    intervention = (
        scores_df[
            (scores_df['ab_flag'] == 'treatment') &
            (scores_df['rank'] <= top_k)
        ]
        .sort_values(['analysis', 'rank'])
        [['cust_id', 'analysis', 'segment', 'score', 'rank', 'ab_flag']]
    )
    print(f"\n── Intervention list: {len(intervention):,} users (top {top_k} per analysis, treatment arm) ──")
    print(intervention.groupby('analysis')['cust_id'].count().to_string())
    return intervention


# ── KPI snapshot ─────────────────────────────────────────────────────────────

def snapshot_kpis(df):
    """Compute and persist KPI snapshot for the latest month."""
    latest = _latest_month(df)
    sub = df[df['month'] == latest]

    kpi = {
        'month': str(latest.date()),
        'n_users': int(len(sub)),
        'tfu_rate': float(sub['is_tfu'].mean()),
        'donated_rate': float(sub['is_donated'].mean()),
        'follow_bet_rate': float(sub['is_follow_bet'].mean()),
        'cold_rate': float((sub['tfu_gap'] == 2).mean()),
        'n_tfu': int(sub['is_tfu'].sum()),
        'n_donated': int(sub['is_donated'].sum()),
        'n_follow_bet': int(sub['is_follow_bet'].sum()),
    }
    print(f"\n── KPI Snapshot ({kpi['month']}) ──")
    for k, v in kpi.items():
        print(f"  {k}: {v}")

    # Append to history file
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    history = []
    if os.path.exists(KPI_PATH):
        with open(KPI_PATH) as f:
            history = json.load(f)
    # Replace if same month already exists
    history = [h for h in history if h.get('month') != kpi['month']]
    history.append(kpi)
    with open(KPI_PATH, 'w') as f:
        json.dump(history, f, indent=2)

    return kpi


# ── Drift detection ───────────────────────────────────────────────────────────

def check_drift():
    """Compare the two most recent KPI snapshots and alert on drift."""
    if not os.path.exists(KPI_PATH):
        print("No KPI history found.")
        return

    with open(KPI_PATH) as f:
        history = json.load(f)

    if len(history) < 2:
        print("Need at least 2 snapshots for drift detection.")
        return

    prev, curr = history[-2], history[-1]
    print(f"\n── Drift check: {prev['month']} → {curr['month']} ──")
    alerts = []

    for metric in ['tfu_rate', 'donated_rate', 'follow_bet_rate']:
        p, c = prev[metric], curr[metric]
        if p == 0:
            continue
        delta = abs(c - p) / p
        status = 'ALERT ⚠' if delta > DRIFT_RATE_THRESHOLD else 'ok'
        print(f"  {metric:20s}: {p:.3%} → {c:.3%}  ({delta:+.1%})  [{status}]")
        if status.startswith('ALERT'):
            alerts.append(f"{metric}: {p:.3%} → {c:.3%}")

    for metric in ['n_users', 'n_tfu', 'n_donated', 'n_follow_bet']:
        p, c = prev[metric], curr[metric]
        if p == 0:
            continue
        delta = abs(c - p) / p
        status = 'ALERT ⚠' if delta > DRIFT_POP_THRESHOLD else 'ok'
        print(f"  {metric:20s}: {p:,} → {c:,}  ({delta:+.1%})  [{status}]")
        if status.startswith('ALERT'):
            alerts.append(f"{metric}: {p:,} → {c:,}")

    if alerts:
        print(f"\n  ⚠  {len(alerts)} drift alert(s) — review before next scoring run.")
    else:
        print("\n  All metrics within normal range.")

    return alerts


# ── Quarterly retrain ─────────────────────────────────────────────────────────

def quarterly_retrain(df=None):
    """
    Retrain all analyses on the latest 6 months of data.
    Compare new AUCs vs previous champions; update artifacts if improved.
    """
    print(f"\n{'═'*60}")
    print("QUARTERLY RETRAIN")

    if df is None:
        df = load_table()

    # Re-import here to avoid circular import at module level
    from ml.analysis1_gap_tfu      import run_model_a, run_model_b
    from ml.analysis2_bettor_follow import run_ml as run_ml2
    from ml.analysis3_watcher_tipper import run_ml as run_ml3

    run_model_a(df)
    run_model_b(df)
    run_ml2(df)
    run_ml3(df)

    new_champions = run_compare_select()
    print("\nQuarterly retrain complete. New champions:")
    for tag, champ in new_champions.items():
        print(f"  {tag}: {champ['model']}  AUC={champ['auc']:.4f}")

    return new_champions


# ── Full monthly pipeline ─────────────────────────────────────────────────────

def run_monthly_pipeline(df=None):
    """
    End-to-end monthly run:
      score → export → snapshot KPIs → drift check
    """
    if df is None:
        print("Loading tfu_user_monthly …")
        df = load_table()

    scores      = score_monthly(df)
    intervention = export_intervention_list(scores)
    snapshot_kpis(df)
    check_drift()

    return scores, intervention


if __name__ == '__main__':
    run_monthly_pipeline()
