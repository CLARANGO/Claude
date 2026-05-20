"""
Analysis 3 — Watcher → Tipper
==============================
Population : if_watch == 1  (broadest of the three analyses — least sparse,
                              most stable model)
Target     : (tip_count + box_count + wheel_count) > 0 next month
             i.e. did this watcher gift anything in the following month?
Train      : months 1-4   Test: month 5   (TIME-BASED — never shuffle)
"""

from __future__ import annotations

import os
import warnings

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from shared import (
    ALL_FEATURES,
    load_bq,
    load_csv,
    make_xy,
    time_split,
    evaluate,
    evaluate_rule,
    compare_all,
    plot_roc,
    plot_lift,
    plot_feature_importance,
    run_model_ladder,
    rule_watcher_tipper,
    precision_at_k,
    lift_at_k,
)

warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT = "your_project"
CSV_FALLBACK = "tfu_user_monthly.csv"

# Segments where tipper rate AND population are both meaningful.
# Adjust based on the EDA bar chart output.
# Focus deeper EDA on segments where tipper rate AND population are both meaningful.
ACTIONABLE_WATCH_BUCKETS = [2, 3, 4]

TARGET_COL = "target_tipper"


# ── 1. Data load ──────────────────────────────────────────────────────────────

def load_data() -> pd.DataFrame:
    """Load tfu_user_monthly; fall back to CSV for offline dev."""
    try:
        print("Loading from BigQuery …")
        df = load_bq(project=PROJECT)
        print(f"  BQ rows loaded: {len(df):,}")
    except Exception as exc:
        print(f"  BQ unavailable ({exc}); falling back to CSV.")
        df = load_csv(CSV_FALLBACK)
        print(f"  CSV rows loaded: {len(df):,}")

    # Restrict to watcher population
    df = df[df["if_watch"] == 1].copy()
    print(f"  Watcher population (if_watch=1): {len(df):,} rows")
    return df


# ── 2. EDA ────────────────────────────────────────────────────────────────────

def eda(df: pd.DataFrame) -> None:
    print("=" * 60)
    print("EDA")
    print("=" * 60)

    df = df.copy()
    # Lightweight tipper indicator at current month (for EDA only)
    df["is_tipper_now"] = (
        (df["total_tip_count"].fillna(0)
         + df["total_box_count"].fillna(0)
         + df["total_wheel_count"].fillna(0)) > 0
    ).astype(int)

    months = sorted(df["month"].unique())
    print(f"  Months in data: {[str(m)[:7] for m in months]}")

    # ── 2a. Monthly watcher count + tipper rate trend ─────────────────────────
    monthly = (
        df.groupby("month")
        .agg(
            watcher_count=("cust_id", "count"),
            tipper_count=("is_tipper_now", "sum"),
        )
        .reset_index()
    )
    monthly["tipper_rate"] = monthly["tipper_count"] / monthly["watcher_count"]

    fig, ax1 = plt.subplots(figsize=(10, 5))
    ax2 = ax1.twinx()
    ax1.bar(
        monthly["month"].astype(str),
        monthly["watcher_count"],
        alpha=0.35,
        color="steelblue",
        label="Watcher count",
    )
    ax2.plot(
        monthly["month"].astype(str),
        monthly["tipper_rate"],
        marker="o",
        color="darkorange",
        linewidth=2,
        label="Tipper rate",
    )
    ax1.set_ylabel("Watcher Count")
    ax2.set_ylabel("Tipper Rate")
    ax1.set_xlabel("Month")
    ax2.set_ylim(0, max(monthly["tipper_rate"].max() * 1.4, 0.1))
    ax1.set_title("Monthly Watcher Count & Tipper Rate (6-month window)")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")
    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.show()

    # ── 2b. KEY CHART: Tipper rate by watch_bucket ────────────────────────────
    # Focus deeper EDA on segments where tipper rate AND population are both meaningful
    wb_stats = (
        df.groupby("watch_bucket")
        .agg(
            population=("cust_id", "count"),
            tipper_count=("is_tipper_now", "sum"),
        )
        .reset_index()
    )
    wb_stats["tipper_rate"] = wb_stats["tipper_count"] / wb_stats["population"]

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(
        wb_stats["watch_bucket"].astype(str),
        wb_stats["tipper_rate"],
        color=["#c0c0c0" if b not in ACTIONABLE_WATCH_BUCKETS else "#2196F3"
               for b in wb_stats["watch_bucket"]],
        edgecolor="white",
        linewidth=1.2,
    )
    for bar, (_, row) in zip(bars, wb_stats.iterrows()):
        ax.annotate(
            f"n={row['population']:,}",
            xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
            xytext=(0, 6),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    ax.set_xlabel("watch_bucket (1=lowest, 4=highest engagement)")
    ax.set_ylabel("Tipper Rate (current month)")
    ax.set_title("Tipper Rate by watch_bucket Segment\n(blue = actionable segments)")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.1%}"))
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.show()

    print("\nwatch_bucket tipper-rate summary:")
    print(wb_stats.to_string(index=False))
    print(f"\nActionable watch buckets config: ACTIONABLE_WATCH_BUCKETS = {ACTIONABLE_WATCH_BUCKETS}")

    # ── 2c. Behavioral profile: tipper vs non-tipper in actionable segments ───
    actionable = df[df["watch_bucket"].isin(ACTIONABLE_WATCH_BUCKETS)].copy()
    profile_features = [
        # watch
        "total_watch_sec", "avg_watch_sec_per_session",
        # chat
        "total_messages", "total_chatroom_sec",
        # gifting
        "total_tip_count", "total_gift_usd",
        # betting
        "total_bet_count", "total_follow_bet_count",
        # breadth / engagement
        "breadth_score", "session_count", "distinct_streamers",
    ]
    profile_features = [c for c in profile_features if c in actionable.columns]

    profile = (
        actionable.groupby("is_tipper_now")[profile_features]
        .median()
        .T.rename(columns={0: "Non-Tipper (median)", 1: "Tipper (median)"})
    )
    print("\nBehavioral profile — actionable segments (watch_bucket in",
          ACTIONABLE_WATCH_BUCKETS, "):")
    print(profile.round(2).to_string())

    fig, ax = plt.subplots(figsize=(9, 0.55 * len(profile_features) + 1.5))
    norm = profile.apply(lambda r: r / (r.max() + 1e-9), axis=1)
    y = np.arange(len(norm))
    ax.barh(y - 0.2, norm["Non-Tipper (median)"], height=0.35,
            color="#90CAF9", label="Non-Tipper")
    ax.barh(y + 0.2, norm["Tipper (median)"], height=0.35,
            color="#1565C0", label="Tipper")
    ax.set_yticks(y)
    ax.set_yticklabels(norm.index)
    ax.set_xlabel("Normalised Median (relative to row max)")
    ax.set_title("Tipper vs Non-Tipper — Behavioral Profile\n"
                 f"(Actionable segments: watch_bucket {ACTIONABLE_WATCH_BUCKETS})")
    ax.legend()
    ax.grid(axis="x", alpha=0.3)
    plt.tight_layout()
    plt.show()

    # ── 2d. Correlation heatmap: numeric features + is_tipper_now ─────────────
    heat_cols = [c for c in ALL_FEATURES if c in df.columns] + ["is_tipper_now"]
    corr = df[heat_cols].fillna(0).corr()

    fig, ax = plt.subplots(figsize=(14, 11))
    mask = np.triu(np.ones_like(corr, dtype=bool))
    sns.heatmap(
        corr,
        mask=mask,
        annot=True,
        fmt=".2f",
        cmap="coolwarm",
        center=0,
        linewidths=0.4,
        ax=ax,
        annot_kws={"size": 7},
    )
    ax.set_title("Correlation Heatmap — Watcher Population Features + target_tipper")
    plt.tight_layout()
    plt.show()

    # ── 2e. Tipper rate trend by watch_bucket segment + cohort stickiness ─────
    trend = (
        df.groupby(["month", "watch_bucket"])
        .agg(
            watcher_count=("cust_id", "count"),
            tipper_count=("is_tipper_now", "sum"),
        )
        .reset_index()
    )
    trend["tipper_rate"] = trend["tipper_count"] / trend["watcher_count"]

    fig, ax = plt.subplots(figsize=(11, 5))
    for bucket, grp in trend.groupby("watch_bucket"):
        style = "-o" if bucket in ACTIONABLE_WATCH_BUCKETS else "--s"
        ax.plot(
            grp["month"].astype(str),
            grp["tipper_rate"],
            style,
            label=f"bucket {bucket}",
            linewidth=2 if bucket in ACTIONABLE_WATCH_BUCKETS else 1,
        )
    ax.set_xlabel("Month")
    ax.set_ylabel("Tipper Rate")
    ax.set_title("Tipper Rate Trend by watch_bucket (6 months)\n"
                 "Solid = actionable segments")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.1%}"))
    ax.legend(title="watch_bucket")
    ax.grid(alpha=0.3)
    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.show()

    # Cohort stickiness: % of month-M tippers who also tip in month M+1
    # (requires consecutive-month pairs)
    print("\n  [cohort stickiness computed within EDA data — uses current-month flag only]")
    stickiness_rows = []
    for i in range(len(months) - 1):
        m_now, m_next = months[i], months[i + 1]
        now_tippers = set(
            df[(df["month"] == m_now) & (df["is_tipper_now"] == 1)]["cust_id"]
        )
        next_tippers = set(
            df[(df["month"] == m_next) & (df["is_tipper_now"] == 1)]["cust_id"]
        )
        if now_tippers:
            repeat = len(now_tippers & next_tippers) / len(now_tippers)
            stickiness_rows.append(
                {"transition": f"{str(m_now)[:7]}→{str(m_next)[:7]}",
                 "repeat_tipper_rate": round(repeat, 4),
                 "tipper_cohort_size": len(now_tippers)}
            )
    if stickiness_rows:
        print("\nCohort stickiness (month-over-month repeat-tipper rate):")
        print(pd.DataFrame(stickiness_rows).to_string(index=False))


# ── 3. Feature engineering ────────────────────────────────────────────────────

def build_model_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Self-join: align month-M features with month-M+1 gifting outcome.
    target_tipper = 1 if the user tipped/boxed/wheeled in the NEXT month.
    """
    print("=" * 60)
    print("FEATURE ENGINEERING — self-join M → M+1")
    print("=" * 60)

    df = df.sort_values(["cust_id", "month"]).copy()
    months = sorted(df["month"].unique())

    rows = []
    for i in range(len(months) - 1):
        m_curr = months[i]
        m_next = months[i + 1]

        curr = df[df["month"] == m_curr].copy()
        nxt = df[df["month"] == m_next][
            ["cust_id",
             "total_tip_count",
             "total_box_count",
             "total_wheel_count"]
        ].rename(columns={
            "total_tip_count":   "total_tip_count_M1",
            "total_box_count":   "total_box_count_M1",
            "total_wheel_count": "total_wheel_count_M1",
        })

        merged = curr.merge(nxt, on="cust_id", how="inner")
        merged["target_month"] = m_next  # month the target was measured
        rows.append(merged)

    model_df = pd.concat(rows, ignore_index=True)

    # target_tipper: gifted anything in the next month
    model_df[TARGET_COL] = (
        (model_df["total_tip_count_M1"].fillna(0)
         + model_df["total_box_count_M1"].fillna(0)
         + model_df["total_wheel_count_M1"].fillna(0)) > 0
    ).astype(int)

    print(f"  Model rows: {len(model_df):,}")
    print(f"  Overall base rate: {model_df[TARGET_COL].mean():.3%}")
    return model_df


# ── 4. Train/test split ───────────────────────────────────────────────────────

def split_data(model_df: pd.DataFrame):
    print("=" * 60)
    print("TRAIN / TEST SPLIT  (time-based — never shuffle)")
    print("=" * 60)

    # time_split uses 'month' column; for model_df that is the feature month
    train_df, test_df = time_split(model_df, test_month_offset=1)

    features = [c for c in ALL_FEATURES if c in model_df.columns]

    X_train, y_train = make_xy(train_df, features, TARGET_COL)
    X_test,  y_test  = make_xy(test_df,  features, TARGET_COL)

    train_rate = y_train.mean()
    test_rate  = y_test.mean()

    print(f"  Train: {len(X_train):,} rows  |  base rate: {train_rate:.3%}")
    print(f"  Test : {len(X_test):,}  rows  |  base rate: {test_rate:.3%}")
    print(f"  Features used: {len(features)}")
    # This is the largest and most stable of the three analysis populations.
    # The base rate is typically healthier → LightGBM should work well here.
    print("  NOTE: Watcher population is the largest / most stable of the 3 analyses.")
    print("        Healthy base rate → LightGBM included in model ladder by default.")

    return train_df, test_df, X_train, y_train, X_test, y_test, features


# ── 5. Rule baseline ──────────────────────────────────────────────────────────

def run_rule_baseline(X_test, y_test) -> dict:
    print("=" * 60)
    print("RULE BASELINE — watch_bucket >= 3")
    print("=" * 60)

    rule_preds = rule_watcher_tipper(X_test)
    metrics = evaluate_rule("rule_watcher_tipper", y_test, rule_preds)
    print(f"  AUC-ROC : {metrics['auc_roc']:.4f}")
    print(f"  F1      : {metrics['f1']:.4f}")
    print(f"  Precision@10% : {metrics.get('precision@10pct', 'n/a'):.4f}")
    print(f"  Lift@10%      : {metrics.get('lift@10pct', 'n/a'):.4f}")
    return metrics


# ── 6. Model ladder ───────────────────────────────────────────────────────────

def train_models(X_train, y_train, X_test, base_rate: float) -> dict:
    print("=" * 60)
    print("MODEL LADDER")
    print("=" * 60)

    # Base rate is healthy for watcher population — always include LightGBM
    probs = run_model_ladder(
        X_train, y_train, X_test,
        base_rate=base_rate,
        include_lgbm=True,          # explicitly include: base rate is healthy here
    )
    return probs


# ── 7. Evaluation ─────────────────────────────────────────────────────────────

def evaluate_all(probs: dict, rule_metrics: dict, y_test) -> tuple[list[dict], str]:
    print("=" * 60)
    print("EVALUATION")
    print("=" * 60)

    all_metrics = [rule_metrics]
    for name, yp in probs.items():
        m = evaluate(name, y_test, yp)
        all_metrics.append(m)
        print(f"  {name:20s}  AUC={m['auc_roc']:.4f}  "
              f"P@10%={m.get('precision@10pct', 0):.4f}  "
              f"Lift@10%={m.get('lift@10pct', 0):.2f}x")

    summary = compare_all(all_metrics)
    print("\nModel comparison (sorted by AUC-ROC):")
    print(summary.to_string())

    winner = summary.index[0]
    print(f"\n  Best model: {winner}")

    # ROC curves
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    plot_roc(probs, y_test, title="ROC — Watcher → Tipper", ax=axes[0])
    plot_lift(probs, y_test, title="Lift Curve — Watcher → Tipper", ax=axes[1])
    plt.suptitle("Analysis 3: Watcher → Tipper", fontsize=13, y=1.01)
    plt.tight_layout()
    plt.show()

    return all_metrics, winner


# ── 8. Feature importance ─────────────────────────────────────────────────────

def feature_importance(X_train, y_train, features: list[str]) -> dict:
    print("=" * 60)
    print("FEATURE IMPORTANCE — RF and LightGBM")
    print("=" * 60)

    from shared import build_random_forest, build_lightgbm

    trained: dict = {}

    rf = build_random_forest()
    rf.fit(X_train, y_train)
    trained["random_forest"] = rf
    fig = plot_feature_importance(rf, features, top_n=20, title="RF Feature Importance")
    plt.show()

    rf_imp = pd.Series(rf.feature_importances_, index=features).nlargest(5)
    print("\n  RF top-5 features:")
    print(rf_imp.round(4).to_string())
    if "watch_bucket" in rf_imp.index:
        print("  ✓ watch_bucket is a top RF feature — expected for watcher analysis.")

    try:
        lgbm = build_lightgbm()
        lgbm.fit(X_train, y_train)
        trained["lightgbm"] = lgbm
        fig = plot_feature_importance(lgbm, features, top_n=20,
                                      title="LightGBM Feature Importance")
        plt.show()
        lgbm_imp = pd.Series(lgbm.feature_importances_, index=features).nlargest(5)
        print("\n  LightGBM top-5 features:")
        print(lgbm_imp.round(4).to_string())
        if "watch_bucket" in lgbm_imp.index:
            print("  ✓ watch_bucket is a top LightGBM feature — as expected.")
    except Exception as exc:
        print(f"  LightGBM importance skipped: {exc}")

    return trained


# ── 9. Model selection ────────────────────────────────────────────────────────

def select_model(winner: str, probs: dict, trained_models: dict,
                 X_train, y_train, features: list[str]):
    print("=" * 60)
    print(f"MODEL SELECTION — winner: {winner.upper()}")
    print("=" * 60)

    if winner == "lightgbm":
        print("  LightGBM selected — appropriate here because:")
        print("  • Watcher population is the LARGEST of the 3 analyses (least sparse).")
        print("  • Base rate is healthy → boosting can exploit class signal fully.")
        print("  • Gradient boosting handles non-linearities in watch/chat features well.")

    # Retrieve or re-fit the winning model
    if winner in trained_models:
        best_model = trained_models[winner]
    else:
        from shared import MODEL_BUILDERS
        best_model = MODEL_BUILDERS[winner]()
        best_model.fit(X_train, y_train)

    return best_model


# ── 10. Watch segment deep-dive ───────────────────────────────────────────────

def segment_deep_dive(best_model, test_df: pd.DataFrame, features: list[str],
                      y_test: pd.Series) -> None:
    print("=" * 60)
    print("WATCH SEGMENT DEEP-DIVE — Precision@10% and Lift@10% by watch_bucket")
    print("=" * 60)
    print("  (Helps operators target high-watch users efficiently)")

    X_test_full = test_df[[c for c in features if c in test_df.columns]].fillna(0)
    scores = best_model.predict_proba(X_test_full)[:, 1]
    test_df = test_df.copy()
    test_df["_score"] = scores
    test_df["_target"] = y_test.values

    rows = []
    for bucket in sorted(test_df["watch_bucket"].dropna().unique()):
        seg = test_df[test_df["watch_bucket"] == bucket]
        if len(seg) < 10:
            continue
        yt = seg["_target"].values
        yp = seg["_score"].values
        k10 = max(1, int(len(yt) * 0.10))
        rows.append({
            "watch_bucket":      int(bucket),
            "segment_size":      len(seg),
            "base_rate":         round(yt.mean(), 4),
            "precision@10pct":   round(precision_at_k(yt, yp, k10), 4),
            "lift@10pct":        round(lift_at_k(yt, yp, k10), 2),
        })

    seg_df = pd.DataFrame(rows)
    print(seg_df.to_string(index=False))

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for ax, metric, color, label in [
        (axes[0], "precision@10pct", "#1565C0", "Precision @ 10%"),
        (axes[1], "lift@10pct",      "#2E7D32", "Lift @ 10%"),
    ]:
        bars = ax.bar(
            seg_df["watch_bucket"].astype(str),
            seg_df[metric],
            color=[color if b in ACTIONABLE_WATCH_BUCKETS else "#BDBDBD"
                   for b in seg_df["watch_bucket"]],
            edgecolor="white",
        )
        for bar, (_, row) in zip(bars, seg_df.iterrows()):
            ax.annotate(
                f"n={row['segment_size']:,}",
                xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center", va="bottom", fontsize=8,
            )
        ax.set_xlabel("watch_bucket")
        ax.set_ylabel(label)
        ax.set_title(f"{label} by watch_bucket")
        ax.grid(axis="y", alpha=0.3)

    plt.suptitle("Segment Deep-Dive — Model targeting performance per watch_bucket",
                 fontsize=12)
    plt.tight_layout()
    plt.show()


# ── 11. Score output ──────────────────────────────────────────────────────────

def score_output(best_model, test_df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    print("=" * 60)
    print("SCORE OUTPUT")
    print("=" * 60)

    X_score = test_df[[c for c in features if c in test_df.columns]].fillna(0)
    scores = best_model.predict_proba(X_score)[:, 1]

    output = test_df[["cust_id", "month"]].copy()
    output["score"]        = scores
    output["watch_bucket"] = test_df["watch_bucket"].values
    output["rank"]         = output["score"].rank(ascending=False, method="first").astype(int)
    output = output.sort_values("rank").reset_index(drop=True)

    print("Top 10 scored watchers:")
    print(output.head(10).to_string(index=False))
    return output


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("ANALYSIS 3 — WATCHER → TIPPER")
    print("=" * 60)

    # 1. Load
    df = load_data()

    # 2. EDA
    eda(df)

    # 3. Feature engineering
    model_df = build_model_data(df)

    # 4. Split
    train_df, test_df, X_train, y_train, X_test, y_test, features = split_data(model_df)

    base_rate = float(y_train.mean())

    # 5. Rule baseline
    rule_metrics = run_rule_baseline(X_test, y_test)

    # 6. Model ladder
    probs = train_models(X_train, y_train, X_test, base_rate)

    # 7. Evaluation
    all_metrics, winner = evaluate_all(probs, rule_metrics, y_test)

    # 8. Feature importance
    trained_models = feature_importance(X_train, y_train, features)

    # 9. Model selection
    best_model = select_model(winner, probs, trained_models, X_train, y_train, features)

    # 10. Watch segment deep-dive
    segment_deep_dive(best_model, test_df, features, y_test)

    # 11. Score output
    scored = score_output(best_model, test_df, features)

    print("=" * 60)
    print("DONE")
    print("=" * 60)
    return scored


if __name__ == "__main__":
    main()
