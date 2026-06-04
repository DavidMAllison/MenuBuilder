#!/usr/bin/env python3
"""
Generate _data/recipes.json for the menubuilder-recipes GitHub Pages site.

Reads recipe_metadata.json and outputs a trimmed JSON file keyed by
filename (without .md) so Jekyll layouts can render health badges,
source links, and time without touching individual recipe files.

Run whenever recipe_metadata.json changes:
    python3 generate_github_pages_data.py
"""

import json
from pathlib import Path

METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"
OUTPUT_PATH = Path.home() / "projects/personal/menubuilder-recipes/_data/recipes.json"


def derive_filename_key(name: str, meta: dict) -> str:
    """Return the filename stem (no extension) for a recipe."""
    filename = meta.get("filename", "")
    if filename:
        return filename.replace(".md", "").replace(".pdf", "")
    # Derive from name: spaces → underscores, strip parens
    return name.replace(" ", "_").replace("(", "").replace(")", "").replace("/", "-")


def get_time(meta: dict) -> str:
    if meta.get("time"):
        return meta["time"]
    mins = meta.get("cook_time_minutes")
    if mins:
        return f"{mins} minutes"
    return ""


def main():
    with open(METADATA_PATH) as f:
        data = json.load(f)

    output = {}
    skipped = []

    for name, recipe in data["recipes"].items():
        if recipe.get("status") != "active":
            continue

        key = derive_filename_key(name, recipe)
        if not key:
            skipped.append(name)
            continue

        output[key] = {
            "health": recipe.get("health_classification") or recipe.get("health", ""),
            "time": get_time(recipe),
            "servings": recipe.get("servings", ""),
            "cuisine": recipe.get("cuisine_type") or recipe.get("cuisine", ""),
            "source": recipe.get("source", ""),
            "source_url": recipe.get("url") or recipe.get("source_url", ""),
        }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Generated {len(output)} recipe entries → {OUTPUT_PATH}")
    if skipped:
        print(f"Skipped {len(skipped)}: {skipped}")


if __name__ == "__main__":
    main()
