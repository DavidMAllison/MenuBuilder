#!/usr/bin/env python3
"""
backfill_ingredients.py

Populate the structured `ingredients` array for recipes that have
`ingredients_raw` (strings) but no parsed `ingredients` entries.

Also handles recipes that have a .md file but neither field — reads
the ## Ingredients section from the file.

Uses Claude Haiku to batch-parse raw strings into:
    {"name": str, "quantity": str, "unit": str, "category": str}

Categories: Proteins | Produce | Dairy | Pantry/Asian | Dry Goods | Spices/Herbs

Usage:
    python3 backfill_ingredients.py          # all eligible recipes
    python3 backfill_ingredients.py --dry-run
    python3 backfill_ingredients.py --limit 5
    python3 backfill_ingredients.py --recipe "Moo Shu Chicken"
"""

import argparse
import json
import os
import re
from pathlib import Path

import anthropic

METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"
RECIPES_DIR   = Path.home() / "Dropbox/LLMContext/cooking/recipes"

CATEGORIES = ["Proteins", "Produce", "Dairy", "Pantry/Asian", "Dry Goods", "Spices/Herbs"]

BATCH_SIZE = 6  # recipes per Haiku call

_PARSE_PROMPT = """\
Parse each recipe's ingredient list into structured JSON.

For each ingredient string, extract:
- name: the food item only, lowercase (e.g. "chicken thighs", "garlic", "soy sauce")
- quantity: numeric amount as string (e.g. "1.5", "2", "3/4"), or "" if none
- unit: measurement unit (e.g. "lbs", "cup", "tbsp", "cloves", "oz"), or "" if none
- category: exactly one of: Proteins | Produce | Dairy | Pantry/Asian | Dry Goods | Spices/Herbs

Category rules:
- Proteins: meat, fish, seafood, eggs, tofu, tempeh, legumes used as protein
- Produce: fresh vegetables, fresh herbs, fruits
- Dairy: milk, cream, cheese, butter, yogurt
- Pantry/Asian: sauces, oils, condiments, canned goods, stocks, Asian pantry staples
- Dry Goods: flour, sugar, rice, pasta, dried noodles, breadcrumbs, dried legumes
- Spices/Herbs: dried spices, dried herbs, salt, pepper, spice blends

Recipes to parse:
{recipes}

Return ONLY a JSON array in this exact format, no commentary:
[
  {{
    "title": "Recipe Name",
    "ingredients": [
      {{"name": "...", "quantity": "...", "unit": "...", "category": "..."}},
      ...
    ]
  }},
  ...
]"""


def _extract_from_md(md_path: Path) -> list[str]:
    """Extract raw ingredient lines from a .md recipe file."""
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return []
    in_ingredients = False
    raw = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^##\s+Ingredients", stripped, re.IGNORECASE):
            in_ingredients = True
            continue
        if in_ingredients:
            if re.match(r"^##\s+", stripped):  # next section
                break
            if stripped.startswith("- "):
                raw.append(stripped[2:].strip())
            elif stripped.startswith("* "):
                raw.append(stripped[2:].strip())
    return raw


def _safe_title(title: str) -> str:
    """Replace curly/smart quotes and other JSON-breaking chars with ASCII equivalents."""
    return (title
            .replace("“", "'").replace("”", "'")  # curly double quotes → single
            .replace("‘", "'").replace("’", "'")  # curly single quotes
            .replace('"', "'"))                              # straight double quotes


def _parse_batch(client: anthropic.Anthropic, batch: list[dict]) -> dict[str, list]:
    """Call Haiku on a batch of {title, ingredients_raw} dicts.
    Returns {original_title: [structured_ingredient, ...]}."""
    # Map safe titles back to original for result lookup
    safe_to_orig = {_safe_title(r["title"]): r["title"] for r in batch}

    recipe_lines = []
    for r in batch:
        lines = "\n".join(f"  - {i}" for i in r["ingredients_raw"])
        recipe_lines.append(f'Title: {_safe_title(r["title"])}\nIngredients:\n{lines}')

    prompt = _PARSE_PROMPT.format(recipes="\n\n".join(recipe_lines))

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            # Map safe titles back to original keys
            return {
                safe_to_orig.get(item["title"], item["title"]): item["ingredients"]
                for item in parsed
            }
    except Exception as e:
        print(f"  [!] Parse error: {e}")
    return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max recipes to process")
    parser.add_argument("--recipe", type=str, default="", help="Process a single recipe by name")
    args = parser.parse_args()

    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    recipes  = metadata["recipes"]

    # Collect eligible recipes
    eligible = []
    for name, entry in recipes.items():
        if entry.get("status") in ("disliked", "ignored"):
            continue
        if entry.get("ingredients"):  # already done
            continue
        if args.recipe and args.recipe.lower() not in name.lower():
            continue

        raw = entry.get("ingredients_raw", [])

        # Fall back to .md file for recipes with neither field
        if not raw:
            fn = entry.get("filename", "")
            if fn.endswith(".md"):
                raw = _extract_from_md(RECIPES_DIR / fn)
                if raw:
                    print(f"  [md fallback] {name}: {len(raw)} ingredients read from .md")

        if not raw:
            print(f"  [skip] {name}: no ingredients_raw and no readable .md")
            continue

        eligible.append({"title": name, "ingredients_raw": raw})

    if args.limit:
        eligible = eligible[:args.limit]

    print(f"Eligible for backfill: {len(eligible)} recipes\n")
    if not eligible:
        return

    if args.dry_run:
        for r in eligible:
            print(f"  [DRY RUN] Would parse: {r['title']} ({len(r['ingredients_raw'])} ingredients)")
        print("\n[DRY RUN] No changes written.")
        return

    # Initialize Anthropic client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        env_path = Path.home() / "projects/personal/sms-assistant/.env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                    break
    client = anthropic.Anthropic()

    # Process in batches
    total_done = 0
    total_failed = 0
    for i in range(0, len(eligible), BATCH_SIZE):
        batch = eligible[i:i + BATCH_SIZE]
        batch_titles = [r["title"] for r in batch]
        print(f"Batch {i // BATCH_SIZE + 1}: {', '.join(t[:30] for t in batch_titles)}")

        results = _parse_batch(client, batch)

        for r in batch:
            title = r["title"]
            if title in results and results[title]:
                recipes[title]["ingredients"] = results[title]
                total_done += 1
                print(f"  ✓ {title}: {len(results[title])} ingredients parsed")
            else:
                total_failed += 1
                print(f"  ✗ {title}: parse failed")

    # Save
    METADATA_PATH.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nDone. {total_done} recipes updated, {total_failed} failed.")
    print(f"Saved to {METADATA_PATH}")


if __name__ == "__main__":
    main()
