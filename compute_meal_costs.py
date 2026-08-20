#!/usr/bin/env python3
"""
compute_meal_costs.py

Populates avg_cost_per_serving (+ coverage stats) on every active recipe in
recipe_metadata.json, using grocery price history at
/Users/Shared/grocery/price_history.json.

Manual/on-demand -- rerun after new receipts land to refresh estimates.
Requires the project venv (imports mcp/menu_server.py for ingredient
name canonicalization).

Usage:
    .venv/bin/python3 compute_meal_costs.py
    .venv/bin/python3 compute_meal_costs.py --dry-run
"""

import argparse
import json
import os
from collections import Counter
from datetime import date
from pathlib import Path

from meal_costing import estimate_recipe_cost

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(_CONFIG_PATH) as _f:
    _CONFIG = json.load(_f)

METADATA_PATH = Path(os.path.expanduser(_CONFIG["metadata_path"]))
PRICE_HISTORY_PATH = Path("/Users/Shared/grocery/price_history.json")


def _bucket(pct: float) -> str:
    if pct == 0:
        return "0%"
    if pct < 25:
        return "1-24%"
    if pct < 50:
        return "25-49%"
    return "50%+"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Compute and print summary without writing")
    args = parser.parse_args()

    metadata = json.loads(METADATA_PATH.read_text())
    price_history = json.loads(PRICE_HISTORY_PATH.read_text())

    updated = 0
    priced = 0
    buckets = Counter()

    for recipe in metadata["recipes"].values():
        if recipe.get("status") != "active" or not recipe.get("ingredients"):
            continue
        result = estimate_recipe_cost(recipe, price_history)
        recipe.update(result)
        updated += 1
        buckets[_bucket(result["cost_coverage_pct"])] += 1
        if result["avg_cost_per_serving"] is not None:
            priced += 1

    print(f"Recipes updated: {updated}")
    print(f"Coverage distribution: {dict(sorted(buckets.items()))}")
    print(f"Recipes with a confident avg_cost_per_serving (>=50% coverage): {priced}")

    if args.dry_run:
        print("Dry run -- not writing file.")
        return

    metadata["last_updated"] = date.today().isoformat()
    METADATA_PATH.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {METADATA_PATH}")


if __name__ == "__main__":
    main()
