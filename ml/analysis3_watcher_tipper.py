"""
ml/analysis3_watcher_tipper.py — Analysis 3: Watcher → Tipper

Population (M-1) : if_watch = 1
Target (M)       : total_gift_count > 0  (tip + box + wheel)

Rule baseline : watch_bucket >= 3  (avg ≥ 30 min/session)
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from ml.utils import (
    load_table, build_pairs, run_model_ladder,
    ML_FEATURES, WATCH_FEATS, CHAT_FEATS, GIFT_FEATS, BET_FEATS, SESSION_FEATS,
    plot_roc, plot_lift, plot_importance, save_artifacts,
)

K = 100


# ── EDA ───────────────────────────────────────────────────────────────────────

def run_eda(df):
    watchers = df[df['if_watch'] == 1].copy()
    months   = sorted(watchers['month'].unique())

    print(f"\n{'═'*60}")
    print("ANALYSIS 3 — EDA")
    print(f"Watcher population: {len(watchers):,} user-months across {len(months)} months")

    watchers['is_tipper'] = (watchers['total_gift_count'] > 0).astype(int)

    # 1. Monthly overview
    overview = (
        watchers.groupby('month')
        .agg(
            n_watchers =('cust_id', 'count'),
            n_tippers  =('is_tipper', 'sum'),
        )
        .assign(tipper_rate=lambda d: d['n_tippers'] / d['n_watchers'])
        .reset_index()
    )
    print("\n── Monthly watcher/tipper population ──")
    print(overview.to_string(index=False))

    # 2. Tipper rate by watch_bucket  ← key chart
    bucket_rate = (
        watchers.groupby('watch_bucket')
        .agg(n=('cust_id', 'count'), tippers=('is_tipper', 'sum'))
        .assign(tipper_rate=lambda d: d['tippers'] / d['n'])
        .reset_index()
    )
    print("\n── Tipper rate by watch bucket ──")
    print(bucket_rate.to_string(index=False))

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(bucket_rate['watch_bucket'].astype(str), bucket_rate['tipper_rate'])
    ax.set_title('Analysis 3 — Tipper Rate by Watch Bucket\n'
                 '(1=<15 min, 2=15–30, 3=30–45, 4=>45 avg/session)')
    ax.set_xlabel('watch_bucket'); ax.set_ylabel('Tipper rate')
    fig.tight_layout(); plt.show()

    # 3. Behavioral profile: tipper vs non-tipper
    num_cols = (WATCH_FEATS[:2] + ['session_count'] + CHAT_FEATS[:2]
                + ['total_bet_count', 'total_bdw_bet_count'] + ['distinct_streamers'])
    print("\n── Tipper vs non-tipper profile (medians) ──")
    print(watchers.groupby('is_tipper')[num_cols].median().T.to_string())

    # 4. Correlation heatmap
    corr_cols = num_cols + ['is_tipper']
    corr = watchers[corr_cols].corr()
    fig, ax = plt.subplots(figsize=(10, 8))
    sns.heatmap(corr, cmap='coolwarm', center=0, annot=True, fmt='.2f',
                linewidths=0.3, ax=ax)
    ax.set_title('Analysis 3 — Correlation Heatmap (watchers)')
    fig.tight_layout(); plt.show()

    # 5. Monthly tipper rate by watch_bucket + cohort stickiness
    fig, ax = plt.subplots(figsize=(9, 4))
    for wb in sorted(watchers['watch_bucket'].unique()):
        sub = (watchers[watchers['watch_bucket'] == wb]
               .groupby('month')['is_tipper'].mean())
        ax.plot(sub.index, sub.values, marker='o', label=f'bucket={wb}')
    ax.set_title('Analysis 3 — Tipper Rate Over Time by Watch Bucket')
    ax.set_ylabel('Tipper rate'); ax.legend(); fig.tight_layout(); plt.show()

    stickiness = []
    for i in range(len(months) - 1):
        m0, m1 = months[i], months[i + 1]
        tp_m0 = set(watchers[(watchers['month'] == m0) & (watchers['is_tipper'] == 1)]['cust_id'])
        tp_m1 = set(watchers[(watchers['month'] == m1) & (watchers['is_tipper'] == 1)]['cust_id'])
        retained = len(tp_m0 & tp_m1) / max(len(tp_m0), 1)
        stickiness.append({'from_month': m0, 'retained_rate': retained})
    stk = pd.DataFrame(stickiness)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(stk['from_month'].astype(str), stk['retained_rate'])
    ax.set_title('Analysis 3 — Tipper Cohort Retention (M → M+1)')
    ax.set_ylabel('Retention rate'); fig.tight_layout(); plt.show()

    return watchers


# ── ML ────────────────────────────────────────────────────────────────────────

def run_ml(df):
    print(f"\n{'═'*60}")
    print("ANALYSIS 3 — ML: Watcher → Tipper")

    df = df.copy()
    df['is_tipper_next'] = (df['total_gift_count'] > 0).astype(int)

    pop_filter = lambda d: d['if_watch'] == 1
    X_tr, y_tr, X_te, y_te = build_pairs(
        df, ML_FEATURES, 'is_tipper_next', pop_filter
    )

    # Rule: watch_bucket >= 3 (avg ≥ 30 min/session)
    rule_te = (X_te['watch_bucket'] >= 3).astype(float)

    results, models, probs = run_model_ladder(
        X_tr, y_tr, X_te, y_te,
        rule_pred_test=rule_te,
        analysis_tag='3', k=K,
    )

    # Deep-dive: performance by watch_bucket
    print("\n── Lift@100 by watch_bucket (best model) ──")
    best_model_name = max(
        (r for r in results if r['model'] != 'rule'),
        key=lambda r: r['auc'],
        default=results[-1]
    )['model']
    best_probs = probs.get(best_model_name)
    if best_probs is not None:
        from ml.utils import lift_at_k
        test_pairs = X_te.copy()
        test_pairs['y_true']  = y_te.values
        test_pairs['y_prob']  = best_probs
        for wb, grp in test_pairs.groupby('watch_bucket'):
            if len(grp) < 10:
                continue
            lift = lift_at_k(grp['y_true'].values, grp['y_prob'].values,
                             min(K, len(grp)))
            print(f"  watch_bucket={wb}: n={len(grp):,}  lift={lift:.2f}x")

    plot_roc(y_te,   probs, 'Analysis 3: Watcher → Tipper  |  ROC')
    plot_lift(y_te,  probs, 'Analysis 3: Watcher → Tipper  |  Lift')
    for name, m in models.items():
        plot_importance(m, ML_FEATURES, f'Analysis 3 ({name}) — feature importance')

    save_artifacts('analysis3', results, models)
    return results, models, X_te, y_te, probs


# ── Entry point ───────────────────────────────────────────────────────────────

def run_analysis3(df=None):
    if df is None:
        print("Loading tfu_user_monthly from BigQuery …")
        df = load_table()
    print(f"Loaded {len(df):,} rows | {df['month'].nunique()} months")

    run_eda(df)
    return run_ml(df)


if __name__ == '__main__':
    run_analysis3()
