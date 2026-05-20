"""
TFU Prediction — Forecast + Monitor Plan
=========================================
Two responsibilities:
  1. FORECAST  — apply the champion models to the current month's
                 population and produce a ranked intervention list.
  2. MONITOR   — track segment sizes and conversion rates over time,
                 alert on drift, schedule quarterly model refresh.

Run monthly as part of the scoring pipeline.
"""

from __future__ import annotations
import json
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

warnings.filterwarnings("ignore")

PROJECT      = "your_project"
TABLE        = "your_project.your_dataset.tfu_user_monthly"
RESULTS_DIR  = Path(__file__).parent / "results"
MONITOR_DIR  = Path(__file__).parent / "monitor"
MONITOR_LOG  = MONITOR_DIR / "segment_history.csv"

# ── Thresholds for drift alerts ───────────────────────────────────────────────
DRIFT_THRESHOLDS = {
    "tfu_rate_change":         0.30,   # >30% relative change month-over-month
    "population_change":       0.25,   # >25% change in segment population
    "model_auc_degradation":   0.05,   # >5 ppt AUC drop triggers re-train flag
}

TOP_K_INTERVENTION = 500   # number of users to surface per analysis per month


# ── Section header helper ─────────────────────────────────────────────────────

def section(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


# ── Data helpers ──────────────────────────────────────────────────────────────

def load_current_month(project: str = PROJECT, csv_path: str | None = None) -> pd.DataFrame:
    """Load the most recent month from tfu_user_monthly."""
    if csv_path:
        from ml.shared import load_csv
        df = load_csv(csv_path)
    else:
        from ml.shared import load_bq
        df = load_bq(project=project, months=1)

    latest = df["month"].max()
    print(f"  Scoring month: {latest}")
    return df[df["month"] == latest].copy()


def load_scores(analysis_key: str) -> pd.DataFrame:
    path = RESULTS_DIR / f"{analysis_key}_scores.csv"
    if not path.exists():
        raise FileNotFoundError(f"No scores file: {path}")
    return pd.read_csv(path, parse_dates=["month"])


# ── Forecast ──────────────────────────────────────────────────────────────────

def build_intervention_list(top_k: int = TOP_K_INTERVENTION) -> pd.DataFrame:
    """
    Merge top-K scored users from all 3 analyses into a unified
    intervention list, de-duplicated by cust_id (highest score wins).
    """
    frames = []
    for key in ["analysis1_model_a", "analysis1_model_b", "analysis2", "analysis3"]:
        try:
            df = load_scores(key)
            df = df.nlargest(top_k, "score")
            df["analysis"] = key
            frames.append(df)
        except FileNotFoundError as e:
            print(f"  [skip] {e}")

    if not frames:
        print("  No score files found — run analysis scripts first.")
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    # Keep highest-scoring row per user
    combined = (
        combined
        .sort_values("score", ascending=False)
        .drop_duplicates(subset="cust_id", keep="first")
        .reset_index(drop=True)
    )
    combined["global_rank"] = combined["score"].rank(ascending=False, method="first").astype(int)
    print(f"  Intervention list: {len(combined):,} unique users")
    return combined


def print_intervention_summary(df: pd.DataFrame):
    if df.empty:
        return
    print(f"\n  Total users flagged     : {len(df):,}")
    if "analysis" in df.columns:
        print(f"\n  Breakdown by analysis:")
        print(df["analysis"].value_counts().to_string())
    if "score" in df.columns:
        print(f"\n  Score distribution:")
        print(df["score"].describe().round(4).to_string())
    if "segment" in df.columns:
        print(f"\n  By segment:")
        print(df["segment"].value_counts().to_string())


# ── Monitor: segment tracking ─────────────────────────────────────────────────

def snapshot_segments(df: pd.DataFrame, scoring_month: date) -> pd.DataFrame:
    """
    Compute key segment KPIs for the current month:
      - TFU rate
      - Donated rate
      - FollowBet rate
      - Cold rate
      - Total active users
    Returns a single-row DataFrame to append to segment_history.
    """
    n = len(df)
    row = {
        "month":             scoring_month,
        "total_users":       n,
        "tfu_count":         df["is_tfu"].sum()          if "is_tfu"          in df.columns else np.nan,
        "donated_count":     df["is_donated"].sum()      if "is_donated"      in df.columns else np.nan,
        "follow_bet_count":  df["is_follow_bet"].sum()   if "is_follow_bet"   in df.columns else np.nan,
        "cold_count":        (df["tfu_gap"] == 2).sum()  if "tfu_gap"         in df.columns else np.nan,
    }
    row["tfu_rate"]          = row["tfu_count"]        / n if n else np.nan
    row["donated_rate"]      = row["donated_count"]    / n if n else np.nan
    row["follow_bet_rate"]   = row["follow_bet_count"] / n if n else np.nan
    row["cold_rate"]         = row["cold_count"]       / n if n else np.nan
    return pd.DataFrame([row])


def update_history(snapshot: pd.DataFrame) -> pd.DataFrame:
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    if MONITOR_LOG.exists():
        history = pd.read_csv(MONITOR_LOG, parse_dates=["month"])
        history = pd.concat([history, snapshot], ignore_index=True)
    else:
        history = snapshot.copy()
    history = history.drop_duplicates(subset="month", keep="last").sort_values("month")
    history.to_csv(MONITOR_LOG, index=False)
    print(f"  Monitor log updated → {MONITOR_LOG}  ({len(history)} months)")
    return history


# ── Monitor: drift detection ──────────────────────────────────────────────────

def check_drift(history: pd.DataFrame) -> list[str]:
    """
    Compare most recent month vs previous month.
    Returns list of alert messages (empty = no drift).
    """
    if len(history) < 2:
        return []

    curr = history.iloc[-1]
    prev = history.iloc[-2]
    alerts = []

    for col in ["tfu_rate", "donated_rate", "follow_bet_rate"]:
        if col not in history.columns:
            continue
        c, p = curr[col], prev[col]
        if pd.isna(c) or pd.isna(p) or p == 0:
            continue
        rel_change = abs(c - p) / p
        if rel_change > DRIFT_THRESHOLDS["tfu_rate_change"]:
            direction = "up" if c > p else "down"
            alerts.append(
                f"  DRIFT ALERT [{col}]: {p:.3%} → {c:.3%} "
                f"({rel_change:.0%} change {direction}) "
                f"— investigate data or model drift."
            )

    for col in ["total_users"]:
        c, p = curr.get(col, np.nan), prev.get(col, np.nan)
        if pd.isna(c) or pd.isna(p) or p == 0:
            continue
        rel_change = abs(c - p) / p
        if rel_change > DRIFT_THRESHOLDS["population_change"]:
            direction = "up" if c > p else "down"
            alerts.append(
                f"  DRIFT ALERT [{col}]: {p:,.0f} → {c:,.0f} "
                f"({rel_change:.0%} {direction}) — check data pipeline."
            )

    return alerts


# ── Monitor: plots ────────────────────────────────────────────────────────────

def plot_segment_trends(history: pd.DataFrame):
    rate_cols = [c for c in ["tfu_rate", "donated_rate", "follow_bet_rate", "cold_rate"]
                 if c in history.columns]
    if not rate_cols:
        print("  No rate columns in history — skipping trend plot.")
        return

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Segment rates over time
    ax = axes[0]
    for col in rate_cols:
        ax.plot(history["month"], history[col] * 100, marker="o", label=col.replace("_rate", ""))
    ax.set(xlabel="Month", ylabel="% of Active Users", title="Segment Rates Over Time")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.tick_params(axis="x", rotation=30)
    ax.legend()
    ax.grid(alpha=0.3)

    # Total population
    ax2 = axes[1]
    if "total_users" in history.columns:
        ax2.bar(history["month"], history["total_users"], width=20, alpha=0.7, color="#2196F3")
        ax2.set(xlabel="Month", ylabel="Active Users", title="Active Chatroom Users")
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        ax2.tick_params(axis="x", rotation=30)
        ax2.grid(alpha=0.3)

    plt.tight_layout()
    plt.show()


# ── Refresh schedule ──────────────────────────────────────────────────────────

REFRESH_SCHEDULE = """
╔══════════════════════════════════════════════════════╗
║         TFU MODEL REFRESH & MONITOR SCHEDULE         ║
╠══════════════════════════════════════════════════════╣
║  MONTHLY (automated)                                 ║
║  ├─ Rebuild tfu_user_monthly table (BigQuery SQL)    ║
║  ├─ Run forecast_monitor.py → score + intervention   ║
║  ├─ Export top-K users to CRM segmentation           ║
║  └─ Update segment_history.csv + check drift alerts  ║
╠══════════════════════════════════════════════════════╣
║  QUARTERLY (manual review)                           ║
║  ├─ Re-run all 3 analysis scripts on rolling 6-month ║
║  │   window (drop oldest month, add newest)          ║
║  ├─ Compare AUC vs. previous quarter baseline        ║
║  ├─ Re-run compare_select.py to pick new champions   ║
║  ├─ Update DRIFT_THRESHOLDS if base rate shifted     ║
║  └─ Document results in results/quarterly_log.md     ║
╠══════════════════════════════════════════════════════╣
║  TRIGGER-BASED (any month with drift alert)          ║
║  ├─ Investigate data pipeline first                  ║
║  ├─ Re-train models if AUC drops > 5ppt              ║
║  └─ Escalate if segment size drops > 25%             ║
╠══════════════════════════════════════════════════════╣
║  A/B TEST (near-term)                                ║
║  ├─ Donated segment: 50% holdout → no intervention   ║
║  ├─ Follow Bet segment: 50% holdout                  ║
║  ├─ Measure incremental TFU conversion lift          ║
║  └─ Minimum 2-month run before reading results       ║
╚══════════════════════════════════════════════════════╝
"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main(csv_path: str | None = None):
    scoring_month = date.today().replace(day=1)

    section("1. LOAD CURRENT MONTH")
    try:
        df_current = load_current_month(csv_path=csv_path)
    except Exception as e:
        print(f"  Could not load data: {e}")
        print("  Pass csv_path='path/to/file.csv' for offline mode.")
        df_current = pd.DataFrame()

    section("2. FORECAST — build intervention list")
    intervention = build_intervention_list()
    if not intervention.empty:
        print_intervention_summary(intervention)
        out = RESULTS_DIR / "intervention_list.csv"
        intervention.to_csv(out, index=False)
        print(f"\n  Saved → {out}")

    section("3. MONITOR — snapshot + drift detection")
    if not df_current.empty:
        snapshot = snapshot_segments(df_current, scoring_month)
        print(snapshot.to_string(index=False))
        history = update_history(snapshot)

        alerts = check_drift(history)
        if alerts:
            print("\n  ⚠️  DRIFT DETECTED:")
            for alert in alerts:
                print(alert)
        else:
            print("\n  ✓ No drift detected.")

        section("4. SEGMENT TREND CHARTS")
        plot_segment_trends(history)
    else:
        print("  No current-month data — skipping snapshot.")

    section("5. REFRESH SCHEDULE")
    print(REFRESH_SCHEDULE)

    section("DONE")
    print(f"  Intervention list → {RESULTS_DIR}/intervention_list.csv")
    print(f"  Monitor log       → {MONITOR_LOG}")


if __name__ == "__main__":
    import sys
    csv_arg = sys.argv[1] if len(sys.argv) > 1 else None
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    main(csv_path=csv_arg)
