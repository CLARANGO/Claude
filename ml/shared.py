"""
TFU Prediction — shared utilities.
BigQuery loader · feature spec · train/test split
Rule baselines · model ladder · evaluation helpers
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional

# ── Feature columns ───────────────────────────────────────────────────────────

WATCH_FEATURES = [
    "total_watch_sec",
    "avg_watch_sec_per_session",
    "watch_bucket",           # ordinal 1-4
]

CHAT_FEATURES = [
    "total_messages",
    "chat_sessions",
    "total_bullet_sec",
    "total_chatroom_sec",
]

GIFTING_FEATURES = [
    "total_tip_count",
    "total_box_count",
    "total_wheel_count",
    "total_gift_usd",
]

BETTING_FEATURES = [
    "total_bet_count",
    "total_member_to",
    "total_bdw_bet_count",
    "total_follow_bet_count",
]

ENGAGEMENT_FEATURES = [
    "breadth_score",
    "session_count",
    "distinct_streamers",
]

SEGMENT_FEATURES = [
    "tfu_gap",              # 0=TFU 1=Donated|FollowBet 2=Cold
    "account_age_tier",     # ordinal 1-6
    "day_night_seg",        # 0=day 1=night 2=mixed
    "weekday_weekend_seg",  # 0=weekday 1=weekend 2=mixed
]

ALL_FEATURES = (
    WATCH_FEATURES
    + CHAT_FEATURES
    + GIFTING_FEATURES
    + BETTING_FEATURES
    + ENGAGEMENT_FEATURES
    + SEGMENT_FEATURES
)

# ── BigQuery loader ───────────────────────────────────────────────────────────

DEFAULT_TABLE          = "nf-muses.muses.tfu_user_monthly"
DEFAULT_LIFETIME_TABLE = "nf-muses.muses.tfu_user_lifetime"
DEFAULT_LOCATION       = "asia-southeast1"

# Tier → ordinal maps (real table stores these as STRING)
TIER_MAP_AGE = {
    "Newborn": 1, "Rising": 2, "Established": 3,
    "Veteran": 4, "Pioneer": 5, "Legend": 6,
}
TIER_MAP_WATCH = {
    "<15min": 1, "15-30min": 2, "30-45min": 3, ">45min": 4,
    "<15 min": 1, "15-30 min": 2, "30-45 min": 3, ">45 min": 4,
}
TIER_MAP_SESSIONS = {
    "low": 1, "medium": 2, "high": 3,
    "Low": 1, "Medium": 2, "High": 3,
}
TIME_SEG_MAP = {"day": 0, "night": 1, "mixed": 2}
DAY_SEG_MAP  = {"weekday": 0, "weekend": 1, "mixed": 2}


def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise the raw tfu_user_monthly frame:
      - rename data_month → month
      - derive is_donated / is_follow_bet / is_cold / tfu_gap / if_watch / total_gift_usd
      - map STRING tiers (watch_bucket, account_age_tier, time_segment, day_segment)
        to ordinal integers, replacing the column in place
      - add legacy aliases (session_count, day_night_seg, weekday_weekend_seg)
    """
    df = df.copy()

    # ── month alias ───────────────────────────────────────────────────────────
    if "data_month" in df.columns and "month" not in df.columns:
        df["month"] = pd.to_datetime(df["data_month"])
    elif "month" in df.columns:
        df["month"] = pd.to_datetime(df["month"])

    # ── derived: gift USD / has_gift / has_followbet ─────────────────────────
    if "total_gift_usd" not in df.columns:
        df["total_gift_usd"] = (
            df.get("total_tip_usd",   pd.Series(0, index=df.index)).fillna(0)
            + df.get("total_box_usd", pd.Series(0, index=df.index)).fillna(0)
            + df.get("total_wheel_usd", pd.Series(0, index=df.index)).fillna(0)
        )

    has_gift = (
        df.get("total_tip_count",  pd.Series(0, index=df.index)).fillna(0)
        + df.get("total_box_count",  pd.Series(0, index=df.index)).fillna(0)
        + df.get("total_wheel_count", pd.Series(0, index=df.index)).fillna(0)
    ) > 0
    has_fb = df.get("total_follow_bet_count", pd.Series(0, index=df.index)).fillna(0) > 0

    # ── segment flags ─────────────────────────────────────────────────────────
    if "is_tfu" not in df.columns:
        df["is_tfu"] = (has_gift & has_fb).astype(int)
    df["is_donated"]    = (has_gift & ~has_fb).astype(int)
    df["is_follow_bet"] = (has_fb   & ~has_gift).astype(int)
    df["is_cold"]       = (~has_gift & ~has_fb).astype(int)
    df["tfu_gap"]       = np.where(
        df["is_tfu"] == 1, 0,
        np.where(df["is_cold"] == 1, 2, 1),
    )

    # ── watch flag ────────────────────────────────────────────────────────────
    df["if_watch"] = (
        (df.get("total_watch_sec",  pd.Series(0, index=df.index)).fillna(0) > 0)
        | (df.get("sessions_count", pd.Series(0, index=df.index)).fillna(0) > 0)
    ).astype(int)

    # ── ordinal tier maps (overwrite STRING columns with int) ─────────────────
    if "account_age_tier" in df.columns and df["account_age_tier"].dtype == object:
        df["account_age_tier_label"] = df["account_age_tier"]
        df["account_age_tier"] = df["account_age_tier"].map(TIER_MAP_AGE).fillna(0).astype(int)

    if "watch_bucket" in df.columns and df["watch_bucket"].dtype == object:
        df["watch_bucket_label"] = df["watch_bucket"]
        df["watch_bucket"] = df["watch_bucket"].map(TIER_MAP_WATCH).fillna(0).astype(int)

    if "time_segment" in df.columns and df["time_segment"].dtype == object:
        df["time_segment_label"] = df["time_segment"]
        df["time_segment"] = (
            df["time_segment"].str.lower().map(TIME_SEG_MAP).fillna(2).astype(int)
        )

    if "day_segment" in df.columns and df["day_segment"].dtype == object:
        df["day_segment_label"] = df["day_segment"]
        df["day_segment"] = (
            df["day_segment"].str.lower().map(DAY_SEG_MAP).fillna(2).astype(int)
        )

    # ── legacy aliases (so existing analysis code keeps working) ──────────────
    if "sessions_count" in df.columns and "session_count" not in df.columns:
        df["session_count"] = df["sessions_count"]
    if "time_segment" in df.columns and "day_night_seg" not in df.columns:
        df["day_night_seg"] = df["time_segment"]
    if "day_segment" in df.columns and "weekday_weekend_seg" not in df.columns:
        df["weekday_weekend_seg"] = df["day_segment"]

    return df


def load_bq(
    project: str,
    table: str = DEFAULT_TABLE,
    months: int = 6,
    location: str = DEFAULT_LOCATION,
) -> pd.DataFrame:
    """Load tfu_user_monthly from BigQuery, last `months` months, preprocessed."""
    from google.cloud import bigquery
    client = bigquery.Client(project=project, location=location)
    query = f"""
        SELECT *
        FROM `{table}`
        WHERE data_month >= DATE_TRUNC(
            DATE_SUB(CURRENT_DATE(), INTERVAL {months} MONTH), MONTH
        )
        ORDER BY data_month, cust_id
    """
    df = client.query(query).to_dataframe()
    return preprocess(df)


def load_csv(path: str) -> pd.DataFrame:
    """Load from local CSV for offline dev / testing — preprocessed."""
    df = pd.read_csv(path)
    return preprocess(df)


def preprocess_lifetime(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise the raw tfu_user_lifetime frame (one row per cust_id).
      - derive has_gift / has_follow_bet / avg_gift_usd from avg_* columns
      - map STRING tiers to ordinal ints (same maps as monthly)
      - keep *_label columns for plotting

    Lifetime columns are AVG per observed month (denominator = months_observed),
    so e.g. has_gift = (avg_tip_count + avg_box_count + avg_wheel_count) > 0
    is equivalent to "ever-gifted in the window" since avg > 0 iff sum > 0.
    """
    df = df.copy()

    if "avg_gift_usd" not in df.columns:
        df["avg_gift_usd"] = (
            df.get("avg_tip_usd",    pd.Series(0, index=df.index)).fillna(0)
            + df.get("avg_box_usd",   pd.Series(0, index=df.index)).fillna(0)
            + df.get("avg_wheel_usd", pd.Series(0, index=df.index)).fillna(0)
        )

    df["has_gift"] = (
        df.get("avg_tip_count",   pd.Series(0, index=df.index)).fillna(0)
        + df.get("avg_box_count",   pd.Series(0, index=df.index)).fillna(0)
        + df.get("avg_wheel_count", pd.Series(0, index=df.index)).fillna(0)
        > 0
    ).astype(int)
    df["has_follow_bet"] = (
        df.get("avg_follow_bet_count", pd.Series(0, index=df.index)).fillna(0) > 0
    ).astype(int)

    if "account_age_tier" in df.columns and df["account_age_tier"].dtype == object:
        df["account_age_tier_label"] = df["account_age_tier"]
        df["account_age_tier"] = df["account_age_tier"].map(TIER_MAP_AGE).fillna(0).astype(int)

    if "watch_bucket" in df.columns and df["watch_bucket"].dtype == object:
        df["watch_bucket_label"] = df["watch_bucket"]
        df["watch_bucket"] = df["watch_bucket"].map(TIER_MAP_WATCH).fillna(0).astype(int)

    if "time_segment" in df.columns and df["time_segment"].dtype == object:
        df["time_segment_label"] = df["time_segment"]
        df["time_segment"] = (
            df["time_segment"].str.lower().map(TIME_SEG_MAP).fillna(2).astype(int)
        )

    if "day_segment" in df.columns and df["day_segment"].dtype == object:
        df["day_segment_label"] = df["day_segment"]
        df["day_segment"] = (
            df["day_segment"].str.lower().map(DAY_SEG_MAP).fillna(2).astype(int)
        )

    return df


def load_bq_lifetime(
    project: str,
    table: str = DEFAULT_LIFETIME_TABLE,
    location: str = DEFAULT_LOCATION,
) -> pd.DataFrame:
    """
    Load tfu_user_lifetime from BigQuery (one row per cust_id), preprocessed.
    Use this for EDA Sections 2-4 instead of in-Python to_user_level().
    """
    from google.cloud import bigquery
    client = bigquery.Client(project=project, location=location)
    df = client.query(f"SELECT * FROM `{table}`").to_dataframe()
    return preprocess_lifetime(df)


# ── EDA helpers (handle monthly-grain duplicates) ────────────────────────────

USER_SEGMENT_COLS = ["is_tfu", "is_donated", "is_follow_bet", "is_cold", "if_watch"]


def to_user_level(
    df: pd.DataFrame,
    extra_segment_cols: list[str] | None = None,
) -> pd.DataFrame:
    """
    Collapse monthly data → ONE row per cust_id.

      - Segment 0/1 flags (USER_SEGMENT_COLS + extra_segment_cols) → MAX
        i.e. ever-in-segment: 1 if user was in the segment ANY month
      - All other numeric features → MEAN across observed months
      - Adds a months_observed column

    Use this for behavioral profiles, correlation heatmaps, and any
    "one row per user" EDA. A user can carry multiple segment labels.
    """
    seg_cols = [c for c in USER_SEGMENT_COLS if c in df.columns]
    if extra_segment_cols:
        seg_cols += [c for c in extra_segment_cols if c in df.columns and c not in seg_cols]

    numeric_cols = [
        c for c in df.select_dtypes(include="number").columns
        if c not in seg_cols and c != "cust_id"
    ]

    agg = {c: "max" for c in seg_cols}
    agg.update({c: "mean" for c in numeric_cols})

    out = df.groupby("cust_id").agg(agg)
    out["months_observed"] = df.groupby("cust_id").size()
    return out.reset_index()


def monthly_unique_counts(
    df: pd.DataFrame,
    segment_cols: list[str] | None = None,
) -> pd.DataFrame:
    """
    Per-month unique-user counts + rates per segment.
    Columns: month, total_users, {seg}_count, {seg}_rate.
    Each (cust_id, month) is unique by table grain, so sum == count of users.
    """
    if segment_cols is None:
        segment_cols = [c for c in USER_SEGMENT_COLS if c in df.columns]

    rows = []
    for m, grp in df.groupby("month"):
        row = {"month": m, "total_users": grp["cust_id"].nunique()}
        for col in segment_cols:
            row[f"{col}_count"] = int(grp[col].sum())
            row[f"{col}_rate"]  = float(grp[col].mean())
        rows.append(row)
    return pd.DataFrame(rows).sort_values("month").reset_index(drop=True)


# ── Train / test split ────────────────────────────────────────────────────────

def time_split(
    df: pd.DataFrame,
    test_month_offset: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Time-based split — NEVER shuffle.
    Months 1-4 → train, month 5 → test (with 6-month window).
    test_month_offset=1 means the last distinct month becomes the test set.
    """
    months = sorted(df["month"].unique())
    cutoff = months[-test_month_offset]
    return df[df["month"] < cutoff].copy(), df[df["month"] >= cutoff].copy()


def make_xy(
    df: pd.DataFrame,
    features: list[str],
    target: str,
) -> tuple[pd.DataFrame, pd.Series]:
    cols = [c for c in features if c in df.columns]
    return df[cols].fillna(0), df[target].astype(int)


# ── Rule-based baselines ──────────────────────────────────────────────────────

def rule_gap_tfu(X: pd.DataFrame) -> np.ndarray:
    """
    Gap=1 → TFU: positive if session_count ≥ 3
    AND already has partial signal (tip OR follow_bet > 0).
    This is the interpretable floor to beat.
    """
    sess = X.get("session_count", pd.Series(0, index=X.index))
    tips = X.get("total_tip_count", pd.Series(0, index=X.index))
    fb   = X.get("total_follow_bet_count", pd.Series(0, index=X.index))
    return ((sess >= 3) & ((tips > 0) | (fb > 0))).astype(int).values


def rule_bettor_follow(X: pd.DataFrame) -> np.ndarray:
    """Bettor → FollowBettor: positive if bet_count > population median."""
    bc = X.get("total_bet_count", pd.Series(0, index=X.index))
    return (bc > bc.median()).astype(int).values


def rule_watcher_tipper(X: pd.DataFrame) -> np.ndarray:
    """Watcher → Tipper: positive if watch_bucket ≥ 3 (30+ min avg session)."""
    wb = X.get("watch_bucket", pd.Series(0, index=X.index))
    return (wb >= 3).astype(int).values


# ── Model ladder ──────────────────────────────────────────────────────────────

from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline


def build_logistic(class_weight: str = "balanced") -> Pipeline:
    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(
            class_weight=class_weight, max_iter=1000, random_state=42,
        )),
    ])


def build_decision_tree(max_depth: int = 4) -> DecisionTreeClassifier:
    return DecisionTreeClassifier(
        max_depth=max_depth, class_weight="balanced", random_state=42,
    )


def build_random_forest(n_estimators: int = 300) -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=n_estimators, max_depth=8, min_samples_leaf=20,
        class_weight="balanced", random_state=42, n_jobs=-1,
    )


def build_lightgbm():
    import lightgbm as lgb
    return lgb.LGBMClassifier(
        n_estimators=500, learning_rate=0.05, num_leaves=31,
        min_child_samples=20, class_weight="balanced",
        random_state=42, n_jobs=-1, verbose=-1,
    )


MODEL_BUILDERS = {
    "logistic":      build_logistic,
    "decision_tree": build_decision_tree,
    "random_forest": build_random_forest,
    "lightgbm":      build_lightgbm,
}


def run_model_ladder(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    base_rate: float,
    include_lgbm: bool | None = None,
) -> dict[str, np.ndarray]:
    """
    Fit and score each model in the ladder.
    Returns {model_name: test_probabilities}.
    LightGBM is skipped automatically if base_rate < 0.01
    unless include_lgbm is explicitly set.
    """
    if include_lgbm is None:
        include_lgbm = base_rate >= 0.01

    probs: dict[str, np.ndarray] = {}
    builders = {k: v for k, v in MODEL_BUILDERS.items()
                if k != "lightgbm" or include_lgbm}

    for name, builder in builders.items():
        model = builder()
        model.fit(X_train, y_train)
        probs[name] = model.predict_proba(X_test)[:, 1]
        print(f"  [✓] {name}")

    return probs


# ── Evaluation ────────────────────────────────────────────────────────────────

from sklearn.metrics import (
    roc_auc_score, f1_score,
    precision_score, recall_score, roc_curve,
)
import matplotlib.pyplot as plt


def precision_at_k(y_true: np.ndarray, y_prob: np.ndarray, k: int) -> float:
    idx = np.argsort(y_prob)[::-1][:k]
    return float(np.asarray(y_true)[idx].mean())


def lift_at_k(y_true: np.ndarray, y_prob: np.ndarray, k: int) -> float:
    base = np.asarray(y_true).mean()
    return precision_at_k(y_true, y_prob, k) / base if base > 0 else 0.0


def evaluate(
    name: str,
    y_true,
    y_prob: np.ndarray,
    k_fractions: tuple[float, ...] = (0.05, 0.10, 0.20),
) -> dict:
    y_true = np.asarray(y_true)
    y_pred = (y_prob >= 0.5).astype(int)
    n = len(y_true)
    row: dict = {
        "model":     name,
        "auc_roc":   roc_auc_score(y_true, y_prob),
        "f1":        f1_score(y_true, y_pred, zero_division=0),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall":    recall_score(y_true, y_pred, zero_division=0),
    }
    for frac in k_fractions:
        k = max(1, int(n * frac))
        row[f"precision@{int(frac*100)}pct"] = precision_at_k(y_true, y_prob, k)
        row[f"lift@{int(frac*100)}pct"]      = lift_at_k(y_true, y_prob, k)
    return row


def evaluate_rule(name: str, y_true, y_pred_binary: np.ndarray) -> dict:
    """Evaluate a rule-based model that returns 0/1 (no probabilities)."""
    y_true = np.asarray(y_true)
    # use pred as proxy probability for AUC
    return evaluate(name, y_true, y_pred_binary.astype(float))


def compare_all(metrics_list: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(metrics_list).set_index("model")
    return df.sort_values("auc_roc", ascending=False).round(4)


# ── Plots ─────────────────────────────────────────────────────────────────────

def plot_roc(probs: dict[str, np.ndarray], y_true, title: str = "ROC", ax=None):
    if ax is None:
        _, ax = plt.subplots(figsize=(7, 6))
    for name, yp in probs.items():
        fpr, tpr, _ = roc_curve(y_true, yp)
        auc = roc_auc_score(y_true, yp)
        ax.plot(fpr, tpr, label=f"{name}  AUC={auc:.3f}")
    ax.plot([0, 1], [0, 1], "k--", alpha=0.4)
    ax.set(xlabel="FPR", ylabel="TPR", title=title)
    ax.legend(loc="lower right"); ax.grid(alpha=0.3)
    return ax


def plot_lift(probs: dict[str, np.ndarray], y_true, title: str = "Lift Curve", ax=None):
    if ax is None:
        _, ax = plt.subplots(figsize=(8, 5))
    y_true = np.asarray(y_true)
    n = len(y_true)
    xs = np.linspace(0.01, 1.0, 100)
    for name, yp in probs.items():
        lifts = [lift_at_k(y_true, yp, max(1, int(x * n))) for x in xs]
        ax.plot(xs * 100, lifts, label=name)
    ax.axhline(1.0, color="grey", linestyle="--", alpha=0.6, label="Random")
    ax.set(xlabel="% Population Targeted", ylabel="Lift", title=title)
    ax.legend(); ax.grid(alpha=0.3)
    return ax


def plot_feature_importance(model, feature_names: list[str], top_n: int = 20, title: str = ""):
    """Works for RF and LightGBM (both expose feature_importances_)."""
    import seaborn as sns
    clf = model.named_steps["clf"] if hasattr(model, "named_steps") else model
    imp = pd.Series(clf.feature_importances_, index=feature_names).nlargest(top_n)
    fig, ax = plt.subplots(figsize=(8, 0.4 * top_n + 1))
    sns.barplot(x=imp.values, y=imp.index, ax=ax)
    ax.set(title=title or "Feature Importance", xlabel="Importance")
    plt.tight_layout()
    return fig
