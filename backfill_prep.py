#!/usr/bin/env python3
"""
backfill_prep.py — Populate prep_components and prep_notes for all active
recipes that are currently missing them.

Reads .md files for instructions where available; falls back to ingredients
alone for recipes without a .md file.

Usage:
  python3 backfill_prep.py            # fill all missing
  python3 backfill_prep.py --dry-run  # show what would be filled, no write
  python3 backfill_prep.py --limit 20 # process at most N recipes
"""

import argparse
import json
import os
from datetime import date
from pathlib import Path

MENUBUILDER = Path(__file__).parent
METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"
RECIPES_DIR   = Path.home() / "Dropbox/LLMContext/cooking/recipes"

# Load ANTHROPIC_API_KEY if not set
if not os.environ.get("ANTHROPIC_API_KEY"):
    env_path = Path.home() / "projects/personal/sms-assistant/.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                break

import sys
sys.path.insert(0, str(MENUBUILDER))
from prep_utils import classify_prep, parse_md_instructions

BATCH_SIZE = 15


def _build_recipe_dict(name: str, entry: dict) -> dict:
    """Build the dict classify_prep expects from a metadata entry."""
    ingr = entry.get("ingredients", [])
    if ingr and isinstance(ingr[0], dict):
        ingr_list = [i.get("name", "") for i in ingr]
    else:
        ingr_list = entry.get("ingredients_raw", [])

    instructions = entry.get("instructions", [])
    if not instructions:
        filename = entry.get("filename", "")
        if filename:
            md_path = RECIPES_DIR / filename
            if md_path.is_file():
                instructions = parse_md_instructions(md_path.read_text(encoding="utf-8", errors="ignore"))

    return {"title": name, "ingredients": ingr_list, "instructions": instructions}


def main():
    parser = argparse.ArgumentParser(description="Backfill prep_components for active recipes.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change, don't write.")
    parser.add_argument("--limit", type=int, default=0, help="Max recipes to process (0 = all).")
    args = parser.parse_args()

    metadata = json.loads(METADATA_PATH.read_text())
    recipes  = metadata.get("recipes", metadata)

    # Find all active recipes missing prep data
    candidates = [
        (name, entry)
        for name, entry in recipes.items()
        if entry.get("status") == "active" and not entry.get("prep_components")
    ]

    print(f"Active recipes missing prep data: {len(candidates)}")

    if args.limit:
        candidates = candidates[:args.limit]
        print(f"Limiting to {args.limit} recipes.")

    if not candidates:
        print("Nothing to do.")
        return

    # Process in batches
    total     = len(candidates)
    updated   = 0
    no_md     = 0
    n_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_num in range(n_batches):
        batch = candidates[batch_num * BATCH_SIZE : (batch_num + 1) * BATCH_SIZE]
        print(f"\nBatch {batch_num + 1}/{n_batches} ({len(batch)} recipes)...")

        to_classify = []
        for name, entry in batch:
            rd = _build_recipe_dict(name, entry)
            if not rd["instructions"]:
                no_md += 1
            to_classify.append(rd)

        prep_map = classify_prep(to_classify)

        for name, entry in batch:
            data = prep_map.get(name, {})
            components = data.get("prep_components", [])
            notes      = data.get("prep_notes", "")

            if args.dry_run:
                print(f"  [dry-run] {name}:")
                if components:
                    for c in components:
                        print(f"    - {c}")
                else:
                    print("    (no advance prep)")
                if notes:
                    print(f"    Note: {notes}")
            else:
                recipes[name]["prep_components"] = components
                recipes[name]["prep_notes"]      = notes
                updated += 1
                status = f"{len(components)} component(s)" if components else "no advance prep"
                print(f"  + {name} — {status}")

    if not args.dry_run:
        metadata["last_updated"] = date.today().isoformat()
        METADATA_PATH.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nDone. Updated {updated}/{total} recipes.")
        if no_md:
            print(f"  {no_md} recipes had no .md file — classified from ingredients only.")
    else:
        print(f"\n[dry-run] Would update {total} recipes.")


if __name__ == "__main__":
    main()
