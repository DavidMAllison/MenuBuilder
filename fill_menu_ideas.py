#!/usr/bin/env python3
"""
fill_menu_ideas.py -- Pull new recipe ideas from all agents and add to recipe_metadata.json.

Runs all cuisine agents in parallel, deduplicates against existing entries,
classifies health with Claude, and writes new entries as status="idea".

Agents run OUTSIDE the weekly workflow. Call this between cycles when the
idea pool needs refreshing — not during Sunday planning.

Usage:
  python3 fill_menu_ideas.py                          # all agents, default topic
  python3 fill_menu_ideas.py --topic "fish dinner"    # specific topic for all agents
  python3 fill_menu_ideas.py --agents mexican,indian  # specific agents only
  python3 fill_menu_ideas.py --dry-run                # show what would be added, no write
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"
MENUBUILDER = Path(__file__).parent

if not os.environ.get("ANTHROPIC_API_KEY"):
    env_path = Path.home() / "projects/personal/sms-assistant/.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                break

client = anthropic.Anthropic()

# Default queries per agent — broad enough for diverse results, cuisine-appropriate
DEFAULT_TOPICS = {
    "mexican": "weeknight chicken or pork dinner",
    "asian":   "weeknight dinner",
    "indian":  "weeknight chicken or vegetarian curry",
    "chef":    "healthy weeknight dinner fish or chicken",
    "sites":   "weeknight dinner",
}

ALL_AGENTS = list(DEFAULT_TOPICS.keys())


# ---------------------------------------------------------------------------
# Run one agent
# ---------------------------------------------------------------------------

def _run_agent(agent_name: str, topic: str) -> list[dict]:
    """Import and run a single agent's run_agent(), return found_recipes list."""
    sys.path.insert(0, str(MENUBUILDER))
    try:
        if agent_name == "mexican":
            from mexican_agent import run_agent
        elif agent_name == "asian":
            from asian_agent import run_agent
        elif agent_name == "indian":
            from indian_agent import run_agent
        elif agent_name == "chef":
            from chef_agent import run_agent
        elif agent_name == "sites":
            from sites_agent import run_agent
        else:
            print(f"  [!] Unknown agent: {agent_name}")
            return []
        return run_agent(topic)
    except Exception as e:
        print(f"  [!] {agent_name} agent error: {e}")
        return []


# ---------------------------------------------------------------------------
# Health classification — one Claude call for all new recipes
# ---------------------------------------------------------------------------

_CLASSIFY_PROMPT = """Classify each recipe as Heart-Healthy, Moderate, or Indulgent based on the recipe name and ingredients listed. Reply with a JSON array only — no prose.

Definitions:
- Heart-Healthy: lean protein (chicken breast, fish, legumes), low saturated fat, low sodium, vegetable-heavy
- Moderate: regular chicken or pork, some cream/cheese, restaurant-style but not excessive
- Indulgent: fried foods, red meat, heavy cream/butter sauces, high sodium, treat-meal territory

Recipes:
{recipes}

Reply format:
[{{"title": "...", "health": "Heart-Healthy"}}, ...]"""


def classify_health(recipes: list[dict]) -> dict[str, str]:
    """Batch classify health for a list of recipe dicts. Returns {title: health}."""
    if not recipes:
        return {}

    recipe_lines = []
    for r in recipes:
        ingr_preview = ", ".join(r.get("ingredients", [])[:6])
        recipe_lines.append(f'- {r["title"]} | Ingredients: {ingr_preview}')

    prompt = _CLASSIFY_PROMPT.format(recipes="\n".join(recipe_lines))

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        # Extract JSON array (may be wrapped in a code block)
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            classified = json.loads(m.group())
            return {item["title"]: item["health"] for item in classified}
    except Exception as e:
        print(f"  [!] Health classification error: {e}")

    return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Words whose presence as the final noun indicate a standalone condiment, not a meal
_CONDIMENT_TERMINAL = {
    "sauce", "salsa", "dressing", "marinade", "rub", "glaze", "vinaigrette",
    "relish", "chutney", "gravy", "dip", "spread", "jam", "compote", "paste",
    "aioli", "mayo", "mayonnaise", "pesto", "tapenade", "hummus", "tzatziki",
    "brine", "pickle", "pickles", "oil",
}

# If any of these appear in the title it's a full dish, not a condiment
_DISH_ANCHORS = {
    "chicken", "beef", "pork", "lamb", "fish", "salmon", "tuna", "cod",
    "halibut", "tilapia", "shrimp", "prawn", "scallop", "clam", "mussel",
    "tofu", "paneer", "lentil", "lentils", "dal", "bean", "beans",
    "chickpea", "chickpeas", "egg", "eggs", "pasta", "noodle", "noodles",
    "rice", "quinoa", "farro", "barley", "stew", "soup", "salad", "taco",
    "tacos", "bowl", "burger", "sandwich", "pizza", "casserole", "bake",
    "roast", "chili", "curry", "wrap", "dumpling", "meatball", "meatloaf",
    "sausage", "turkey", "duck", "tenderloin", "thigh", "breast", "chop",
    "rib", "ribs", "fillet", "steak", "cutlet", "schnitzel",
}


def _is_condiment(title: str) -> bool:
    """Return True if the title describes a standalone condiment, not a dinner recipe.

    Logic: the last meaningful word is a condiment type AND no main dish anchor
    (protein, grain, stew, etc.) appears anywhere in the title.
    """
    words = re.sub(r"[^\w\s]", "", title.lower()).split()
    if not words:
        return False
    if words[-1] not in _CONDIMENT_TERMINAL:
        return False
    title_lower = title.lower()
    return not any(anchor in title_lower for anchor in _DISH_ANCHORS)


def _title_to_filename(title: str) -> str:
    """Convert a recipe title to a filename slug."""
    slug = re.sub(r"[^\w\s-]", "", title)
    slug = re.sub(r"\s+", "_", slug.strip())
    return f"{slug}.md"


def _infer_cooking_method(title: str, instructions: list[str]) -> str:
    """Guess cooking method from title and instruction text."""
    text = (title + " " + " ".join(instructions[:2])).lower()
    if any(w in text for w in ["slow cooker", "crockpot", "slow-cook"]):
        return "slow_cooker"
    if any(w in text for w in ["grill", "bbq", "barbecue"]):
        return "grill"
    if any(w in text for w in ["roast", "bake", "oven", "sheet pan", "baked"]):
        return "oven"
    if any(w in text for w in ["stir-fry", "stir fry", "wok", "sauté", "saute", "pan-sear", "pan sear"]):
        return "stovetop"
    return "stovetop"


def _iso_to_minutes(iso: str) -> int:
    if not iso:
        return 0
    m = re.search(r"(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?", iso)
    if not m:
        return 0
    return int(m.group(1) or 0) * 1440 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def _infer_meal_type(recipe: dict) -> str:
    """Weeknight (<=60 min total) or Weekend."""
    # Try ISO duration fields first
    total_iso = recipe.get("total_time") or recipe.get("cook_time", "")
    mins = _iso_to_minutes(total_iso)
    if not mins:
        # Try human-readable time string
        time_str = recipe.get("time", "")
        m = re.search(r"(\d+)\s*hour", time_str)
        h = int(m.group(1)) if m else 0
        m2 = re.search(r"(\d+)\s*minute", time_str)
        mins = h * 60 + (int(m2.group(1)) if m2 else 0)
    return "Weekend" if mins > 60 else "Weeknight"


def _extract_source_name(source_label: str) -> str:
    """'Indian Healthy Recipes - https://...' → 'Indian Healthy Recipes'"""
    return source_label.split(" - ")[0].strip() if " - " in source_label else source_label


def _extract_url(source_label: str, recipe: dict) -> str:
    """Get the source URL from either source_label or recipe url field."""
    if " - http" in source_label:
        return source_label.split(" - ", 1)[1].strip()
    return recipe.get("url", "")


def _existing_urls(recipes: dict) -> set[str]:
    """Collect all source_url values already in the metadata."""
    urls = set()
    for entry in recipes.values():
        url = entry.get("source_url", "") or entry.get("url", "")
        if url:
            urls.add(url.rstrip("/"))
    return urls


def _existing_titles(recipes: dict) -> set[str]:
    return {v.get("title", "").lower().strip() for v in recipes.values()}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Replenish recipe idea pool from all agents.")
    parser.add_argument("--agents", default="all",
                        help="Comma-separated list of agents to run (mexican,asian,indian,chef,sites) or 'all'")
    parser.add_argument("--topic", default=None,
                        help="Override search topic for all agents (default: per-agent defaults)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be added without writing to metadata")
    args = parser.parse_args()

    # Resolve agents list
    if args.agents == "all":
        agents_to_run = ALL_AGENTS
    else:
        agents_to_run = [a.strip().lower() for a in args.agents.split(",")]
        unknown = [a for a in agents_to_run if a not in ALL_AGENTS]
        if unknown:
            print(f"Unknown agents: {unknown}. Valid: {ALL_AGENTS}")
            sys.exit(1)

    # Load metadata
    metadata = json.loads(METADATA_PATH.read_text())
    recipes = metadata["recipes"]
    existing_urls = _existing_urls(recipes)
    existing_titles = _existing_titles(recipes)

    print(f"Loaded {len(recipes)} existing recipes ({len(existing_urls)} with URLs)")
    print(f"Running agents: {agents_to_run}")
    print()

    # Run agents in parallel
    all_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(agents_to_run)) as pool:
        futures = {}
        for agent_name in agents_to_run:
            topic = args.topic or DEFAULT_TOPICS[agent_name]
            print(f"  Starting {agent_name} agent: {topic!r}")
            futures[pool.submit(_run_agent, agent_name, topic)] = agent_name

        for future in as_completed(futures):
            agent_name = futures[future]
            results = future.result()
            print(f"  [{agent_name}] returned {len(results)} recipe(s)")
            all_results.extend(results)

    print(f"\nTotal from agents: {len(all_results)} recipe(s)")

    # Deduplicate against existing entries
    new_recipes = []
    skipped = []
    for r in all_results:
        url = _extract_url(r.get("source", ""), r).rstrip("/")
        title = r.get("title", "").lower().strip()

        if url and url in existing_urls:
            skipped.append(r.get("title", "?") + " (URL exists)")
            continue
        if title and title in existing_titles:
            skipped.append(r.get("title", "?") + " (title exists)")
            continue
        if not r.get("ingredients") or not r.get("instructions"):
            skipped.append(r.get("title", "?") + " (no ingredients/instructions)")
            continue
        if _is_condiment(r.get("title", "")):
            skipped.append(r.get("title", "?") + " (condiment, not a meal)")
            continue

        new_recipes.append(r)
        # Track to avoid adding duplicates within the batch itself
        if url:
            existing_urls.add(url)
        if title:
            existing_titles.add(title)

    print(f"New (not in collection): {len(new_recipes)}")
    print(f"Skipped: {len(skipped)}")
    if skipped:
        for s in skipped:
            print(f"  - {s}")

    if not new_recipes:
        print("\nNothing new to add.")
        return

    # Classify health in one batch call
    print(f"\nClassifying health for {len(new_recipes)} recipes...")
    health_map = classify_health(new_recipes)

    # Build metadata entries
    today = date.today().isoformat()
    entries_to_add = {}

    for r in new_recipes:
        title = r.get("title", "Unknown").strip()
        source_label = r.get("source", "")
        source_name = _extract_source_name(source_label)
        source_url = _extract_url(source_label, r)

        cuisine = r.get("cuisine", "")
        if isinstance(cuisine, list):
            cuisine = ", ".join(cuisine)

        time_str = r.get("time", "")
        if not time_str:
            total_iso = r.get("total_time") or r.get("cook_time", "")
            mins = _iso_to_minutes(total_iso)
            if mins:
                h, m = divmod(mins, 60)
                parts = []
                if h:
                    parts.append(f"{h} hour{'s' if h > 1 else ''}")
                if m:
                    parts.append(f"{m} minute{'s' if m > 1 else ''}")
                time_str = " ".join(parts)

        health = health_map.get(title, "Moderate")
        meal_type = _infer_meal_type(r)
        cooking_method = _infer_cooking_method(title, r.get("instructions", []))

        entry = {
            "title": title,
            "filename": _title_to_filename(title),
            "source": source_name,
            "source_url": source_url,
            "url": source_url,              # alias — activation code uses "url"
            "cuisine": cuisine,
            "meal_type": meal_type,
            "health": health,
            "times_cooked": 0,
            "time": time_str,
            "servings": r.get("yield", ""),
            "status": "idea",
            "cooking_method": cooking_method,
            "last_cooked_date": None,
            # Full recipe content captured at intake — prevents re-fetch at activation
            # and enables shopping list generation without a .md file
            "ingredients_raw": r.get("ingredients", []),  # raw strings e.g. "¾ cup toor dal"
            "instructions":    r.get("instructions", []), # raw step strings
            # structured "ingredients" populated when recipe is activated (status → active)
        }
        # Use title as key (same pattern as existing entries)
        entries_to_add[title] = entry

    # Print summary
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Adding {len(entries_to_add)} new idea(s):\n")
    for title, entry in entries_to_add.items():
        print(f"  + {title}")
        print(f"    Source: {entry['source']} | Cuisine: {entry['cuisine']} | "
              f"Time: {entry['time']} | Health: {entry['health']} | Type: {entry['meal_type']}")

    if args.dry_run:
        print("\n[DRY RUN] No changes written.")
        return

    # Write to metadata
    recipes.update(entries_to_add)
    metadata["last_updated"] = today
    METADATA_PATH.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {len(entries_to_add)} new idea(s) to {METADATA_PATH}")
    print(f"Total recipes now: {len(recipes)}")


if __name__ == "__main__":
    main()
