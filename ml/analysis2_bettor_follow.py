"""
ml/analysis2_bettor_follow.py — Analysis 2: Bettor → Follow Bettor

Population (M-1) : total_bet_count > 0  (any bet type, including follow_bet per design)
Target (M)       : total_follow_bet_count > 0

Rule baseline : total_bet_count > median(total_bet_count) in training population
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
    bettors = df[df['total_bet_count'] > 0].copy()
    months  = sorted(bettors['month'].unique())

    print(f"\n{'═'*60}")
    print("ANALYSIS 2 — EDA")
    print(f"Bettor population: {len(bettors):,} user-months across {len(months)} months")

    # 1. Monthly overview
    overview = (
        bettors.groupby('month')
        .agg(
            n_bettors       =('cust_id', 'count'),
            n_follow_bettors=('is_follow_bet', 'sum'),  # already follow-betting
        )
        .assign(follow_bet_rate=lambda d: d['n_follow_bettors'] / d['n_bettors'])
        .reset_index()
    )
    print("\n── Monthly bettor population ──")
    print(overview.to_string(index=False))

    # 2. Follow bettor rate by segment
    fig, axes = plt.subplots(2, 3, figsize=(14, 8))
    seg_cols = ['account_age_tier', 'watch_bucket', 'time_segment',
                'day_segment', 'primary_stream_type', 'primary_device']
    for ax, col in zip(axes.flat, seg_cols):
        if col not in bettors.columns:
            ax.set_visible(False); continue
        rate = bettors.groupby(col)['is_follow_bet'].mean().sort_values()
        rate.plot(kind='barh', ax=ax)
        ax.set_title(f'Follow-bet rate by {col}')
        ax.set_xlabel('Rate')
    fig.suptitle('Analysis 2 — Follow Bettor Rate by Segment')
    fig.tight_layout(); plt.show()

    # 3. Behavioral profile: follow bettor vs regular bettor
    bettors['is_follow_bettor'] = bettors['is_follow_bet']
    num_cols = (WATCH_FEATS[:2] + CHAT_FEATS[:2] + GIFT_FEATS[:3]
                + ['total_bet_count', 'total_member_to'] + ['session_count'])
    print("\n── Follow bettor vs regular bettor (medians) ──")
    print(bettors.groupby('is_follow_bettor')[num_cols].median().T.to_string())

    # 4. Correlation heatmap
    corr_cols = num_cols + ['is_follow_bet']
    corr = bettors[corr_cols].corr()
    fig, ax = plt.subplots(figsize=(10, 8))
    sns.heatmap(corr, cmap='coolwarm', center=0, annot=True, fmt='.2f',
                linewidths=0.3, ax=ax)
    ax.set_title('Analysis 2 — Correlation Heatmap (bettors)')
    fig.tight_layout(); plt.show()

    # 5. Monthly trend + cohort stickiness
    stickiness = []
    for i in range(len(months) - 1):
        m0, m1 = months[i], months[i + 1]
        fb_m0 = set(bettors[(bettors['month'] == m0) & (bettors['is_follow_bet'] == 1)]['cust_id'])
        fb_m1 = set(bettors[(bettors['month'] == m1) & (bettors['is_follow_bet'] == 1)]['cust_id'])
        retained = len(fb_m0 & fb_m1) / max(len(fb_m0), 1)
        stickiness.append({'from_month': m0, 'retained_rate': retained})
    stk = pd.DataFrame(stickiness)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(stk['from_month'].astype(str), stk['retained_rate'])
    ax.set_title('Analysis 2 — Follow-bettor Retention (M → M+1)')
    ax.set_ylabel('Retention rate'); fig.tight_layout(); plt.show()

    return bettors


# ── ML ────────────────────────────────────────────────────────────────────────

def run_ml(df):
    print(f"\n{'═'*60}")
    print("ANALYSIS 2 — ML: Bettor → Follow Bettor")

    # target = had follow_bet next month (binary)
    df = df.copy()
    df['is_follow_bet_next'] = (df['total_follow_bet_count'] > 0).astype(int)

    pop_filter = lambda d: d['total_bet_count'] > 0
    X_tr, y_tr, X_te, y_te = build_pairs(
        df, ML_FEATURES, 'is_follow_bet_next', pop_filter
    )

    # Rule: total_bet_count > training-set median
    median_bet = X_tr['total_bet_count'].median()
    rule_te = (X_te['total_bet_count'] > median_bet).astype(float)
    print(f"  Rule threshold: total_bet_count > {median_bet:.1f}")

    results, models, probs = run_model_ladder(
        X_tr, y_tr, X_te, y_te,
        rule_pred_test=rule_te,
        analysis_tag='2', k=K,
    )

    plot_roc(y_te,   probs, 'Analysis 2: Bettor → Follow Bettor  |  ROC')
    plot_lift(y_te,  probs, 'Analysis 2: Bettor → Follow Bettor  |  Lift')
    for name, m in models.items():
        plot_importance(m, ML_FEATURES, f'Analysis 2 ({name}) — feature importance')

    save_artifacts('analysis2', results, models)
    return results, models, X_te, y_te, probs


# ── Entry point ───────────────────────────────────────────────────────────────

def run_analysis2(df=None):
    if df is None:
        print("Loading tfu_user_monthly from BigQuery …")
        df = load_table()
    print(f"Loaded {len(df):,} rows | {df['month'].nunique()} months")

    run_eda(df)
    return run_ml(df)


if __name__ == '__main__':
    run_analysis2()
