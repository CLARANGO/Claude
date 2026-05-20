"""
Analysis 2 — Bettor → Follow-Bettor
====================================
Population : bettors (total_bet_count > 0, site_id != 99 already filtered)
Target     : did this bettor place a follow-bet next month?
Train      : months 1-4   |   Test : month 5
Model      : single model (no sub-model split)
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import os
import sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shared import (
    ALL_FEATURES,
    load_bq, load_csv,
    time_split, make_xy,
    rule_bettor_follow,
    run_model_ladder,
    evaluate, evaluate_rule, compare_all,
    plot_roc, plot_lift, plot_feature_importance,
    build_random_forest, build_lightgbm,
    to_user_level, monthly_unique_counts,
)

PROJECT = "nf-muses"
CSV_FALLBACK = "tfu_user_monthly.csv"


# ─────────────────────────────────────────────────────────────────────────────
# 1. DATA LOAD
# ─────────────────────────────────────────────────────────────────────────────

def load_data() -> pd.DataFrame:
    print("=" * 60)
    print("SECTION 1 — DATA LOAD")
    print("=" * 60)
    try:
        df = load_bq(project=PROJECT)
        print(f"  Loaded from BigQuery: {len(df):,} rows")
    except Exception as exc:
        print(f"  BigQuery unavailable ({exc}); falling back to CSV.")
        df = load_csv(CSV_FALLBACK)
        print(f"  Loaded from CSV: {len(df):,} rows")

    df["month"] = pd.to_datetime(df["month"])
    # Population filter: bettors only (site_id != 99 already applied upstream)
    df = df[df["total_bet_count"] > 0].copy()
    print(f"  After population filter (total_bet_count > 0): {len(df):,} rows")
    print(f"  Months in data: {sorted(df['month'].dt.to_period('M').unique())}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 2. FEATURE ENGINEERING — self-join to build lagged target
# ─────────────────────────────────────────────────────────────────────────────

def build_lagged_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """
    Join month-M features with month-(M+1) follow-bet count.
    Self-join: df_feat (month M) ← merge → df_target (month M+1).
    """
    print("=" * 60)
    print("SECTION 2 — FEATURE ENGINEERING (LAGGED TARGET)")
    print("=" * 60)

    df_feat = df.copy()
    df_feat["month_next"] = df_feat["month"] + pd.DateOffset(months=1)
    df_feat["month_next"] = df_feat["month_next"].dt.to_period("M").dt.to_timestamp()

    # Target side: only need cust_id, month, follow_bet_count
    target_col = "total_follow_bet_count"
    df_target = df[["cust_id", "month", target_col]].copy()
    df_target.columns = ["cust_id", "month_next", "follow_bet_count_next"]

    merged = df_feat.merge(df_target, on=["cust_id", "month_next"], how="inner")
    merged["target_follow_bet"] = (merged["follow_bet_count_next"] > 0).astype(int)

    print(f"  Merged dataset: {len(merged):,} rows "
          f"(month-pairs with a next-month record)")
    print(f"  Months (feature side): "
          f"{sorted(merged['month'].dt.to_period('M').unique())}")
    base_rate = merged["target_follow_bet"].mean()
    print(f"  Base rate (target_follow_bet=1): {base_rate:.4f}  ({base_rate*100:.2f}%)")

    if 0.05 <= base_rate <= 0.10:
        print("  Base rate 5-10% → logistic regression should work cleanly.")
    elif base_rate < 0.05:
        print("  Base rate <5% → class imbalance; balanced weights applied in models.")
    else:
        print("  Base rate >10% → reasonably balanced; all models applicable.")

    return merged


# ─────────────────────────────────────────────────────────────────────────────
# 3. EDA
# ─────────────────────────────────────────────────────────────────────────────

def run_eda(df: pd.DataFrame) -> None:
    print("=" * 60)
    print("SECTION 3 — EDA")
    print("=" * 60)

    target_col_raw = "total_follow_bet_count"
    df = df.copy()
    df["is_follow_bet"] = (df[target_col_raw] > 0).astype(int)
    df["month_label"] = df["month"].dt.to_period("M").astype(str)

    # ── 3a. Monthly bettor count + follow-bettor rate ─────────────────────────
    # Each (cust_id, month) row is unique by table grain, so within a single
    # month: nunique(cust_id) == row count. Use nunique to be explicit about
    # the "unique users per month" semantics.
    monthly = (
        df.groupby("month_label")
        .agg(
            bettors=("cust_id", "nunique"),
            follow_bettors=("is_follow_bet", "sum"),
        )
        .assign(follow_rate=lambda x: x["follow_bettors"] / x["bettors"])
        .reset_index()
    )
    print("\nMonthly bettor counts and follow-bettor rate:")
    print(monthly.to_string(index=False))

    fig, axes = plt.subplots(1, 2, figsize=(13, 4))
    axes[0].bar(monthly["month_label"], monthly["bettors"], color="steelblue")
    axes[0].set(title="Bettor Count by Month", xlabel="Month", ylabel="Count")
    axes[0].tick_params(axis="x", rotation=30)

    axes[1].plot(monthly["month_label"], monthly["follow_rate"],
                 marker="o", color="darkorange")
    axes[1].set(title="Follow-Bettor Rate by Month",
                xlabel="Month", ylabel="Rate")
    axes[1].yaxis.set_major_formatter(
        plt.FuncFormatter(lambda y, _: f"{y:.1%}")
    )
    axes[1].tick_params(axis="x", rotation=30)
    plt.tight_layout()
    plt.show()

    # ── 3b. Follow-bettor rate by segment dimensions (2×2 grid, per-user) ────
    # Per-user view: assign each user their MODE bucket across their months,
    # then compute % of unique users in that bucket who were EVER follow-bettors.
    seg_cols = [
        ("account_age_tier",    "Account Age Tier"),
        ("watch_bucket",        "Watch Bucket"),
        ("day_night_seg",       "Day/Night Segment"),
        ("weekday_weekend_seg", "Weekday/Weekend Segment"),
    ]
    present_segs = [(c, lbl) for c, lbl in seg_cols if c in df.columns]

    if present_segs:
        # build per-user segment assignments using mode
        user_seg = (
            df.groupby("cust_id")
            .agg({col: lambda s: s.mode().iloc[0] if not s.mode().empty else None
                  for col, _ in present_segs})
        )
        user_seg["is_follow_bet"] = user_df.set_index("cust_id")["is_follow_bet"]

        fig, axes = plt.subplots(2, 2, figsize=(13, 8))
        axes_flat = axes.flatten()
        for ax, (col, label) in zip(axes_flat, present_segs):
            rate_by_seg = (
                user_seg.groupby(col)["is_follow_bet"]
                .agg(users="size", follow_rate="mean")
                .reset_index()
            )
            ax.bar(rate_by_seg[col].astype(str),
                   rate_by_seg["follow_rate"], color="teal")
            for i, (n, _) in enumerate(zip(rate_by_seg["users"],
                                            rate_by_seg["follow_rate"])):
                ax.text(i, 0.001, f"n={n:,}", ha="center", va="bottom",
                        fontsize=8, color="white", fontweight="bold")
            ax.set(title=f"Follow-Bettor Rate by {label}",
                   xlabel=label, ylabel="Rate (unique users)")
            ax.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda y, _: f"{y:.1%}")
            )
        for ax in axes_flat[len(present_segs):]:
            ax.set_visible(False)
        plt.suptitle("Follow-Bettor Rate by Segment Dimensions  (per unique user)",
                     y=1.01)
        plt.tight_layout()
        plt.show()

    # ── 3c. Behavioral profile: follow-bettor vs regular bettor ──────────────
    # Collapse to one row per user (ever-in-segment labels, mean features)
    # so a user appearing in multiple months isn't counted multiple times.
    user_df = to_user_level(df)
    print(f"\n  Unique bettors (one row per user): {len(user_df):,}")
    print(f"  Of which EVER follow-bettor       : {int(user_df['is_follow_bet'].sum()):,} "
          f"({user_df['is_follow_bet'].mean():.2%})")

    profile_cols = [
        c for c in (
            ["total_watch_sec", "avg_watch_sec_per_session",
             "total_messages", "total_tip_count", "total_gift_usd",
             "total_bet_count", "total_bdw_bet_count",
             "total_follow_bet_count", "session_count", "breadth_score"]
        )
        if c in user_df.columns
    ]
    if profile_cols:
        profile = (
            user_df.groupby("is_follow_bet")[profile_cols]
            .median()
            .T
            .rename(columns={0: "Regular Bettor", 1: "Follow-Bettor"})
        )
        print("\nBehavioral profile (per-user median) — Follow-Bettor vs Regular Bettor:")
        print(profile.to_string())

        # Normalised bar chart (% difference from regular bettor)
        profile_norm = profile.copy()
        denom = profile_norm["Regular Bettor"].replace(0, np.nan)
        profile_norm["pct_diff"] = (
            (profile_norm["Follow-Bettor"] - profile_norm["Regular Bettor"])
            / denom * 100
        )
        fig, ax = plt.subplots(figsize=(9, 0.45 * len(profile_norm) + 1))
        colors = ["forestgreen" if v >= 0 else "tomato"
                  for v in profile_norm["pct_diff"]]
        ax.barh(profile_norm.index, profile_norm["pct_diff"], color=colors)
        ax.axvline(0, color="black", linewidth=0.8)
        ax.set(
            title="Follow-Bettor vs Regular Bettor\n(% diff from regular bettor median)",
            xlabel="% Difference",
        )
        plt.tight_layout()
        plt.show()

    # ── 3d. Correlation heatmap (per-user) ────────────────────────────────────
    numeric_cols = [c for c in ALL_FEATURES if c in user_df.columns
                    and pd.api.types.is_numeric_dtype(user_df[c])]
    heat_cols = numeric_cols + ["is_follow_bet"]
    corr = user_df[heat_cols].corr()

    fig, ax = plt.subplots(figsize=(max(10, len(heat_cols) * 0.6),
                                    max(8, len(heat_cols) * 0.55)))
    sns.heatmap(corr, annot=False, fmt=".2f", cmap="coolwarm",
                center=0, ax=ax, linewidths=0.3)
    ax.set_title("Correlation Heatmap — Numeric Features + is_follow_bet")
    plt.tight_layout()
    plt.show()

    # ── 3e. Monthly cohort stickiness ─────────────────────────────────────────
    print("\nMonthly cohort stickiness — follow-bettors in M who follow-bet in M+1:")
    df_fb = df[df["is_follow_bet"] == 1][["cust_id", "month_label", "month"]].copy()
    df_fb["month_next"] = (
        df_fb["month"] + pd.DateOffset(months=1)
    ).dt.to_period("M").astype(str)

    # Full dataset labels for checking M+1 presence
    fb_set = set(zip(df[df["is_follow_bet"] == 1]["cust_id"],
                     df[df["is_follow_bet"] == 1]["month_label"]))

    stickiness_rows = []
    for month_m, grp in df_fb.groupby("month_label"):
        n = len(grp)
        m1 = grp["month_next"].iloc[0]
        retained = sum(
            1 for cid, _ in zip(grp["cust_id"], grp["month_next"])
            if (cid, m1) in fb_set
        )
        stickiness_rows.append({
            "month_M":     month_m,
            "month_M1":    m1,
            "follow_bettors": n,
            "retained":    retained,
            "retention_rate": retained / n if n > 0 else 0.0,
        })
    stickiness_df = pd.DataFrame(stickiness_rows)
    print(stickiness_df.to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# 4. TRAIN/TEST SPLIT
# ─────────────────────────────────────────────────────────────────────────────

def split_data(
    merged: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    print("=" * 60)
    print("SECTION 4 — TRAIN/TEST SPLIT (TIME-BASED)")
    print("=" * 60)

    train_df, test_df = time_split(merged, test_month_offset=1)
    features = [c for c in ALL_FEATURES if c in merged.columns]

    def _rate(df: pd.DataFrame) -> str:
        r = df["target_follow_bet"].mean()
        return f"{r:.4f} ({r*100:.2f}%)"

    print(f"  Train: {len(train_df):,} rows | months "
          f"{sorted(train_df['month'].dt.to_period('M').unique())} "
          f"| base rate {_rate(train_df)}")
    print(f"  Test : {len(test_df):,} rows  | month  "
          f"{sorted(test_df['month'].dt.to_period('M').unique())} "
          f"| base rate {_rate(test_df)}")
    print(f"  Features used ({len(features)}): {features}")

    return train_df, test_df, features


# ─────────────────────────────────────────────────────────────────────────────
# 5. RULE BASELINE
# ─────────────────────────────────────────────────────────────────────────────

def run_rule_baseline(
    X_test: pd.DataFrame,
    y_test: pd.Series,
) -> tuple[dict, np.ndarray]:
    print("=" * 60)
    print("SECTION 5 — RULE BASELINE")
    print("=" * 60)

    rule_preds = rule_bettor_follow(X_test)
    rule_metrics = evaluate_rule("rule_bettor_follow", y_test, rule_preds)
    print(f"  Rule AUC-ROC : {rule_metrics['auc_roc']:.4f}")
    print(f"  Rule F1      : {rule_metrics['f1']:.4f}")
    return rule_metrics, rule_preds


# ─────────────────────────────────────────────────────────────────────────────
# 6. MODEL LADDER
# ─────────────────────────────────────────────────────────────────────────────

def run_models(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    base_rate: float,
) -> dict[str, np.ndarray]:
    print("=" * 60)
    print("SECTION 6 — MODEL LADDER")
    print("=" * 60)

    probs = run_model_ladder(X_train, y_train, X_test, base_rate)
    return probs


# ─────────────────────────────────────────────────────────────────────────────
# 7. EVALUATION
# ─────────────────────────────────────────────────────────────────────────────

def run_evaluation(
    probs: dict[str, np.ndarray],
    rule_preds: np.ndarray,
    rule_metrics: dict,
    y_test: pd.Series,
) -> pd.DataFrame:
    print("=" * 60)
    print("SECTION 7 — EVALUATION")
    print("=" * 60)

    all_metrics = [rule_metrics]
    for name, yp in probs.items():
        m = evaluate(name, y_test, yp)
        all_metrics.append(m)
        print(f"  {name:<20}  AUC={m['auc_roc']:.4f}  F1={m['f1']:.4f}")

    comparison = compare_all(all_metrics)
    print("\nFull comparison (sorted by AUC-ROC):")
    print(comparison.to_string())

    # ROC + Lift side-by-side
    all_probs_plot = dict(probs)
    all_probs_plot["rule_bettor_follow"] = rule_preds.astype(float)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    plot_roc(all_probs_plot, y_test,
             title="ROC — Bettor → Follow-Bettor", ax=ax1)
    plot_lift(all_probs_plot, y_test,
              title="Lift — Bettor → Follow-Bettor", ax=ax2)
    plt.suptitle("Analysis 2: Bettor → Follow-Bettor", fontsize=13)
    plt.tight_layout()
    plt.show()

    return comparison


# ─────────────────────────────────────────────────────────────────────────────
# 8. FEATURE IMPORTANCE
# ─────────────────────────────────────────────────────────────────────────────

def run_feature_importance(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    features: list[str],
) -> None:
    print("=" * 60)
    print("SECTION 8 — FEATURE IMPORTANCE")
    print("=" * 60)

    rf = build_random_forest()
    rf.fit(X_train, y_train)
    fig = plot_feature_importance(rf, features, top_n=20,
                                  title="Random Forest — Feature Importance")
    plt.tight_layout()
    plt.show()

    try:
        lgbm = build_lightgbm()
        lgbm.fit(X_train, y_train)
        fig = plot_feature_importance(lgbm, features, top_n=20,
                                      title="LightGBM — Feature Importance")
        plt.tight_layout()
        plt.show()
    except ImportError:
        print("  LightGBM not installed — skipping LightGBM importance plot.")


# ─────────────────────────────────────────────────────────────────────────────
# 9. MODEL SELECTION
# ─────────────────────────────────────────────────────────────────────────────

def select_model(comparison: pd.DataFrame) -> str:
    print("=" * 60)
    print("SECTION 9 — MODEL SELECTION")
    print("=" * 60)

    best_auc = comparison["auc_roc"].iloc[0]
    MODEL_COMPLEXITY = ["logistic", "decision_tree", "random_forest", "lightgbm"]

    winner = comparison.index[0]
    for model_name in MODEL_COMPLEXITY:
        if model_name in comparison.index:
            auc = comparison.loc[model_name, "auc_roc"]
            if best_auc - auc <= 0.02:
                winner = model_name
                break

    winner_auc = comparison.loc[winner, "auc_roc"]
    print(f"  Best AUC-ROC in ladder: {best_auc:.4f} ({comparison.index[0]})")
    print(f"  Selected model        : {winner}  (AUC={winner_auc:.4f})")
    if winner != comparison.index[0]:
        print(f"  Reasoning: {winner} is within 0.02 AUC of the best model "
              f"and is simpler / more interpretable.")
    else:
        print(f"  Reasoning: {winner} is the best-performing model outright.")
    return winner


# ─────────────────────────────────────────────────────────────────────────────
# 10. OPERATING TIERS
# ─────────────────────────────────────────────────────────────────────────────

def compute_operating_tiers(
    test_df: pd.DataFrame,
    scores: np.ndarray,
    y_test: pd.Series,
) -> pd.DataFrame:
    print("=" * 60)
    print("SECTION 10 — OPERATING TIERS")
    print("=" * 60)

    tier_df = test_df[["cust_id", "month"]].copy()
    tier_df["score"] = scores
    tier_df["target"] = y_test.values

    p90 = np.percentile(scores, 90)
    p70 = np.percentile(scores, 70)

    def assign_tier(s):
        if s >= p90:
            return "High"
        elif s >= p70:
            return "Medium"
        else:
            return "Low"

    tier_df["tier"] = tier_df["score"].apply(assign_tier)

    tier_summary = (
        tier_df.groupby("tier")
        .agg(
            count=("cust_id", "count"),
            conversion_rate=("target", "mean"),
        )
        .reindex(["High", "Medium", "Low"])
        .reset_index()
    )
    tier_summary["pct_of_total"] = (
        tier_summary["count"] / tier_summary["count"].sum() * 100
    ).round(1)

    print("  Tier definitions: High=top 10%, Medium=10-30%, Low=bottom 70%")
    print(tier_summary.to_string(index=False))

    fig, ax = plt.subplots(figsize=(7, 4))
    colors = {"High": "forestgreen", "Medium": "goldenrod", "Low": "tomato"}
    for _, row in tier_summary.iterrows():
        ax.bar(row["tier"], row["conversion_rate"],
               color=colors.get(row["tier"], "steelblue"))
    ax.set(
        title="Conversion Rate by Operating Tier\n(Bettor → Follow-Bettor)",
        xlabel="Tier", ylabel="Conversion Rate",
    )
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.1%}"))
    plt.tight_layout()
    plt.show()

    return tier_df


# ─────────────────────────────────────────────────────────────────────────────
# 11. SCORE OUTPUT
# ─────────────────────────────────────────────────────────────────────────────

def build_score_output(tier_df: pd.DataFrame) -> pd.DataFrame:
    print("=" * 60)
    print("SECTION 11 — SCORE OUTPUT")
    print("=" * 60)

    score_df = tier_df[["cust_id", "month", "score", "tier"]].copy()
    score_df["rank"] = score_df["score"].rank(ascending=False, method="first").astype(int)
    score_df = score_df.sort_values("rank").reset_index(drop=True)

    print(f"  Total scored records: {len(score_df):,}")
    print("\n  Top 10 by score:")
    print(
        score_df[["cust_id", "month", "score", "tier", "rank"]]
        .head(10)
        .to_string(index=False)
    )
    return score_df


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # 1. Load
    df_raw = load_data()

    # 2. Feature engineering + lagged target
    merged = build_lagged_dataset(df_raw)

    # 3. EDA (on full merged dataset, using month-M features + raw target)
    run_eda(df_raw)

    # 4. Train/test split
    train_df, test_df, features = split_data(merged)
    X_train, y_train = make_xy(train_df, features, "target_follow_bet")
    X_test,  y_test  = make_xy(test_df,  features, "target_follow_bet")
    base_rate = float(y_train.mean())

    # 5. Rule baseline
    rule_metrics, rule_preds = run_rule_baseline(X_test, y_test)

    # 6. Model ladder
    probs = run_models(X_train, y_train, X_test, y_test, base_rate)

    # 7. Evaluation
    comparison = run_evaluation(probs, rule_preds, rule_metrics, y_test)

    # 8. Feature importance
    run_feature_importance(X_train, y_train, features)

    # 9. Model selection
    winner = select_model(comparison)

    # 10. Operating tiers (use winning model's scores)
    winner_scores = (
        probs.get(winner, rule_preds.astype(float))
    )
    tier_df = compute_operating_tiers(test_df, winner_scores, y_test)

    # 11. Score output
    score_df = build_score_output(tier_df)

    print("=" * 60)
    print("DONE — Analysis 2: Bettor → Follow-Bettor")
    print("=" * 60)


if __name__ == "__main__":
    main()
