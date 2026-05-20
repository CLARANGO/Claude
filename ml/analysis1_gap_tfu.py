"""
Analysis 1 — Gap=1 → TFU
=========================
Population: users who are one step away from TFU (gifter OR follow-bettor, not both).

Model A: is_donated==1 only  → predict is_tfu next month
Model B: is_follow_bet==1 only → predict is_tfu next month

Train: months 1-4  |  Test: month 5  (TIME-BASED, never shuffle)
"""

from __future__ import annotations

import sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from shared import (
    ALL_FEATURES,
    load_bq,
    load_csv,
    time_split,
    make_xy,
    rule_gap_tfu,
    evaluate,
    evaluate_rule,
    compare_all,
    run_model_ladder,
    plot_roc,
    plot_lift,
    plot_feature_importance,
    build_random_forest,
    build_lightgbm,
)

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT = "your_project"
CSV_PATH = "tfu_user_monthly.csv"   # fallback for offline dev
TARGET   = "is_tfu"
MONTHS   = 6
INTERPRETABILITY_THRESHOLD = 0.02  # prefer simpler model if AUC within this margin


# ── Helpers ───────────────────────────────────────────────────────────────────

def section(title: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_split_stats(name: str, train: pd.DataFrame, test: pd.DataFrame) -> None:
    br_train = train[TARGET].mean()
    br_test  = test[TARGET].mean()
    print(f"  {name}")
    print(f"    Train : {len(train):>6,} rows | base rate {br_train:.4f} ({br_train*100:.2f}%)")
    print(f"    Test  : {len(test):>6,}  rows | base rate {br_test:.4f} ({br_test*100:.2f}%)")


def select_winner(metrics_list: list[dict]) -> str:
    """
    Pick model with highest AUC-ROC while F1 > random (> 0).
    Prefer logistic > decision_tree > random_forest > lightgbm
    when AUC is within INTERPRETABILITY_THRESHOLD of the best.
    """
    df = pd.DataFrame(metrics_list).set_index("model")
    df = df[df["f1"] > 0]
    if df.empty:
        return metrics_list[0]["model"]

    best_auc = df["auc_roc"].max()
    candidates = df[df["auc_roc"] >= best_auc - INTERPRETABILITY_THRESHOLD]

    preference = ["logistic", "decision_tree", "random_forest", "lightgbm"]
    for preferred in preference:
        if preferred in candidates.index:
            return preferred
    return candidates["auc_roc"].idxmax()


def score_output(
    model,
    X_test: pd.DataFrame,
    test_df: pd.DataFrame,
    segment: str,
) -> pd.DataFrame:
    """Score the test set; return cust_id, month, score, rank, segment."""
    scores = model.predict_proba(X_test)[:, 1]
    out = test_df[["cust_id", "month"]].copy()
    out["score"]   = scores
    out["segment"] = segment
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    out["rank"] = out.index + 1
    return out[["cust_id", "month", "score", "rank", "segment"]]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:

    # ── 1. Data load ─────────────────────────────────────────────────────────
    section("1. DATA LOAD")

    try:
        print(f"  Loading from BigQuery: project={PROJECT}")
        raw = load_bq(project=PROJECT)
        print(f"  Loaded {len(raw):,} rows from BigQuery.")
    except Exception as exc:
        print(f"  BigQuery unavailable ({exc}); falling back to CSV: {CSV_PATH}")
        raw = load_csv(CSV_PATH)
        print(f"  Loaded {len(raw):,} rows from CSV.")

    print(f"  Columns : {list(raw.columns)}")
    print(f"  Months  : {sorted(raw['month'].unique())}")
    print(f"  Total users (row count): {len(raw):,}")

    # Population: gap=1 (donated OR follow_bet, not both)
    pop = raw[(raw["is_donated"] == 1) | (raw["is_follow_bet"] == 1)].copy()
    print(f"\n  Gap=1 population (donated OR follow_bet): {len(pop):,} rows")

    # Sub-populations
    pop_a = pop[pop["is_donated"] == 1].copy()    # Model A: gifter only
    pop_b = pop[pop["is_follow_bet"] == 1].copy() # Model B: follow-bettor only
    print(f"  Model A (donated==1)     : {len(pop_a):,} rows")
    print(f"  Model B (is_follow_bet==1): {len(pop_b):,} rows")

    # ── 2. EDA ───────────────────────────────────────────────────────────────
    section("2. EDA — BASE RATES & MONTHLY TREND")

    br_a = pop_a[TARGET].mean()
    br_b = pop_b[TARGET].mean()
    print(f"\n  Base rate — Model A (donated → TFU)    : {br_a:.4f}  ({br_a*100:.2f}%)")
    print(f"  Base rate — Model B (follow_bet → TFU) : {br_b:.4f}  ({br_b*100:.2f}%)")

    print("\n  Monthly conversion rate (gap=1 population):")
    for segment_label, sub_df in [("Model A (donated)", pop_a), ("Model B (follow_bet)", pop_b)]:
        monthly = (
            sub_df.groupby("month")[TARGET]
            .agg(count="count", conversions="sum")
            .assign(rate=lambda d: d["conversions"] / d["count"])
        )
        print(f"\n    {segment_label}")
        print(monthly.to_string())

    # ── 3. Feature engineering note ──────────────────────────────────────────
    section("3. FEATURE ENGINEERING")
    print("  The table already contains M-1 lag features.")
    print("  Features used for prediction:")
    feat_cols = [c for c in ALL_FEATURES if c in pop.columns]
    missing   = [c for c in ALL_FEATURES if c not in pop.columns]
    print(f"    Available : {feat_cols}")
    if missing:
        print(f"    Missing   : {missing}  (will be filled with 0)")

    # ── 4. Train / test split ─────────────────────────────────────────────────
    section("4. TRAIN / TEST SPLIT  (time-based, never shuffle)")

    # Filter first, then split — for each sub-model independently
    train_a, test_a = time_split(pop_a)
    train_b, test_b = time_split(pop_b)

    print_split_stats("Model A (donated → TFU)",     train_a, test_a)
    print_split_stats("Model B (follow_bet → TFU)",  train_b, test_b)

    X_train_a, y_train_a = make_xy(train_a, ALL_FEATURES, TARGET)
    X_test_a,  y_test_a  = make_xy(test_a,  ALL_FEATURES, TARGET)
    X_train_b, y_train_b = make_xy(train_b, ALL_FEATURES, TARGET)
    X_test_b,  y_test_b  = make_xy(test_b,  ALL_FEATURES, TARGET)

    # ── 5. Rule baseline ─────────────────────────────────────────────────────
    section("5. RULE BASELINE  (rule_gap_tfu)")

    rule_preds_a = rule_gap_tfu(X_test_a)
    rule_preds_b = rule_gap_tfu(X_test_b)

    rule_metrics_a = evaluate_rule("rule_gap_tfu", y_test_a, rule_preds_a)
    rule_metrics_b = evaluate_rule("rule_gap_tfu", y_test_b, rule_preds_b)

    print(f"\n  Model A rule AUC-ROC : {rule_metrics_a['auc_roc']:.4f}")
    print(f"  Model B rule AUC-ROC : {rule_metrics_b['auc_roc']:.4f}")

    # ── 6. Model A: Donated → TFU ────────────────────────────────────────────
    section("6. MODEL A — Donated → TFU")
    print("  Fitting model ladder …")

    probs_a = run_model_ladder(
        X_train_a, y_train_a,
        X_test_a,
        base_rate=float(y_train_a.mean()),
    )

    # ── 7. Model B: Follow Bet → TFU ─────────────────────────────────────────
    section("7. MODEL B — Follow Bet → TFU")
    print("  Fitting model ladder …")

    probs_b = run_model_ladder(
        X_train_b, y_train_b,
        X_test_b,
        base_rate=float(y_train_b.mean()),
    )

    # ── 8. Evaluation ────────────────────────────────────────────────────────
    section("8. EVALUATION")

    # Model A
    metrics_a = [rule_metrics_a] + [
        evaluate(name, y_test_a, yp) for name, yp in probs_a.items()
    ]
    print("\n  Model A — Donated → TFU:")
    print(compare_all(metrics_a).to_string())

    # Model B
    metrics_b = [rule_metrics_b] + [
        evaluate(name, y_test_b, yp) for name, yp in probs_b.items()
    ]
    print("\n  Model B — Follow Bet → TFU:")
    print(compare_all(metrics_b).to_string())

    # ROC + Lift side-by-side — Model A
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle("Model A: Donated → TFU", fontsize=14)
    plot_roc( {**probs_a, "rule": rule_preds_a.astype(float)},
              y_test_a, title="ROC Curve", ax=axes[0])
    plot_lift({**probs_a, "rule": rule_preds_a.astype(float)},
              y_test_a, title="Lift Curve", ax=axes[1])
    plt.tight_layout()
    plt.show()

    # ROC + Lift side-by-side — Model B
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle("Model B: Follow Bet → TFU", fontsize=14)
    plot_roc( {**probs_b, "rule": rule_preds_b.astype(float)},
              y_test_b, title="ROC Curve", ax=axes[0])
    plot_lift({**probs_b, "rule": rule_preds_b.astype(float)},
              y_test_b, title="Lift Curve", ax=axes[1])
    plt.tight_layout()
    plt.show()

    # ── 9. Feature importance ────────────────────────────────────────────────
    section("9. FEATURE IMPORTANCE")

    feat_names = list(X_train_a.columns)

    def _refit_named(builder_fn, X_tr, y_tr):
        m = builder_fn()
        m.fit(X_tr, y_tr)
        return m

    # Model A — RF and LightGBM
    for builder_fn, label in [
        (build_random_forest, "Random Forest"),
        (build_lightgbm,      "LightGBM"),
    ]:
        try:
            m = _refit_named(builder_fn, X_train_a, y_train_a)
            fig = plot_feature_importance(
                m, feat_names, top_n=20,
                title=f"Model A (Donated → TFU) — {label}",
            )
            plt.show()
        except Exception as exc:
            print(f"  [warn] Could not plot {label} importance for Model A: {exc}")

    # Model B — RF and LightGBM
    for builder_fn, label in [
        (build_random_forest, "Random Forest"),
        (build_lightgbm,      "LightGBM"),
    ]:
        try:
            m = _refit_named(builder_fn, X_train_b, y_train_b)
            fig = plot_feature_importance(
                m, feat_names, top_n=20,
                title=f"Model B (Follow Bet → TFU) — {label}",
            )
            plt.show()
        except Exception as exc:
            print(f"  [warn] Could not plot {label} importance for Model B: {exc}")

    # ── 10. Model selection ──────────────────────────────────────────────────
    section("10. MODEL SELECTION")

    winner_a_name = select_winner([m for m in metrics_a if m["model"] != "rule_gap_tfu"])
    winner_b_name = select_winner([m for m in metrics_b if m["model"] != "rule_gap_tfu"])

    auc_a = next(m["auc_roc"] for m in metrics_a if m["model"] == winner_a_name)
    auc_b = next(m["auc_roc"] for m in metrics_b if m["model"] == winner_b_name)

    print(f"\n  Model A winner : {winner_a_name}  (AUC-ROC={auc_a:.4f})")
    print(f"  Model B winner : {winner_b_name}  (AUC-ROC={auc_b:.4f})")
    print(
        "\n  Selection rule: highest AUC-ROC with F1 > 0 (better than random)."
        "\n  When two models are within 0.02 AUC of each other, the more"
        "\n  interpretable one is preferred: logistic > DT > RF > LightGBM."
    )

    # Rebuild winning models on full train set for scoring
    from shared import MODEL_BUILDERS

    model_a = MODEL_BUILDERS[winner_a_name]()
    model_a.fit(X_train_a, y_train_a)

    model_b = MODEL_BUILDERS[winner_b_name]()
    model_b.fit(X_train_b, y_train_b)

    # ── 11. Score output ─────────────────────────────────────────────────────
    section("11. SCORE OUTPUT")

    scores_a = score_output(model_a, X_test_a, test_a, segment="donated")
    scores_b = score_output(model_b, X_test_b, test_b, segment="follow_bet")

    scores_combined = (
        pd.concat([scores_a, scores_b], ignore_index=True)
        .sort_values("score", ascending=False)
        .reset_index(drop=True)
    )
    scores_combined["rank"] = scores_combined.index + 1

    print(f"\n  Total scored rows: {len(scores_combined):,}")
    print(f"  Segment breakdown:")
    print(scores_combined["segment"].value_counts().to_string())
    print(f"\n  Top-10 by score:")
    print(scores_combined.head(10).to_string(index=False))

    return scores_combined


if __name__ == "__main__":
    main()
