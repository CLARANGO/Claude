"""
TFU Prediction — Model Comparison & Selection
Aggregates results from all 3 analyses, produces a side-by-side
comparison table and selects the champion model per analysis.

Run AFTER the three analysis scripts have been executed and have
persisted their results to results/.
"""

from __future__ import annotations
import json
import os
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"


# ── Load persisted results ────────────────────────────────────────────────────

def load_metrics(analysis_key: str) -> pd.DataFrame:
    """Load the metrics CSV written by each analysis script."""
    path = RESULTS_DIR / f"{analysis_key}_metrics.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"Results not found: {path}\n"
            f"Run ml/analysis*.py scripts first to generate results."
        )
    return pd.read_csv(path, index_col="model")


def load_scores(analysis_key: str) -> pd.DataFrame:
    path = RESULTS_DIR / f"{analysis_key}_scores.csv"
    if not path.exists():
        raise FileNotFoundError(f"Scores not found: {path}")
    return pd.read_csv(path, parse_dates=["month"])


# ── Comparison table ──────────────────────────────────────────────────────────

ANALYSES = {
    "analysis1_model_a": "Gap=1→TFU  [Donated sub-model]",
    "analysis1_model_b": "Gap=1→TFU  [FollowBet sub-model]",
    "analysis2":         "Bettor→FollowBettor",
    "analysis3":         "Watcher→Tipper",
}

DISPLAY_COLS = [
    "auc_roc", "f1", "precision", "recall",
    "precision@5pct", "lift@5pct",
    "precision@10pct", "lift@10pct",
]


def build_comparison_table() -> pd.DataFrame:
    frames = {}
    for key, label in ANALYSES.items():
        try:
            df = load_metrics(key)
            df.index = pd.MultiIndex.from_product([[label], df.index], names=["analysis", "model"])
            frames[key] = df
        except FileNotFoundError as e:
            print(f"  [skip] {label}: {e}")
    if not frames:
        raise RuntimeError("No results found — run the analysis scripts first.")
    return pd.concat(frames.values())[DISPLAY_COLS].round(4)


# ── Champion selection ────────────────────────────────────────────────────────

PREFERENCE_ORDER = ["logistic", "decision_tree", "random_forest", "lightgbm"]
AUC_TOLERANCE = 0.02


def select_champion(metrics: pd.DataFrame) -> str:
    """
    Pick the simplest model within AUC_TOLERANCE of the best AUC.
    Ties broken by F1.
    Rule-baseline excluded from champion consideration.
    """
    m = metrics[~metrics.index.str.startswith("rule")].copy()
    best_auc = m["auc_roc"].max()
    candidates = m[m["auc_roc"] >= best_auc - AUC_TOLERANCE]
    for name in PREFERENCE_ORDER:
        match = [i for i in candidates.index if name in i.lower()]
        if match:
            return match[0]
    return m["auc_roc"].idxmax()


def champions_summary() -> pd.DataFrame:
    rows = []
    for key, label in ANALYSES.items():
        try:
            metrics = load_metrics(key)
            champ = select_champion(metrics)
            row = metrics.loc[champ].to_dict()
            row["analysis"] = label
            row["champion_model"] = champ
            rows.append(row)
        except FileNotFoundError:
            pass
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("analysis")
    return df[["champion_model"] + DISPLAY_COLS].round(4)


# ── Plots ─────────────────────────────────────────────────────────────────────

def plot_champion_auc_comparison(summary: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(10, 4))
    colors = ["#2196F3", "#4CAF50", "#FF9800", "#9C27B0"]
    bars = ax.barh(
        summary.index,
        summary["auc_roc"],
        color=colors[: len(summary)],
        alpha=0.8,
    )
    ax.bar_label(bars, fmt="{:.3f}", padding=3)
    ax.set_xlim(0.5, 1.0)
    ax.axvline(0.5, color="grey", linestyle="--", alpha=0.5, label="Random")
    ax.set_xlabel("AUC-ROC (test set)")
    ax.set_title("Champion Model AUC-ROC by Analysis")
    ax.legend()
    plt.tight_layout()
    plt.show()


def plot_lift_comparison(summary: pd.DataFrame):
    """Bar chart of Lift@10pct for each champion."""
    if "lift@10pct" not in summary.columns:
        return
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(summary.index, summary["lift@10pct"], alpha=0.8, color="#2196F3")
    ax.axhline(1.0, color="grey", linestyle="--", alpha=0.6, label="Random (lift=1)")
    ax.set_ylabel("Lift @ top 10% of population")
    ax.set_title("Champion Model Lift@10pct by Analysis")
    ax.tick_params(axis="x", rotation=20)
    ax.legend()
    plt.tight_layout()
    plt.show()


def plot_precision_recall_grid(summary: pd.DataFrame):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    for i, (col, label) in enumerate([
        ("precision", "Precision (threshold=0.5)"),
        ("recall",    "Recall (threshold=0.5)"),
    ]):
        if col not in summary.columns:
            continue
        axes[i].bar(summary.index, summary[col], alpha=0.8)
        axes[i].set_ylabel(label)
        axes[i].set_title(label)
        axes[i].tick_params(axis="x", rotation=20)
    plt.tight_layout()
    plt.show()


# ── Main ──────────────────────────────────────────────────────────────────────

def section(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def main():
    section("1. FULL COMPARISON TABLE — all models × all analyses")
    try:
        full_table = build_comparison_table()
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 120)
        print(full_table.to_string())
    except RuntimeError as e:
        print(f"  {e}")
        return

    section("2. CHAMPION SELECTION — best model per analysis")
    summary = champions_summary()
    if summary.empty:
        print("  No champion data available.")
        return
    print(summary.to_string())

    section("3. VISUAL COMPARISON")
    plot_champion_auc_comparison(summary)
    plot_lift_comparison(summary)
    plot_precision_recall_grid(summary)

    section("4. DECISION LOG")
    for analysis, row in summary.iterrows():
        champ = row.get("champion_model", "?")
        auc   = row.get("auc_roc",    "?")
        lift  = row.get("lift@10pct", "?")
        print(f"\n  {analysis}")
        print(f"    Champion : {champ}")
        print(f"    AUC-ROC  : {auc}")
        print(f"    Lift@10% : {lift}")
        # Guidance
        if isinstance(auc, float):
            if auc >= 0.75:
                note = "Strong — deploy with confidence."
            elif auc >= 0.65:
                note = "Moderate — useful for targeting; monitor closely."
            else:
                note = "Weak — review features; may need more data."
            print(f"    Assessment: {note}")

    section("DONE — see results/ for per-analysis score files")


if __name__ == "__main__":
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    main()
