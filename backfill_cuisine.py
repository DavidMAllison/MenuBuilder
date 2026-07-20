#!/usr/bin/env python3
"""
backfill_cuisine.py -- Classify missing cuisine tags in recipe_metadata.json via Haiku.

Usage:
  python3 backfill_cuisine.py           # dry-run (show proposed changes)
  python3 backfill_cuisine.py --write   # apply changes
"""

import argparse
import html
import json
import os
import re
from pathlib import Path

import anthropic

if not os.environ.get("ANTHROPIC_API_KEY"):
    env_path = Path.home() / "projects/personal/sms-assistant/.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                break

METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"
CUISINE_FAMILY_MAP: dict[str, str] = json.loads(
    (Path(__file__).parent / "config.json").read_text()
).get("cuisine_family_map", {})
CANONICAL_CUISINES = set(CUISINE_FAMILY_MAP.keys())

CLASSIFY_PROMPT = """\
Classify each recipe's cuisine using ONLY these canonical values:
American, Italian, Mexican, Chinese, Japanese, Korean, Thai, Vietnamese,
Indian, Mediterranean, Greek, Moroccan, Middle Eastern, French, Spanish,
Caribbean, Peruvian, Taiwanese

Rules:
- Use the dish origin, not the cookbook source (ATK/Alton Brown publish all cuisines)
- "Mediterranean" = broadly Mediterranean without a specific country (e.g. couscous bowl)
- "Greek" for clearly Greek dishes (tzatziki, spanakopita, souvlaki)
- "American" for US comfort food, Southern, BBQ, fusion with no clear origin
- "French" for classical French technique dishes (fricassee, rillons, braise with wine)
- "Italian" for pasta, risotto, Italian regional dishes
- Taiwanese for gua bao and other Taiwanese street food
- If truly ambiguous, pick the most dominant culinary tradition

Recipes to classify:
{recipes}

Reply with a JSON array only — no prose:
[{{"title": "...", "cuisine": "..."}}]"""


def load_metadata():
    raw = json.loads(METADATA_PATH.read_text())
    return raw, raw["recipes"]


def _clean_title(title: str) -> str:
    """Decode HTML entities and normalize whitespace for title matching."""
    return html.unescape(title).strip()


def find_missing(recipes: dict) -> list[dict]:
    missing = []
    for key, v in recipes.items():
        if isinstance(v, dict) and (not v.get("cuisine") or v.get("cuisine") == "?"):
            missing.append({
                "key": key,
                "title": _clean_title(v.get("title", key)),
                "source": v.get("source", ""),
                "ingredients_preview": ", ".join(
                    (v.get("ingredients_raw") or [])[:5]
                ),
            })
    return missing


def classify(missing: list[dict]) -> dict[str, str]:
    client = anthropic.Anthropic()
    lines = []
    for r in missing:
        line = f'- {r["title"]} (source: {r["source"]})'
        if r["ingredients_preview"]:
            line += f' | ingredients: {r["ingredients_preview"]}'
        lines.append(line)

    prompt = CLASSIFY_PROMPT.format(recipes="\n".join(lines))
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    # Strip code block markers if present
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        print(f"[!] Could not parse Haiku response:\n{text}")
        return {}
    classified = json.loads(m.group())
    result = {}
    for item in classified:
        cuisine = item["cuisine"]
        if cuisine not in CANONICAL_CUISINES:
            print(f"  [!] Unknown cuisine '{cuisine}' for '{item['title']}' — add to CUISINE_FAMILY_MAP in suggest_meals.py")
        result[_clean_title(item["title"])] = cuisine
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Apply changes to metadata")
    args = parser.parse_args()

    raw, recipes = load_metadata()
    missing = find_missing(recipes)

    if not missing:
        print("No recipes with missing cuisine — nothing to do.")
        return

    print(f"Found {len(missing)} recipes with missing cuisine. Classifying via Haiku...\n")
    classifications = classify(missing)

    # Match classifications back to recipe keys — exact then fuzzy (strip punctuation)
    def _norm(t: str) -> str:
        return re.sub(r"[^\w\s]", "", t.lower()).strip()

    fuzzy_map = {_norm(t): c for t, c in classifications.items()}

    changes = []
    unmatched = []
    for r in missing:
        cuisine = classifications.get(r["title"]) or fuzzy_map.get(_norm(r["title"]))
        if cuisine:
            changes.append((r["key"], r["title"], cuisine))
        else:
            unmatched.append(r["title"])

    print(f"{'TITLE':<55} {'CUISINE'}")
    print("-" * 72)
    for key, title, cuisine in sorted(changes, key=lambda x: x[2]):
        print(f"  {title[:53]:<55} {cuisine}")

    if unmatched:
        print("\n[!] No classification returned for:")
        for t in unmatched:
            print(f"    {t}")

    if not args.write:
        print(f"\nDry run — {len(changes)} changes proposed. Run with --write to apply.")
        return

    # Apply changes
    for key, title, cuisine in changes:
        recipes[key]["cuisine"] = cuisine

    raw["recipes"] = recipes
    METADATA_PATH.write_text(json.dumps(raw, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(changes)} cuisine updates to {METADATA_PATH}")


if __name__ == "__main__":
    main()
