#!/usr/bin/env python3
"""
suggest_lunch.py -- Return 3 lunch suggestions for Ashley.

Filters recipes that are lunch_suitable or meal_type=="lunch", avoids
anything cooked in the last 4 weeks, and returns name + GitHub Pages URL.

Usage:
  python3 suggest_lunch.py
  python3 suggest_lunch.py --exclude "Greek Chicken Salad"   # skip last week's pick
"""
import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"
CONFIG_PATH   = Path(__file__).parent / "config.json"

AVOID_WEEKS   = 4   # skip recipes eaten within this many weeks
N_SUGGESTIONS = 3


def _load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def _github_url(filename: str, base_url: str) -> str:
    slug = filename.replace(".md", "")
    return f"{base_url.rstrip('/')}/{slug}"


def _normalize(s: str) -> str:
    return re.sub(r"[^\w]", "", s.lower())


def suggest(exclude: str = "") -> list[dict]:
    data     = json.loads(METADATA_PATH.read_text())
    recipes  = data["recipes"]
    config   = _load_config()
    base_url = config.get("github_pages_base_url", "https://davidmallison.github.io/menubuilder-recipes")
    cutoff   = date.today() - timedelta(weeks=AVOID_WEEKS)
    exclude_norm = _normalize(exclude) if exclude else ""

    candidates = []
    for name, r in recipes.items():
        if r.get("status") != "active":
            continue
        is_lunch = r.get("lunch_suitable") or r.get("meal_type") == "lunch"
        if not is_lunch:
            continue
        # Skip recently eaten
        last = r.get("last_lunch_date")
        if last:
            try:
                if date.fromisoformat(last) >= cutoff:
                    continue
            except ValueError:
                pass
        # Skip last week's pick
        if exclude_norm and _normalize(name) == exclude_norm:
            continue
        candidates.append((name, r))

    # Sort: fewer times eaten first, then alphabetical for stability
    candidates.sort(key=lambda x: (x[1].get("times_eaten_lunch", 0), x[0]))

    picks = []
    for name, r in candidates[:N_SUGGESTIONS]:
        filename = r.get("filename", "")
        url = _github_url(filename, base_url) if filename else ""
        picks.append({
            "name":   name,
            "url":    url,
            "health": r.get("health", ""),
            "times_eaten_lunch": r.get("times_eaten_lunch", 0),
        })
    return picks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exclude", default="", help="Recipe name to exclude (last week's pick)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    picks = suggest(exclude=args.exclude)
    if not picks:
        print("No lunch candidates found.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(picks, indent=2))
        return

    for i, p in enumerate(picks, 1):
        tag = f" ({p['times_eaten_lunch']}x)" if p["times_eaten_lunch"] else " (new)"
        print(f"{i}. {p['name']}{tag}")
        print(f"   {p['url']}")
        print()


if __name__ == "__main__":
    main()
