"""
ml/compare_select.py — Champion model selection across all analyses.

Rule:
  For each analysis tag, pick the simplest model whose AUC is within 0.02
  of the best AUC in that group.
  Complexity order (simplest → most complex): rule, logistic, tree, rf, lgbm
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import pandas as pd
from ml.utils import load_artifacts, ARTIFACTS_DIR, MODEL_ORDER

TAGS = ['analysis1a', 'analysis1b', 'analysis2', 'analysis3']
AUC_TOLERANCE = 0.02


def load_all_results():
    rows = []
    for tag in TAGS:
        results_path = os.path.join(ARTIFACTS_DIR, f'{tag}_results.json')
        if not os.path.exists(results_path):
            print(f"  WARNING: {results_path} not found — run the analysis scripts first.")
            continue
        with open(results_path) as f:
            for r in json.load(f):
                r['tag'] = tag
                rows.append(r)
    return pd.DataFrame(rows)


def select_champion(group_df):
    """For one analysis group: return the simplest model within AUC_TOLERANCE of the best."""
    best_auc = group_df['auc'].max()
    threshold = best_auc - AUC_TOLERANCE
    candidates = group_df[group_df['auc'] >= threshold].copy()

    # Sort by model complexity (simplest first)
    candidates['complexity'] = candidates['model'].map(
        {m: i for i, m in enumerate(MODEL_ORDER)}
    ).fillna(len(MODEL_ORDER))
    candidates = candidates.sort_values('complexity')

    champion = candidates.iloc[0]
    return champion


def run_compare_select():
    results_df = load_all_results()
    if results_df.empty:
        print("No results found. Run all analysis scripts first.")
        return {}

    print(f"\n{'═'*60}")
    print("CHAMPION SELECTION")
    print(f"AUC tolerance: ±{AUC_TOLERANCE}\n")
    print(results_df[['tag', 'model', 'auc', 'f1']].sort_values(['tag', 'auc'], ascending=[True, False]).to_string(index=False))

    champions = {}
    print(f"\n{'─'*60}")
    print("Champions (simplest model within AUC tolerance of best):\n")
    for tag, grp in results_df.groupby('tag'):
        champ = select_champion(grp)
        champions[tag] = champ.to_dict()
        print(f"  {tag:12s} → {champ['model']:10s}  AUC={champ['auc']:.4f}  "
              f"(best={grp['auc'].max():.4f})")

    # Save champion manifest
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    manifest_path = os.path.join(ARTIFACTS_DIR, 'champions.json')
    with open(manifest_path, 'w') as f:
        json.dump(champions, f, indent=2)
    print(f"\nSaved champion manifest → {manifest_path}")

    return champions


def load_champion_models():
    """Load fitted champion model objects for all analyses. Returns {tag: model}."""
    manifest_path = os.path.join(ARTIFACTS_DIR, 'champions.json')
    if not os.path.exists(manifest_path):
        raise FileNotFoundError("Run run_compare_select() first to generate champions.json")

    with open(manifest_path) as f:
        champions = json.load(f)

    champion_models = {}
    for tag, champ in champions.items():
        _, models = load_artifacts(tag)
        model_name = champ['model']
        champion_models[tag] = models.get(model_name)
        print(f"  {tag:12s} → loaded {model_name}")

    return champion_models


if __name__ == '__main__':
    run_compare_select()
