"""
TFU Prediction — Colab Pipeline (end-to-end)
============================================
Source table  : nf-muses.muses.tfu_user_monthly   (schema already finalised)
Platform      : Google Colab
Pipeline      : load → preprocess → self-join (M-1 → M) → EDA →
                3 parallel analyses (rule + model ladder) →
                champion select → score export

Plan changes applied:
  - breadth_score DROPPED from ML feature set
  - total_bet_count INCLUDES total_follow_bet_count → derive
    non_follow_bet_count to avoid double-count / target leakage
  - features are taken from month M-1, targets from month M
    (self-join on cust_id)
  - data window restricted to CLOSED past months (no partial current month)
  - if_watch tightened to total_watch_sec > 0 (sessions_count is always > 0)
  - location passed via QueryJobConfig, not Client kwarg

Run the cells in order in Colab.
"""

# %% [cell 1] ── Install + auth ─────────────────────────────────────────────
# !pip -q install google-cloud-bigquery db-dtypes lightgbm seaborn

# from google.colab import auth
# auth.authenticate_user()

# %% [cell 2] ── Imports + config ────────────────────────────────────────────
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from google.cloud import bigquery
from google.cloud.bigquery import QueryJobConfig

from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    roc_auc_score, f1_score, precision_score, recall_score, roc_curve,
)

PROJECT       = "nf-muses"
TABLE         = "nf-muses.muses.tfu_user_monthly"
LOCATION      = "asia-southeast1"
MONTHS_WINDOW = 6                       # closed past months
INTERPRETABILITY_THRESHOLD = 0.02       # AUC tolerance for simpler model

# %% [cell 3] ── BigQuery loader ─────────────────────────────────────────────
def load_bq(table: str = TABLE, months: int = MONTHS_WINDOW) -> pd.DataFrame:
    """
    Pull the last `months` CLOSED months (exclude current partial month).
    The location lives on the job config, not the Client kwarg.
    """
    client = bigquery.Client(project=PROJECT)
    sql = f"""
        SELECT *
        FROM `{table}`
        WHERE data_month >= DATE_TRUNC(
                DATE_SUB(CURRENT_DATE(), INTERVAL {months} MONTH), MONTH)
          AND data_month  < DATE_TRUNC(CURRENT_DATE(), MONTH)
        ORDER BY data_month, cust_id
    """
    job = client.query(sql, job_config=QueryJobConfig(use_query_cache=True),
                       location=LOCATION)
    df = job.to_dataframe(create_bqstorage_client=False)
    print(f"  Loaded {len(df):,} rows · {df['data_month'].min()} → {df['data_month'].max()}")
    return df

# %% [cell 4] ── Preprocess (segment flags, ordinal maps, derived bet count)
TIER_MAP_AGE = {"Newborn": 1, "Rising": 2, "Established": 3,
                "Veteran": 4, "Pioneer": 5, "Legend": 6}
TIER_MAP_WATCH = {"<15min": 1, "15-30min": 2, "30-45min": 3, ">45min": 4,
                  "<15 min": 1, "15-30 min": 2, "30-45 min": 3, ">45 min": 4}
TIME_SEG_MAP = {"day": 0, "night": 1, "mixed": 2}
DAY_SEG_MAP  = {"weekday": 0, "weekend": 1, "mixed": 2}


def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["month"] = pd.to_datetime(df["data_month"])

    # Derived USD totals
    df["total_gift_usd"] = (
        df[["total_tip_usd", "total_box_usd", "total_wheel_usd"]]
        .fillna(0).sum(axis=1)
    )

    # ── Fix: total_bet_count INCLUDES follow_bet → split it out
    df["total_bet_count"]        = df["total_bet_count"].fillna(0)
    df["total_follow_bet_count"] = df["total_follow_bet_count"].fillna(0)
    df["non_follow_bet_count"]   = (
        df["total_bet_count"] - df["total_follow_bet_count"]
    ).clip(lower=0)

    # Segment flags (TFU = gift AND follow-bet)
    has_gift = (df[["total_tip_count", "total_box_count", "total_wheel_count"]]
                .fillna(0).sum(axis=1)) > 0
    has_fb   = df["total_follow_bet_count"] > 0

    if "is_tfu" not in df.columns:
        df["is_tfu"] = (has_gift & has_fb).astype(int)
    df["is_donated"]    = (has_gift & ~has_fb).astype(int)
    df["is_follow_bet"] = (has_fb   & ~has_gift).astype(int)
    df["is_cold"]       = (~has_gift & ~has_fb).astype(int)
    df["tfu_gap"]       = np.where(df["is_tfu"] == 1, 0,
                          np.where(df["is_cold"] == 1, 2, 1))

    # Fix: if_watch must be a real watch signal
    df["if_watch"] = (df["total_watch_sec"].fillna(0) > 0).astype(int)

    # Ordinal encodings
    df["account_age_tier_label"] = df["account_age_tier"]
    df["account_age_tier"] = df["account_age_tier"].map(TIER_MAP_AGE).fillna(0).astype(int)

    df["watch_bucket_label"] = df["watch_bucket"]
    df["watch_bucket"] = df["watch_bucket"].map(TIER_MAP_WATCH).fillna(0).astype(int)

    df["time_segment_label"] = df["time_segment"]
    df["time_segment"] = (df["time_segment"].astype(str).str.lower()
                            .map(TIME_SEG_MAP).fillna(2).astype(int))

    df["day_segment_label"] = df["day_segment"]
    df["day_segment"] = (df["day_segment"].astype(str).str.lower()
                           .map(DAY_SEG_MAP).fillna(2).astype(int))

    # Legacy aliases
    df["session_count"]      = df["sessions_count"]
    df["day_night_seg"]      = df["time_segment"]
    df["weekday_weekend_seg"] = df["day_segment"]
    return df

# %% [cell 5] ── Self-join M-1 → M  (the key fix) ────────────────────────────
def build_lag_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Features come from month M-1, target columns come from month M.
    Joining on (cust_id, month) where left.month + 1 == right.month.
    Returns one row per (cust_id, month_M) with `_curr` suffixes for targets.
    """
    df = df.sort_values(["cust_id", "month"]).copy()
    nxt = df[["cust_id", "month", "is_tfu", "is_donated",
              "is_follow_bet", "total_follow_bet_count",
              "total_tip_count", "total_box_count",
              "total_wheel_count", "total_gift_usd"]].copy()
    nxt["join_month"] = nxt["month"] - pd.offsets.MonthBegin(1)
    nxt = nxt.rename(columns={
        "month":                  "month_M",
        "is_tfu":                 "is_tfu_M",
        "is_donated":             "is_donated_M",
        "is_follow_bet":          "is_follow_bet_M",
        "total_follow_bet_count": "follow_bet_count_M",
        "total_tip_count":        "tip_count_M",
        "total_box_count":        "box_count_M",
        "total_wheel_count":      "wheel_count_M",
        "total_gift_usd":         "gift_usd_M",
    })
    merged = df.merge(
        nxt, left_on=["cust_id", "month"],
        right_on=["cust_id", "join_month"], how="inner",
    )
    merged["any_gift_M"] = (
        (merged["tip_count_M"] + merged["box_count_M"] + merged["wheel_count_M"]) > 0
    ).astype(int)
    print(f"  Self-join produced {len(merged):,} rows "
          f"(features M-1, targets M). Months in M-1: "
          f"{sorted(merged['month'].dt.to_period('M').unique())}")
    return merged

# %% [cell 6] ── Feature spec (breadth_score DROPPED) ────────────────────────
WATCH_FEATURES   = ["total_watch_sec", "avg_watch_sec_per_session", "watch_bucket"]
CHAT_FEATURES    = ["total_messages", "chat_sessions",
                    "total_bullet_sec", "total_chatroom_sec"]
GIFTING_FEATURES = ["total_tip_count", "total_box_count",
                    "total_wheel_count", "total_gift_usd"]
# non_follow_bet_count replaces total_bet_count to avoid follow_bet leakage
BETTING_FEATURES = ["non_follow_bet_count", "total_member_to",
                    "total_bdw_bet_count", "total_follow_bet_count"]
ENGAGEMENT_FEATURES = ["session_count", "distinct_streamers"]    # breadth_score removed
SEGMENT_FEATURES = ["tfu_gap", "account_age_tier",
                    "day_night_seg", "weekday_weekend_seg"]

ALL_FEATURES = (WATCH_FEATURES + CHAT_FEATURES + GIFTING_FEATURES
                + BETTING_FEATURES + ENGAGEMENT_FEATURES + SEGMENT_FEATURES)

# %% [cell 7] ── Split / xy helpers ──────────────────────────────────────────
def time_split(df: pd.DataFrame, month_col: str = "month"):
    """Train on all months except the last; test on the last month."""
    months = sorted(df[month_col].unique())
    assert len(months) >= 2, "Need ≥2 months for a time-based split."
    cutoff = months[-1]
    return df[df[month_col] < cutoff].copy(), df[df[month_col] == cutoff].copy()


def make_xy(df, features, target):
    cols = [c for c in features if c in df.columns]
    return df[cols].fillna(0), df[target].astype(int)

# %% [cell 8] ── Rule baselines ──────────────────────────────────────────────
def rule_gap_tfu(X):
    sess = X.get("session_count", pd.Series(0, index=X.index))
    tips = X.get("total_tip_count", pd.Series(0, index=X.index))
    fb   = X.get("total_follow_bet_count", pd.Series(0, index=X.index))
    return ((sess >= 3) & ((tips > 0) | (fb > 0))).astype(int).values


def rule_bettor_follow(X):
    bc = X.get("non_follow_bet_count", pd.Series(0, index=X.index))
    return (bc > bc.median()).astype(int).values


def rule_watcher_tipper(X):
    wb = X.get("watch_bucket", pd.Series(0, index=X.index))
    return (wb >= 3).astype(int).values

# %% [cell 9] ── Model ladder ────────────────────────────────────────────────
def build_logistic():
    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(class_weight="balanced",
                                   max_iter=1000, random_state=42)),
    ])

def build_decision_tree():
    return DecisionTreeClassifier(max_depth=4, class_weight="balanced",
                                  random_state=42)

def build_random_forest():
    return RandomForestClassifier(n_estimators=300, max_depth=8,
                                  min_samples_leaf=20, class_weight="balanced",
                                  random_state=42, n_jobs=-1)

def build_lightgbm():
    import lightgbm as lgb
    return lgb.LGBMClassifier(n_estimators=500, learning_rate=0.05,
                              num_leaves=31, min_child_samples=20,
                              class_weight="balanced", random_state=42,
                              n_jobs=-1, verbose=-1)

MODEL_BUILDERS = {
    "logistic":      build_logistic,
    "decision_tree": build_decision_tree,
    "random_forest": build_random_forest,
    "lightgbm":      build_lightgbm,
}


def run_model_ladder(X_train, y_train, X_test, base_rate, include_lgbm=None):
    if include_lgbm is None:
        include_lgbm = base_rate >= 0.01
    probs = {}
    for name, builder in MODEL_BUILDERS.items():
        if name == "lightgbm" and not include_lgbm:
            print(f"  [skip] {name} (base rate {base_rate:.2%} < 1%)")
            continue
        m = builder()
        m.fit(X_train, y_train)
        probs[name] = m.predict_proba(X_test)[:, 1]
        print(f"  [ok]  {name}")
    return probs

# %% [cell 10] ── Evaluation ─────────────────────────────────────────────────
def precision_at_k(y_true, y_prob, k):
    idx = np.argsort(y_prob)[::-1][:k]
    return float(np.asarray(y_true)[idx].mean())

def lift_at_k(y_true, y_prob, k):
    base = np.asarray(y_true).mean()
    return precision_at_k(y_true, y_prob, k) / base if base > 0 else 0.0

def evaluate(name, y_true, y_prob, k_fracs=(0.05, 0.10, 0.20)):
    y_true = np.asarray(y_true)
    y_pred = (y_prob >= 0.5).astype(int)
    n = len(y_true)
    row = {
        "model":     name,
        "auc_roc":   roc_auc_score(y_true, y_prob),
        "f1":        f1_score(y_true, y_pred, zero_division=0),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall":    recall_score(y_true, y_pred, zero_division=0),
    }
    for f in k_fracs:
        k = max(1, int(n * f))
        row[f"precision@{int(f*100)}pct"] = precision_at_k(y_true, y_prob, k)
        row[f"lift@{int(f*100)}pct"]      = lift_at_k(y_true, y_prob, k)
    return row

def evaluate_rule(name, y_true, y_pred_binary):
    return evaluate(name, y_true, y_pred_binary.astype(float))

def compare_all(metrics):
    return (pd.DataFrame(metrics).set_index("model")
            .sort_values("auc_roc", ascending=False).round(4))


def select_winner(metrics, tol=INTERPRETABILITY_THRESHOLD):
    df = pd.DataFrame(metrics).set_index("model")
    df = df[df["f1"] > 0]
    if df.empty:
        return metrics[0]["model"]
    best = df["auc_roc"].max()
    cand = df[df["auc_roc"] >= best - tol]
    for pref in ["logistic", "decision_tree", "random_forest", "lightgbm"]:
        if pref in cand.index:
            return pref
    return cand["auc_roc"].idxmax()

# %% [cell 11] ── Plot helpers ───────────────────────────────────────────────
def plot_roc(probs, y_true, title="ROC", ax=None):
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


def plot_lift(probs, y_true, title="Lift", ax=None):
    if ax is None:
        _, ax = plt.subplots(figsize=(8, 5))
    y_true = np.asarray(y_true)
    n = len(y_true)
    xs = np.linspace(0.01, 1.0, 100)
    for name, yp in probs.items():
        lifts = [lift_at_k(y_true, yp, max(1, int(x * n))) for x in xs]
        ax.plot(xs * 100, lifts, label=name)
    ax.axhline(1.0, color="grey", linestyle="--", alpha=0.6, label="Random")
    ax.set(xlabel="% Targeted", ylabel="Lift", title=title)
    ax.legend(); ax.grid(alpha=0.3)
    return ax


def plot_feature_importance(model, feat_names, top_n=20, title=""):
    clf = model.named_steps["clf"] if hasattr(model, "named_steps") else model
    if not hasattr(clf, "feature_importances_"):
        return None
    imp = pd.Series(clf.feature_importances_, index=feat_names).nlargest(top_n)
    fig, ax = plt.subplots(figsize=(8, 0.4 * top_n + 1))
    sns.barplot(x=imp.values, y=imp.index, ax=ax)
    ax.set(title=title or "Feature Importance", xlabel="Importance")
    plt.tight_layout()
    return fig

# %% [cell 12] ── Run an analysis (parameterised) ────────────────────────────
def run_analysis(name, df_pop, target_col, rule_fn, features=ALL_FEATURES):
    print("\n" + "=" * 64)
    print(f"  {name}")
    print("=" * 64)

    base = df_pop[target_col].mean()
    print(f"  Population: {len(df_pop):,} rows · base rate {base:.4f} ({base*100:.2f}%)")

    train, test = time_split(df_pop, month_col="month")
    print(f"  Train: {len(train):,} rows  ({sorted(train['month'].dt.to_period('M').unique())})")
    print(f"  Test : {len(test):,} rows  ({sorted(test['month'].dt.to_period('M').unique())})")

    X_train, y_train = make_xy(train, features, target_col)
    X_test,  y_test  = make_xy(test,  features, target_col)

    # rule baseline
    rule_preds = rule_fn(X_test)
    rule_metric = evaluate_rule("rule_baseline", y_test, rule_preds)

    # model ladder
    probs = run_model_ladder(X_train, y_train, X_test,
                             base_rate=float(y_train.mean()))

    metrics = [rule_metric] + [evaluate(n, y_test, p) for n, p in probs.items()]
    table = compare_all(metrics)
    print("\n  Metrics:"); print(table.to_string())

    # ROC + lift
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(name)
    plot_roc({**probs, "rule": rule_preds.astype(float)}, y_test,
             title="ROC", ax=axes[0])
    plot_lift({**probs, "rule": rule_preds.astype(float)}, y_test,
              title="Lift", ax=axes[1])
    plt.tight_layout(); plt.show()

    # champion + feature importance
    winner = select_winner([m for m in metrics if m["model"] != "rule_baseline"])
    print(f"\n  Champion: {winner}")
    champ = MODEL_BUILDERS[winner](); champ.fit(X_train, y_train)
    fig = plot_feature_importance(champ, list(X_train.columns), top_n=20,
                                  title=f"{name} — {winner} importance")
    if fig is not None:
        plt.show()

    # scores
    scores = test[["cust_id", "month"]].copy()
    scores["score"] = champ.predict_proba(X_test)[:, 1]
    scores["analysis"] = name
    scores["champion"] = winner
    scores = scores.sort_values("score", ascending=False).reset_index(drop=True)
    scores["rank"] = scores.index + 1
    return {"table": table, "winner": winner, "scores": scores}

# %% [cell 13] ── Load + preprocess + lag-join ───────────────────────────────
raw     = load_bq()
clean   = preprocess(raw)
lagged  = build_lag_table(clean)        # one row per (cust_id, month) where M-1→M exists
print(f"\nLagged shape: {lagged.shape}")

# %% [cell 14] ── Quick EDA: monthly counts + segment mix ────────────────────
print("\nMonthly row count (M-1 rows feeding the join):")
print(clean.groupby(clean["month"].dt.to_period("M")).size().to_string())

print("\nSegment mix in M-1:")
print(clean[["is_tfu", "is_donated", "is_follow_bet", "is_cold"]].mean().round(4).to_string())

# Sanity: verify total_bet_count includes follow_bet
diff = (clean["total_bet_count"] - clean["total_follow_bet_count"]).min()
print(f"\nSanity check — min(total_bet_count - total_follow_bet_count) = {diff} "
      f"(must be ≥ 0; confirms bet_count ⊇ follow_bet_count)")

# %% [cell 15] ── Analysis 1 — Gap=1 → TFU  (Model A + Model B) ──────────────
pop1 = lagged[(lagged["is_donated"] == 1) | (lagged["is_follow_bet"] == 1)].copy()
pop_a = pop1[pop1["is_donated"] == 1].copy()
pop_b = pop1[pop1["is_follow_bet"] == 1].copy()

res_1a = run_analysis("Analysis 1A — Donated (M-1) → TFU (M)",
                      pop_a, target_col="is_tfu_M", rule_fn=rule_gap_tfu)

res_1b = run_analysis("Analysis 1B — FollowBet (M-1) → TFU (M)",
                      pop_b, target_col="is_tfu_M", rule_fn=rule_gap_tfu)

# %% [cell 16] ── Analysis 2 — Bettor → Follow-Bettor ────────────────────────
pop2 = lagged[lagged["total_bet_count"] > 0].copy()
pop2["target_follow_M"] = (pop2["follow_bet_count_M"] > 0).astype(int)

res_2 = run_analysis("Analysis 2 — Bettor (M-1) → FollowBet (M)",
                     pop2, target_col="target_follow_M",
                     rule_fn=rule_bettor_follow)

# %% [cell 17] ── Analysis 3 — Watcher → Tipper ──────────────────────────────
pop3 = lagged[lagged["if_watch"] == 1].copy()
pop3["target_gift_M"] = pop3["any_gift_M"]

res_3 = run_analysis("Analysis 3 — Watcher (M-1) → Tipper (M)",
                     pop3, target_col="target_gift_M",
                     rule_fn=rule_watcher_tipper)

# Watch-bucket deep dive
print("\nTipper rate by M-1 watch_bucket:")
print(pop3.groupby("watch_bucket")["target_gift_M"]
         .agg(["mean", "count"]).round(4).to_string())

# %% [cell 18] ── Forecast / monitor output ──────────────────────────────────
all_scores = pd.concat([
    res_1a["scores"].assign(segment="donated"),
    res_1b["scores"].assign(segment="follow_bet"),
    res_2["scores"].assign(segment="bettor"),
    res_3["scores"].assign(segment="watcher"),
], ignore_index=True)

TOP_K = 1000
top_k = (all_scores.sort_values("score", ascending=False)
                   .groupby("analysis").head(TOP_K))
print(f"\nTop-{TOP_K} intervention list per analysis ({len(top_k):,} rows total):")
print(top_k.groupby("analysis").size().to_string())

# Save locally; in Colab uncomment the BQ export below to push to a CRM table.
top_k.to_csv("intervention_top_k.csv", index=False)
print("Wrote intervention_top_k.csv")

# bigquery.Client(project=PROJECT).load_table_from_dataframe(
#     top_k, "nf-muses.muses.tfu_intervention_top_k",
#     job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
# ).result()

# %% [cell 19] ── KPI snapshot + drift alert ─────────────────────────────────
kpi = {
    "month_max":          str(clean["month"].max().date()),
    "rows_lagged":        len(lagged),
    "tfu_rate_M":         float(lagged["is_tfu_M"].mean()),
    "donated_rate_M-1":   float(lagged["is_donated"].mean()),
    "followbet_rate_M-1": float(lagged["is_follow_bet"].mean()),
    "winner_1a": res_1a["winner"],
    "winner_1b": res_1b["winner"],
    "winner_2":  res_2["winner"],
    "winner_3":  res_3["winner"],
}
print("\nKPI snapshot:")
for k, v in kpi.items():
    print(f"  {k:24s} {v}")

# Simple drift example — compare M vs prior month
months = sorted(lagged["month_M"].unique())
if len(months) >= 2:
    cur, prev = months[-1], months[-2]
    cur_rate  = lagged.loc[lagged["month_M"] == cur,  "is_tfu_M"].mean()
    prev_rate = lagged.loc[lagged["month_M"] == prev, "is_tfu_M"].mean()
    delta = (cur_rate - prev_rate) / max(prev_rate, 1e-9)
    flag = "ALERT" if abs(delta) > 0.30 else "ok"
    print(f"\nDrift: TFU rate {prev_rate:.4f} → {cur_rate:.4f} "
          f"(Δ={delta*100:.1f}%) [{flag}]")
