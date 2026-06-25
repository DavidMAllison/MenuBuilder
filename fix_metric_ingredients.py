#!/usr/bin/env python3
"""
fix_metric_ingredients.py

Find recipes whose ingredients_raw contain metric measurements and convert
them to US equivalents via Haiku. Clears the structured `ingredients` array
so backfill_ingredients.py can rebuild it from the corrected raw strings.

Usage:
    python3 fix_metric_ingredients.py --dry-run
    python3 fix_metric_ingredients.py
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import anthropic

METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"

# Sources known to use metric measurements
METRIC_SOURCES = {
    "Memorie di Angelina",
    "Cucchiaio d'Argento",
    "Chetna Makan",
    "Ranveer Brar",
    "Indian Healthy Recipes",
    "Hebbars Kitchen",
    "Kannamma Cooks",
    "Archana's Kitchen",
}

METRIC_PAT = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:g|gms?|gm|ml|kg|l|cl)\b", re.IGNORECASE)


def has_metric(strings: list[str]) -> bool:
    return any(METRIC_PAT.search(s) for s in strings if not s.startswith("["))


def find_affected(recipes: dict) -> list[tuple[str, dict]]:
    """Return (name, recipe) pairs that have metric in ingredients_raw."""
    affected = []
    for name, r in recipes.items():
        if r.get("source") not in METRIC_SOURCES:
            continue
        raw = r.get("ingredients_raw", [])
        if not raw:
            continue
        if has_metric(raw):
            affected.append((name, r))
    return affected


def convert_raw_batch(recipes_raw: list[tuple[str, list[str]]]) -> dict[str, list[str]]:
    """Send raw ingredient lists to Haiku and get back US-converted versions.

    Returns dict: recipe_name -> converted ingredients_raw list.
    """
    if not recipes_raw:
        return {}

    blocks = []
    for i, (name, raw) in enumerate(recipes_raw):
        blocks.append(f"RECIPE {i + 1}: {name}")
        blocks.append("Ingredients: " + " | ".join(raw))
        blocks.append("")

    prompt = (
        "Convert all metric measurements in the following recipe ingredients to US measurements.\n"
        "Rules:\n"
        "- If an ingredient has both metric AND US already (e.g. '200 g 7 oz.'), remove the metric part and keep only the US.\n"
        "- If an ingredient also has both in parentheses (e.g. '1 kg (2.2 lbs)'), remove the metric and keep just '2.2 lbs'.\n"
        "- If it only has metric, convert: g→oz (for <500g) or lbs (for ≥500g), ml→cups/tbsp/tsp, kg→lbs. Round to clean fractions (1/4, 1/3, 1/2, 3/4).\n"
        "- HTML entities like &amp; should be converted to their characters (& etc).\n"
        "- Keep all other text exactly as-is.\n"
        "Return a JSON array with one object per recipe in the same order:\n"
        '[{"ingredients": ["..."]}]\n\n'
        + "\n".join(blocks)
    )

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = re.sub(r"```(?:json)?\s*", "", resp.content[0].text.strip()).strip().rstrip("`")
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        print("  [!] Could not parse Haiku response")
        return {}

    converted = json.loads(m.group())
    result = {}
    for (name, _), conv in zip(recipes_raw, converted):
        result[name] = conv.get("ingredients", [])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = parser.parse_args()

    with open(METADATA_PATH) as f:
        data = json.load(f)
    recipes = data["recipes"]

    affected = find_affected(recipes)
    if not affected:
        print("No recipes with metric measurements found.")
        return

    print(f"Found {len(affected)} recipe(s) with metric measurements:\n")
    for name, r in affected:
        raw = r.get("ingredients_raw", [])
        metric = [i for i in raw if METRIC_PAT.search(i)]
        print(f"  [{r.get('source', '?')}] {name}")
        for m in metric[:4]:
            print(f"    {m}")
        if len(metric) > 4:
            print(f"    ... and {len(metric) - 4} more")

    if args.dry_run:
        print("\n[dry-run] No changes written.")
        return

    print("\nConverting via Haiku...")
    batch = [(name, r.get("ingredients_raw", [])) for name, r in affected]
    converted = convert_raw_batch(batch)

    if not converted:
        print("Conversion failed — no changes written.")
        sys.exit(1)

    changed = 0
    for name, r in affected:
        if name not in converted:
            print(f"  [!] No conversion result for {name!r} — skipping")
            continue
        new_raw = converted[name]
        old_raw = r.get("ingredients_raw", [])
        print(f"\n  {name}")
        for old, new in zip(old_raw, new_raw):
            if old != new:
                print(f"    - {old}")
                print(f"    + {new}")
        # Write converted raw; clear structured ingredients so backfill rebuilds them
        recipes[name]["ingredients_raw"] = new_raw
        recipes[name]["ingredients"] = []
        changed += 1

    data["recipes"] = recipes
    with open(METADATA_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nUpdated {changed} recipe(s). Run backfill_ingredients.py to rebuild structured ingredients:")
    for name, _ in affected:
        print(f'  python3 backfill_ingredients.py --recipe "{name}"')


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        env_path = Path.home() / "projects/personal/sms-assistant/.env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                    break
    main()
