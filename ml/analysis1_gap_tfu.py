"""
ml/analysis1_gap_tfu.py — Analysis 1: Gap=1 → TFU

Population (M-1) : is_donated=1 OR is_follow_bet=1  (~736 users/month)
Target (M)       : is_tfu = 1
Base rate        : ~2.04%

Two sub-models (split by sub-segment):
  Model A  Donated   (is_donated=1)    → TFU  — what makes a gifter add follow-bet?
  Model B  FollowBet (is_follow_bet=1) → TFU  — what makes a follow-bettor add gifting?

Rule baseline : session_count >= 3
  (tip>0 OR follow_bet>0 is always true for this population, so it reduces to count >= 3)
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

K = 100   # top-K for Precision@K and Lift@K


# ── EDA ───────────────────────────────────────────────────────────────────────

def run_eda(df):
    gap1 = df[(df['is_donated'] == 1) | (df['is_follow_bet'] == 1)].copy()
    months = sorted(gap1['month'].unique())

    print(f"\n{'═'*60}")
    print("ANALYSIS 1 — EDA")
    print(f"Gap=1 population: {len(gap1):,} user-months across {len(months)} months")

    # 1. Monthly population + conversion rate
    overview = (
        gap1.groupby(['month'])
        .agg(
            n_total        =('cust_id', 'count'),
            n_donated      =('is_donated', 'sum'),
            n_follow_bet   =('is_follow_bet', 'sum'),
            n_tfu          =('is_tfu', 'sum'),
        )
        .assign(tfu_rate=lambda d: d['n_tfu'] / d['n_total'])
        .reset_index()
    )
    print("\n── Monthly overview ──")
    print(overview.to_string(index=False))

    # 2. Conversion rate trend by sub-segment
    fig, ax = plt.subplots(figsize=(9, 4))
    for label, mask in [('Donated', gap1['is_donated'] == 1),
                         ('Follow Bet', gap1['is_follow_bet'] == 1)]:
        rate = gap1[mask].groupby('month')['is_tfu'].mean()
        ax.plot(rate.index, rate.values, marker='o', label=label)
    ax.set_title('Analysis 1 — TFU Conversion Rate by Sub-segment')
    ax.set_ylabel('TFU Rate')
    ax.legend(); fig.tight_layout(); plt.show()

    # 3. 4-way behavioral profile (median of numeric features)
    def _label(row):
        if row['is_donated'] == 1 and row['is_tfu'] == 1:
            return 'Donated→TFU'
        if row['is_donated'] == 1:
            return 'Donated→Non'
        if row['is_follow_bet'] == 1 and row['is_tfu'] == 1:
            return 'FollowBet→TFU'
        return 'FollowBet→Non'

    gap1['profile'] = gap1.apply(_label, axis=1)
    num_cols = WATCH_FEATS[:2] + CHAT_FEATS[:2] + GIFT_FEATS[:3] + BET_FEATS[:2] + ['session_count']
    print("\n── 4-way behavioral profile (medians) ──")
    print(gap1.groupby('profile')[num_cols].median().T.to_string())

    # 4. Correlation heatmaps per sub-segment
    for seg, seg_df in [('Donated', gap1[gap1['is_donated'] == 1]),
                         ('Follow Bet', gap1[gap1['is_follow_bet'] == 1])]:
        corr_cols = num_cols + ['is_tfu']
        corr = seg_df[corr_cols].corr()
        fig, ax = plt.subplots(figsize=(10, 8))
        sns.heatmap(corr, cmap='coolwarm', center=0, annot=True, fmt='.2f',
                    linewidths=0.3, ax=ax)
        ax.set_title(f'Analysis 1 — Correlation Heatmap ({seg})')
        fig.tight_layout(); plt.show()

    # 5. Watch-bucket distribution by sub-segment
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    for ax, (seg, mask) in zip(axes, [('Donated', gap1['is_donated'] == 1),
                                        ('Follow Bet', gap1['is_follow_bet'] == 1)]):
        sub = gap1[mask].groupby(['watch_bucket', 'is_tfu']).size().unstack(fill_value=0)
        sub.plot(kind='bar', stacked=True, ax=ax, legend=(ax == axes[1]))
        ax.set_title(f'{seg} — watch bucket'); ax.set_xlabel('watch_bucket')
    fig.tight_layout(); plt.show()

    return gap1


# ── Model A: Donated → TFU ────────────────────────────────────────────────────

def run_model_a(df):
    print(f"\n{'═'*60}")
    print("ANALYSIS 1 — Model A: Donated → TFU")
    pop_filter = lambda d: d['is_donated'] == 1
    X_tr, y_tr, X_te, y_te = build_pairs(df, ML_FEATURES, 'is_tfu', pop_filter)

    # Rule: session_count >= 3 (tip>0 is always true in Donated segment)
    rule_te = (X_te['session_count'] >= 3).astype(float)

    results, models, probs = run_model_ladder(
        X_tr, y_tr, X_te, y_te,
        rule_pred_test=rule_te,
        analysis_tag='1a', k=K,
    )

    plot_roc(y_te,   probs, 'Model A: Donated → TFU  |  ROC')
    plot_lift(y_te,  probs, 'Model A: Donated → TFU  |  Lift')
    for name, m in models.items():
        plot_importance(m, ML_FEATURES, f'Model A ({name}) — feature importance')

    save_artifacts('analysis1a', results, models)
    return results, models, X_te, y_te, probs


# ── Model B: Follow Bet → TFU ─────────────────────────────────────────────────

def run_model_b(df):
    print(f"\n{'═'*60}")
    print("ANALYSIS 1 — Model B: Follow Bet → TFU")
    pop_filter = lambda d: d['is_follow_bet'] == 1
    X_tr, y_tr, X_te, y_te = build_pairs(df, ML_FEATURES, 'is_tfu', pop_filter)

    # Rule: session_count >= 3 (follow_bet>0 is always true in FollowBet segment)
    rule_te = (X_te['session_count'] >= 3).astype(float)

    results, models, probs = run_model_ladder(
        X_tr, y_tr, X_te, y_te,
        rule_pred_test=rule_te,
        analysis_tag='1b', k=K,
    )

    plot_roc(y_te,   probs, 'Model B: Follow Bet → TFU  |  ROC')
    plot_lift(y_te,  probs, 'Model B: Follow Bet → TFU  |  Lift')
    for name, m in models.items():
        plot_importance(m, ML_FEATURES, f'Model B ({name}) — feature importance')

    save_artifacts('analysis1b', results, models)
    return results, models, X_te, y_te, probs


# ── Entry point ───────────────────────────────────────────────────────────────

def run_analysis1(df=None):
    if df is None:
        print("Loading tfu_user_monthly from BigQuery …")
        df = load_table()
    print(f"Loaded {len(df):,} rows | {df['month'].nunique()} months")

    run_eda(df)
    r_a, m_a, Xte_a, yte_a, probs_a = run_model_a(df)
    r_b, m_b, Xte_b, yte_b, probs_b = run_model_b(df)

    return {'1a': (r_a, m_a), '1b': (r_b, m_b)}


if __name__ == '__main__':
    run_analysis1()
