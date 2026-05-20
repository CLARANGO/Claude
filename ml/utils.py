"""
ml/utils.py — Shared utilities for TFU prediction pipeline.

Colab setup (run once per session):
    !pip install lightgbm google-cloud-bigquery db-dtypes
    from ml.utils import *
"""

import warnings
warnings.filterwarnings('ignore')

import os, json, pickle
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.linear_model  import LogisticRegression
from sklearn.tree          import DecisionTreeClassifier
from sklearn.ensemble      import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline      import Pipeline
from sklearn.metrics       import (
    roc_auc_score, f1_score, precision_score, recall_score, roc_curve
)

try:
    import lightgbm as lgb
    LGBM_AVAILABLE = True
except ImportError:
    LGBM_AVAILABLE = False
    print("lightgbm not found — LightGBM step will be skipped. "
          "Install with:  !pip install lightgbm")

# Authenticate in Colab; fall back to ADC / GOOGLE_APPLICATION_CREDENTIALS elsewhere.
try:
    from google.colab import auth as _colab_auth
    _colab_auth.authenticate_user()
except ImportError:
    pass

from google.cloud import bigquery

# ─── Project constants ────────────────────────────────────────────────────────
BQ_PROJECT = 'nf-muses'
BQ_TABLE   = '`nf-muses.muses.tfu_user_monthly`'

# ─── Feature lists ────────────────────────────────────────────────────────────
# breadth_score excluded per project update (2026-05).
# total_bet_count includes follow_bet_count per design — see SQL comments.

WATCH_FEATS   = ['total_watch_sec', 'avg_watch_sec_per_session', 'watch_bucket']
CHAT_FEATS    = ['total_messages', 'chat_sessions', 'total_bullet_sec', 'total_chatroom_sec']
GIFT_FEATS    = ['total_tip_count', 'total_box_count', 'total_wheel_count', 'total_gift_count']
BET_FEATS     = ['total_bet_count', 'total_follow_bet_count', 'total_member_to', 'total_bdw_bet_count']
SESSION_FEATS = ['session_count', 'distinct_streamers']

ML_FEATURES = WATCH_FEATS + CHAT_FEATS + GIFT_FEATS + BET_FEATS + SESSION_FEATS

# Model complexity order for champion selection (simplest → most complex)
MODEL_ORDER = ['rule', 'logistic', 'tree', 'rf', 'lgbm']

ARTIFACTS_DIR = os.path.join(os.path.dirname(__file__), 'artifacts')


# ─── BigQuery helpers ─────────────────────────────────────────────────────────

def get_bq_client():
    return bigquery.Client(project=BQ_PROJECT)


def load_table(client=None):
    """Pull tfu_user_monthly from BigQuery. Returns DataFrame sorted by cust_id, month."""
    if client is None:
        client = get_bq_client()
    q = f"SELECT * FROM {BQ_TABLE} ORDER BY cust_id, month"
    df = client.query(q).to_dataframe()
    df['month'] = pd.to_datetime(df['month'])
    # Coerce feature columns to float (BigQuery may return pyarrow / object types)
    for col in ML_FEATURES:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(float)
    return df


# ─── Pair builder ─────────────────────────────────────────────────────────────

def build_pairs(df, feature_cols, target_col, pop_filter=None):
    """
    Build (M-1 features) → (M target) self-join pairs, then time-split.

    Using the 5 most-recent months in df:
      Train:  feature months 1–3  →  target months 2–4  (3 pairs)
      Test:   feature month  4    →  target month  5    (1 pair)

    Args:
        pop_filter: callable(sub_df) → bool Series applied to M-1 rows.
                    e.g.  lambda d: (d['is_donated']==1) | (d['is_follow_bet']==1)
    Returns:
        X_train, y_train, X_test, y_test  (all numeric, NaN → 0)
    """
    df = df.copy()
    months = sorted(df['month'].unique())
    if len(months) < 2:
        raise ValueError(f"Need at least 2 months of data, got {len(months)}")
    months = months[-5:] if len(months) >= 5 else months

    records = []
    for i in range(len(months) - 1):
        m_feat = months[i]
        m_tgt  = months[i + 1]

        feat_df = df[df['month'] == m_feat].copy()
        if pop_filter is not None:
            feat_df = feat_df[pop_filter(feat_df)]

        tgt_df = (
            df[df['month'] == m_tgt][['cust_id', target_col]]
            .rename(columns={target_col: 'target'})
        )

        merged = (
            feat_df[['cust_id'] + feature_cols]
            .merge(tgt_df, on='cust_id', how='inner')
        )
        merged['feat_month'] = m_feat
        records.append(merged)

    all_pairs = pd.concat(records, ignore_index=True)

    # Test uses feature month at index -2 (4th of 5); train uses everything before it
    test_feat_month = months[-2]
    train = all_pairs[all_pairs['feat_month'] <  test_feat_month]
    test  = all_pairs[all_pairs['feat_month'] == test_feat_month]

    X_train = train[feature_cols].fillna(0).astype(float)
    y_train = train['target'].astype(int)
    X_test  = test[feature_cols].fillna(0).astype(float)
    y_test  = test['target'].astype(int)

    print(f"  Train : {len(X_train):>6,} rows | base rate {y_train.mean():.2%}")
    print(f"  Test  : {len(X_test):>6,} rows | base rate {y_test.mean():.2%}")
    return X_train, y_train, X_test, y_test


# ─── Evaluation helpers ───────────────────────────────────────────────────────

def precision_at_k(y_true, y_prob, k=100):
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    top_idx = np.argsort(y_prob)[::-1][:k]
    return y_true[top_idx].mean()


def lift_at_k(y_true, y_prob, k=100):
    base = np.asarray(y_true).mean()
    return 0.0 if base == 0 else precision_at_k(y_true, y_prob, k) / base


def evaluate(model_name, y_true, y_prob, k=100, analysis_tag=''):
    y_pred = (np.asarray(y_prob) >= 0.5).astype(int)
    y_true = np.asarray(y_true)
    auc  = roc_auc_score(y_true, y_prob) if len(np.unique(y_true)) > 1 else 0.5
    f1   = f1_score(y_true, y_pred, zero_division=0)
    pak  = precision_at_k(y_true, y_prob, k)
    lak  = lift_at_k(y_true, y_prob, k)
    print(f"  [{model_name:10s}]  AUC={auc:.4f}  F1={f1:.4f}  "
          f"P@{k}={pak:.4f}  Lift@{k}={lak:.2f}x")
    return {
        'analysis': analysis_tag,
        'model': model_name,
        'auc': round(auc, 6),
        'f1': round(f1, 6),
        f'precision_at_{k}': round(pak, 6),
        f'lift_at_{k}': round(lak, 4),
    }


# ─── Model ladder ─────────────────────────────────────────────────────────────

def run_model_ladder(X_train, y_train, X_test, y_test,
                     rule_pred_test=None,
                     analysis_tag='',
                     k=100,
                     min_base_rate=0.01):
    """
    Run rule → logistic → tree (depth 4) → RF → LightGBM.
    LightGBM skipped when test base rate < min_base_rate.

    Returns:
        results : list of metric dicts
        models  : dict  {model_name: fitted_object}
        probs   : dict  {model_name: probability_array}  for plotting
    """
    base_rate = y_test.mean()
    results, models, probs = [], {}, {}

    # 1. Rule baseline
    if rule_pred_test is not None:
        rp = np.asarray(rule_pred_test, dtype=float)
        r = evaluate('rule', y_test, rp, k, analysis_tag)
        results.append(r)
        models['rule'] = None
        probs['rule'] = rp

    # 2. Logistic Regression
    pipe_lr = Pipeline([
        ('scaler', StandardScaler()),
        ('clf', LogisticRegression(max_iter=1000, class_weight='balanced', random_state=42))
    ])
    pipe_lr.fit(X_train, y_train)
    p_lr = pipe_lr.predict_proba(X_test)[:, 1]
    results.append(evaluate('logistic', y_test, p_lr, k, analysis_tag))
    models['logistic'] = pipe_lr
    probs['logistic'] = p_lr

    # 3. Decision Tree (depth 4)
    dt = DecisionTreeClassifier(max_depth=4, class_weight='balanced', random_state=42)
    dt.fit(X_train, y_train)
    p_dt = dt.predict_proba(X_test)[:, 1]
    results.append(evaluate('tree', y_test, p_dt, k, analysis_tag))
    models['tree'] = dt
    probs['tree'] = p_dt

    # 4. Random Forest
    rf = RandomForestClassifier(
        n_estimators=200, max_depth=8, class_weight='balanced',
        n_jobs=-1, random_state=42
    )
    rf.fit(X_train, y_train)
    p_rf = rf.predict_proba(X_test)[:, 1]
    results.append(evaluate('rf', y_test, p_rf, k, analysis_tag))
    models['rf'] = rf
    probs['rf'] = p_rf

    # 5. LightGBM
    if base_rate < min_base_rate:
        print(f"  [lgbm      ]  SKIPPED — base rate {base_rate:.2%} < {min_base_rate:.0%}")
    elif not LGBM_AVAILABLE:
        print("  [lgbm      ]  SKIPPED — not installed")
    else:
        pos_w = max((y_train == 0).sum() / max((y_train == 1).sum(), 1), 1)
        lgbm_clf = lgb.LGBMClassifier(
            n_estimators=500, learning_rate=0.05, max_depth=6,
            scale_pos_weight=pos_w, n_jobs=-1, random_state=42, verbose=-1
        )
        lgbm_clf.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(-1)]
        )
        p_lgbm = lgbm_clf.predict_proba(X_test)[:, 1]
        results.append(evaluate('lgbm', y_test, p_lgbm, k, analysis_tag))
        models['lgbm'] = lgbm_clf
        probs['lgbm'] = p_lgbm

    return results, models, probs


# ─── Plots ────────────────────────────────────────────────────────────────────

def plot_roc(y_test, probs: dict, title='ROC Curve'):
    fig, ax = plt.subplots(figsize=(7, 5))
    for name, p in probs.items():
        if p is None:
            continue
        fpr, tpr, _ = roc_curve(y_test, p)
        auc = roc_auc_score(y_test, p)
        ax.plot(fpr, tpr, label=f'{name} (AUC={auc:.3f})')
    ax.plot([0, 1], [0, 1], 'k--', lw=0.8)
    ax.set_xlabel('FPR'); ax.set_ylabel('TPR'); ax.set_title(title)
    ax.legend(); fig.tight_layout(); plt.show()


def plot_lift(y_test, probs: dict, title='Lift Curve', max_pct=0.5):
    fig, ax = plt.subplots(figsize=(7, 5))
    y_arr = np.asarray(y_test)
    n = len(y_arr)
    ks = list(range(10, max(11, int(n * max_pct)), max(1, int(n * max_pct) // 60)))
    for name, p in probs.items():
        if p is None:
            continue
        lifts = [lift_at_k(y_arr, p, ki) for ki in ks]
        ax.plot([ki / n for ki in ks], lifts, label=name)
    ax.axhline(1.0, color='k', linestyle='--', lw=0.8, label='random')
    ax.set_xlabel('Top fraction'); ax.set_ylabel('Lift'); ax.set_title(title)
    ax.legend(); fig.tight_layout(); plt.show()


def plot_importance(model, feature_cols, title='Feature Importance', top_n=15):
    if model is None:
        return
    if hasattr(model, 'feature_importances_'):
        imp = pd.Series(model.feature_importances_, index=feature_cols)
    elif hasattr(model, 'named_steps'):
        clf = model.named_steps.get('clf')
        if clf is not None and hasattr(clf, 'coef_'):
            imp = pd.Series(np.abs(clf.coef_[0]), index=feature_cols)
        else:
            return
    else:
        return
    imp = imp.nlargest(top_n).sort_values()
    fig, ax = plt.subplots(figsize=(7, 5))
    imp.plot(kind='barh', ax=ax)
    ax.set_title(title); fig.tight_layout(); plt.show()


# ─── Artifact I/O ─────────────────────────────────────────────────────────────

def save_artifacts(tag, results, models):
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    with open(os.path.join(ARTIFACTS_DIR, f'{tag}_results.json'), 'w') as f:
        json.dump(results, f, indent=2)
    with open(os.path.join(ARTIFACTS_DIR, f'{tag}_models.pkl'), 'wb') as f:
        pickle.dump(models, f)
    print(f"  Saved → {ARTIFACTS_DIR}/{tag}_*.json / .pkl")


def load_artifacts(tag):
    with open(os.path.join(ARTIFACTS_DIR, f'{tag}_results.json')) as f:
        results = json.load(f)
    with open(os.path.join(ARTIFACTS_DIR, f'{tag}_models.pkl'), 'rb') as f:
        models = pickle.load(f)
    return results, models
