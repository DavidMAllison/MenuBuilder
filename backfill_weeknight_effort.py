#!/usr/bin/env python3
"""
backfill_weeknight_effort.py

Classify weeknight cooking effort for each active recipe as low/medium/high.

  low    -- oven or slow cooker does the work; minimal active attention
  medium -- manageable active cooking; one pan, moderate attention
  high   -- multiple components, timing pressure, or constant attention required

Uses cooking_method, time, prep_components, and instructions (when in JSON).

Usage:
    python3 backfill_weeknight_effort.py          # all eligible recipes
    python3 backfill_weeknight_effort.py --dry-run
    python3 backfill_weeknight_effort.py --limit 10
    python3 backfill_weeknight_effort.py --recipe "Korean Fried Chicken"
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import anthropic

METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"

BATCH_SIZE = 8

_CLASSIFY_PROMPT = """\
Classify the weeknight cooking effort for each recipe below.

Effort levels:
- low: oven or slow cooker does most of the work; you set it and step away; minimal active tending
- medium: active but manageable; some chopping/prep, moderate attention at stove, one or two timing decisions; typical one-pan weeknight meal
- high: multiple components running simultaneously, timing pressure, constant attention, or technically demanding steps (deep frying, complex multi-stage); stressful on a busy weeknight

Recipes:
{recipes}

Return ONLY a JSON array, no commentary:
[
  {{"title": "Recipe Name", "weeknight_effort": "low|medium|high"}},
  ...
]"""


def _build_recipe_block(name: str, r: dict) -> str:
    parts = [f"Title: {name}"]
    if r.get("time"):
        parts.append(f"Time: {r['time']}")
    if r.get("cooking_method"):
        parts.append(f"Method: {r['cooking_method']}")
    if r.get("prep_components"):
        prep = "; ".join(r["prep_components"])
        parts.append(f"Prep components: {prep}")
    if r.get("instructions"):
        snippet = str(r["instructions"])[:300].replace("\n", " ")
        parts.append(f"Instructions (excerpt): {snippet}")
    return "\n".join(parts)


def _classify_batch(client: anthropic.Anthropic, batch: list[tuple[str, dict]]) -> dict[str, str]:
    blocks = [_build_recipe_block(name, r) for name, r in batch]
    prompt = _CLASSIFY_PROMPT.format(recipes="\n\n".join(blocks))

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            return {item["title"]: item["weeknight_effort"] for item in parsed
                    if item.get("weeknight_effort") in ("low", "medium", "high")}
    except Exception as e:
        print(f"  [!] Classify error: {e}")
    return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--recipe", type=str, default="")
    args = parser.parse_args()

    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    recipes = metadata["recipes"]

    eligible = []
    for name, r in recipes.items():
        if r.get("status") in ("disliked", "ignored"):
            continue
        if r.get("weeknight_effort"):
            continue
        if args.recipe and args.recipe.lower() not in name.lower():
            continue
        eligible.append((name, r))

    if args.limit:
        eligible = eligible[:args.limit]

    print(f"Eligible for effort classification: {len(eligible)} recipes\n")
    if not eligible:
        return

    if args.dry_run:
        for name, r in eligible:
            method = r.get("cooking_method", "?")
            has_prep = bool(r.get("prep_components"))
            has_instructions = bool(r.get("instructions"))
            print(f"  [DRY RUN] {name} | method={method} prep={has_prep} instructions={has_instructions}")
        print(f"\n[DRY RUN] No changes written.")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        env_path = Path.home() / "projects/personal/sms-assistant/.env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                    break
    client = anthropic.Anthropic()

    total_done = 0
    total_failed = 0

    for i in range(0, len(eligible), BATCH_SIZE):
        batch = eligible[i:i + BATCH_SIZE]
        titles = [name for name, _ in batch]
        print(f"Batch {i // BATCH_SIZE + 1}: {', '.join(t[:25] for t in titles)}")

        results = _classify_batch(client, batch)

        for name, r in batch:
            if name in results:
                recipes[name]["weeknight_effort"] = results[name]
                total_done += 1
                print(f"  - {name}: {results[name]}")
            else:
                total_failed += 1
                print(f"  [!] {name}: no result")

    METADATA_PATH.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nDone. {total_done} classified, {total_failed} failed.")
    print(f"Saved to {METADATA_PATH}")


if __name__ == "__main__":
    main()
