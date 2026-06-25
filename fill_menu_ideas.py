#!/usr/bin/env python3
"""
fill_menu_ideas.py -- Pull new recipe ideas from all agents and add to recipe_metadata.json.

Runs all cuisine agents in parallel, deduplicates against existing entries,
classifies health with Claude, and writes new entries directly as status="active"
with a .md file created at intake. Low-quality content gets needs_review=true.

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
import random
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

_ROTATION_STATE = Path(__file__).parent / "agent_rotation_state.json"

import anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

METADATA_PATH   = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"
CONDIMENTS_PATH = Path.home() / "Dropbox/LLMContext/cooking/condiments.json"
RECIPES_DIR     = Path.home() / "Dropbox/LLMContext/cooking/recipes"
MENUBUILDER     = Path(__file__).parent

_CONFIG_PATH = MENUBUILDER / "config.json"
_CUISINE_FAMILY_MAP: dict[str, str] = json.loads(_CONFIG_PATH.read_text()).get("cuisine_family_map", {})


def _register_cuisine(cuisine: str) -> None:
    """Add an unknown cuisine to config.json with itself as its own family."""
    if not cuisine or cuisine in _CUISINE_FAMILY_MAP:
        return
    _CUISINE_FAMILY_MAP[cuisine] = cuisine
    raw = json.loads(_CONFIG_PATH.read_text())
    raw.setdefault("cuisine_family_map", {})[cuisine] = cuisine
    _CONFIG_PATH.write_text(json.dumps(raw, indent=2, ensure_ascii=False))
    print(f"  [cuisine] Registered new cuisine '{cuisine}' in config.json")

# Quality thresholds — entries failing these get needs_review=true
_HTML_RE        = re.compile(r"<[^>]+>")
_MIN_STEPS      = 3
_MIN_STEP_LEN   = 30
_MIN_INGREDIENTS = 4


def _quality_check(ingredients_raw: list, instructions: list) -> bool:
    """Return True (needs_review) if content looks auto-generated / incomplete."""
    if len(instructions) < _MIN_STEPS:
        return True
    if any(len(s) < _MIN_STEP_LEN for s in instructions):
        return True
    if len(ingredients_raw) < _MIN_INGREDIENTS:
        return True
    if any(_HTML_RE.search(s) for s in instructions + ingredients_raw):
        return True
    return False


def _build_recipe_md(title: str, entry: dict, needs_review: bool) -> str:
    """Format a recipe entry as a .md file."""
    lines = [f"# {title}", ""]
    if needs_review:
        lines += [
            "> **Needs Review** — auto-generated content; verify formatting and completeness before first cook.",
            "",
        ]
    time_str   = entry.get("time", "")
    servings   = entry.get("servings", "")
    source     = entry.get("source", "")
    source_url = entry.get("source_url", "")
    if time_str:
        lines.append(f"**Time**: {time_str}  ")
    if servings:
        lines.append(f"**Serves**: {servings}  ")
    if source_url:
        label = source if source else source_url
        lines.append(f"**Adapted from**: [{label}]({source_url})  ")
    elif source:
        lines.append(f"**Source**: {source}  ")
    if time_str or servings or source_url or source:
        lines.append("")
    lines += ["## Ingredients", ""]
    for ing in entry.get("ingredients_raw", []):
        lines.append(f"- {ing}")
    lines.append("")
    lines += ["## Instructions", ""]
    for i, step in enumerate(entry.get("instructions", []), 1):
        lines.append(f"{i}. {step}")
    lines.append("")
    return "\n".join(lines)

if not os.environ.get("ANTHROPIC_API_KEY"):
    env_path = Path.home() / "projects/personal/sms-assistant/.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                break

client = anthropic.Anthropic()

# Query pools per agent — one is picked randomly each run for variety.
# Specific dish names / ingredients work better than broad terms on most sites.
# Health filtering happens at menu planning, not here — include indulgent too.
QUERY_POOLS = {
    "mexican": [
        "cochinita pibil",
        "mole negro chicken",
        "Veracruz fish",
        "carne asada tacos",
        "chiles en nogada",
        "pozole rojo",
        "birria",
        "rajas con crema",        # vegetarian
        "poc chuc",
        "shrimp tacos",
        "black bean soup",        # vegetarian
        "picadillo",
        "chile colorado pork",
        "calabacitas vegetarian",
    ],
    "asian": [
        "pork belly braised",
        "galbi short ribs",
        "khao soi",
        "dan dan noodles",
        "Vietnamese lemongrass pork",
        "mapo tofu",
        "pad kra pao",
        "bun bo hue",
        "sundubu jjigae",
        "oyakodon",
        "lion's head meatballs",
        "massaman curry",
        "tofu stir fry",          # vegetarian
        "japchae",
        "Vietnamese pork noodle bowl",
        "Chinese braised lamb",
    ],
    "indian": [
        "lamb rogan josh",
        "Goan fish curry",
        "nihari",
        "Chettinad chicken",
        "dal makhani",            # vegetarian
        "lamb biryani",
        "Kerala prawn curry",
        "haleem",
        "Bengali fish curry",
        "aloo gobi",              # vegetarian
        "rajma",                  # vegetarian
        "baingan bharta",         # vegetarian
        "egg curry South Indian",
        "mutton seekh kebab",
        "saag paneer",            # vegetarian
    ],
    "chef": [
        "pork tenderloin",
        "salmon",
        "lamb shoulder",
        "braised chicken thighs",
        "beef short ribs",
        "shrimp",
        "pork chops",
        "mushroom pasta",         # vegetarian
        "lentil soup",            # vegetarian
        "eggplant",               # vegetarian
        "fish en papillote",
        "roast chicken",
        "turkey meatballs",
    ],
    "italian": [
        "pasta carbonara",
        "ossobuco",
        "saltimbocca",
        "ribollita",              # vegetarian
        "pasta e fagioli",        # vegetarian
        "chicken cacciatore",
        "pasta all'amatriciana",
        "branzino al forno",
        "polpette al sugo",
        "pasta e ceci",           # vegetarian
        "abbacchio alla romana",
        "risotto ai funghi",      # vegetarian
        "pesce all'acqua pazza",
        "involtini di carne",
        "caponata siciliana",     # vegetarian
    ],
    "mediterranean": [
        "Greek lemon chicken",
        "lamb kleftiko",
        "moussaka",
        "chicken tagine",
        "shakshuka",
        "kibbeh",
        "kafta",
        "Turkish kofta",
        "spanakopita",
        "baba ganoush",           # vegetarian
        "Persian herb frittata",  # vegetarian
        "muujaddara",             # vegetarian
        "chermoula fish",
        "pastitsio",
        "Greek baked fish",
        "Lebanese chicken rice",
    ],
    "sites": [
        # Beef
        "braised short ribs",
        "smash burger",
        "pot roast",
        "beef stew",
        "brisket",
        "meatballs",
        "steak",
        # Pork
        "pork shoulder",
        "pork belly",
        "pork chops",
        "spare ribs",
        # Chicken
        "roast chicken",
        "fried chicken",
        "braised chicken thighs",
        "chicken wings",
        # Lamb
        "lamb chops",
        "braised lamb shoulder",
        # Fish
        "salmon",
        "halibut",
        "cod",
        "sea bass",
        "tuna steak",
        # Seafood
        "shrimp",
        "scallops",
        "mussels",
        "clams",
        # Vegetarian
        "shakshuka",              # vegetarian
        "vegetarian chili",       # vegetarian
        "risotto",                # vegetarian
        "mushroom pasta",         # vegetarian
        "lentil soup",            # vegetarian
        "roasted eggplant",       # vegetarian
        "grain bowl",             # vegetarian
        # Soups & stews
        "French onion soup",      # vegetarian
        "chowder",
        "ramen",
        # Cross-cuisine
        "Thai curry",
        "tacos",
        "enchiladas",
        "fried rice",
        "grilled fish",
    ],
}

ALL_AGENTS = list(QUERY_POOLS.keys())


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
        elif agent_name == "italian":
            from italian_agent import run_agent
        elif agent_name == "mediterranean":
            from mediterranean_agent import run_agent
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

_EFFORT_PROMPT = """Classify the weeknight cooking effort for each recipe as low, medium, or high.

low   -- oven or slow cooker does the work; minimal active attention; set and step away
medium -- active but manageable; some chopping, moderate stove attention, one pan
high  -- multiple components simultaneously, timing pressure, or constant attention required

Recipes:
{recipes}

Reply with a JSON array only — no prose:
[{{"title": "...", "weeknight_effort": "low|medium|high"}}, ...]"""


def classify_effort(recipes: list[dict]) -> dict[str, str]:
    """Batch classify weeknight effort for a list of recipe dicts. Returns {title: effort}."""
    if not recipes:
        return {}

    recipe_lines = []
    for r in recipes:
        parts = [f'- {r["title"]}']
        if r.get("cooking_method"):
            parts.append(f'method={r["cooking_method"]}')
        if r.get("time"):
            parts.append(f'time={r["time"]}')
        recipe_lines.append(" | ".join(parts))

    prompt = _EFFORT_PROMPT.format(recipes="\n".join(recipe_lines))

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            classified = json.loads(m.group())
            return {item["title"]: item["weeknight_effort"]
                    for item in classified
                    if item.get("weeknight_effort") in ("low", "medium", "high")}
    except Exception as e:
        print(f"  [!] Effort classification error: {e}")

    return {}


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
# Kid-friendly classification
# ---------------------------------------------------------------------------

_KID_PROMPT = """Classify each recipe as kid_friendly: true or false.

A recipe is kid_friendly if it features familiar, mild flavors that most kids will eat
(chicken, pasta, rice, noodles, tacos, burgers, mild stir-fry, plain proteins) AND does not
rely on strong heat, aggressive spice, very pungent sauces, or unusual textures as the main
flavor profile. Bonus if a kid's portion can be pulled before saucing.

Not kid_friendly: very spicy dishes, strong fish-sauce-forward recipes, unusual offal/organ meats,
highly acidic or bitter profiles, dishes where the spice is inseparable from the base.

Recipes:
{recipes}

Reply with a JSON array only — no prose:
[{{"title": "...", "kid_friendly": true}}, ...]"""


def classify_kid_friendly(recipes: list[dict]) -> dict[str, bool]:
    """Batch classify kid-friendliness. Returns {title: bool}."""
    if not recipes:
        return {}

    recipe_lines = []
    for r in recipes:
        ingr_preview = ", ".join(r.get("ingredients", [])[:6])
        recipe_lines.append(f'- {r["title"]} | Ingredients: {ingr_preview}')

    prompt = _KID_PROMPT.format(recipes="\n".join(recipe_lines))

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            classified = json.loads(m.group())
            return {item["title"]: bool(item.get("kid_friendly")) for item in classified}
    except Exception as e:
        print(f"  [!] Kid-friendly classification error: {e}")

    return {}


# ---------------------------------------------------------------------------
# Prep classification — delegated to prep_utils
# ---------------------------------------------------------------------------

from prep_utils import classify_prep  # noqa: E402  (after sys.path setup above)


# ---------------------------------------------------------------------------
# Structured ingredient parsing
# ---------------------------------------------------------------------------

_INGREDIENT_PARSE_PROMPT = """\
Parse each recipe's ingredient list into structured JSON.

For each ingredient string, extract:
- name: the food item only, lowercase (no quantities or units)
- quantity: numeric amount as string (e.g. "1.5", "2", "3/4"), or "" if none
- unit: measurement unit (e.g. "lbs", "cup", "tbsp"), or "" if none
- category: one of: Proteins | Produce | Dairy | Pantry/Asian | Dry Goods | Spices/Herbs

Categories: Proteins=meat/fish/eggs/tofu; Produce=fresh veg/herbs/fruit;
Dairy=milk/cream/cheese/butter; Pantry/Asian=sauces/oils/condiments/stocks;
Dry Goods=flour/sugar/rice/pasta/dried legumes; Spices/Herbs=dried spices and herbs.

Recipes:
{recipes}

Return ONLY a JSON array:
[{{"title": "...", "ingredients": [{{"name":"...","quantity":"...","unit":"...","category":"..."}},...]}},...]"""


def _safe_title_for_json(title: str) -> str:
    return (title
            .replace("“", "'").replace("”", "'")
            .replace("‘", "'").replace("’", "'")
            .replace('"', "'"))


def parse_ingredients_structured(recipes: list[dict]) -> dict[str, list]:
    """Batch parse ingredients_raw → structured list. Returns {title: [ingredient,...]}."""
    if not recipes:
        return {}

    safe_to_orig = {_safe_title_for_json(r["title"]): r["title"] for r in recipes}
    recipe_blocks = []
    for r in recipes:
        lines = "\n".join(f"  - {i}" for i in r.get("ingredients", []))
        recipe_blocks.append(f'Title: {_safe_title_for_json(r["title"])}\nIngredients:\n{lines}')

    prompt = _INGREDIENT_PARSE_PROMPT.format(recipes="\n\n".join(recipe_blocks))
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
            return {safe_to_orig.get(item["title"], item["title"]): item["ingredients"]
                    for item in parsed}
    except Exception as e:
        print(f"  [!] Ingredient parse error: {e}")
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


def _save_agent_condiment(recipe: dict) -> None:
    """Write an agent-found condiment to condiments.json if not already present."""
    title = (recipe.get("title") or "").strip()
    if not title:
        return
    try:
        data = json.loads(CONDIMENTS_PATH.read_text()) if CONDIMENTS_PATH.exists() else {}
        if title in data:
            return  # already have it
        source_label = recipe.get("source", "")
        # Extract bare URL from "Source Name - https://..." style labels
        url = recipe.get("url") or recipe.get("source_url") or ""
        if not url:
            import re as _re
            m = _re.search(r"https?://\S+", source_label)
            if m:
                url = m.group(0)
        data[title] = {
            "name":        title,
            "type":        "sauce",
            "source":      source_label,
            "source_url":  url,
            "image":       recipe.get("image", ""),
            "servings":    recipe.get("yield", ""),
            "ingredients": recipe.get("ingredients", []),
            "instructions": recipe.get("instructions", []),
            "notes":       "",
        }
        CONDIMENTS_PATH.write_text(json.dumps(data, indent=2))
        print(f"  [condiment] Added to condiments.json: {title}")
    except Exception as e:
        print(f"  [condiment] Failed to save {title}: {e}")


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


# Adjectives / stop words that don't identify a dish — strip before title comparison
# so "Easy Chicken Tikka Masala" matches existing "Chicken Tikka Masala".
_TITLE_STOP = {
    "easy", "simple", "quick", "best", "classic", "authentic", "homemade",
    "traditional", "perfect", "crispy", "tender", "juicy", "creamy", "spicy",
    "cheesy", "smoky", "hearty", "rustic", "amazing", "ultimate", "foolproof",
    "the", "a", "an", "my", "with", "and", "in", "or", "for", "style",
}


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, remove filler adjectives for fuzzy dedup."""
    t = re.sub(r"[^\w\s]", "", title.lower())
    words = [w for w in t.split() if w not in _TITLE_STOP]
    return " ".join(words)


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


def _existing_norm_titles(recipes: dict) -> set[str]:
    return {_normalize_title(k) for k in recipes}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Replenish recipe idea pool from all agents.")
    parser.add_argument("--agents", default="all",
                        help="Comma-separated list of agents to run, or 'all'")
    parser.add_argument("--rotate", type=int, default=0, metavar="N",
                        help="Run next N agents in rotation instead of all (persists state in agent_rotation_state.json)")
    parser.add_argument("--topic", default=None,
                        help="Override search topic for all agents (default: per-agent defaults)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run agents (populates /tmp) but do not write to metadata")
    args = parser.parse_args()

    # Resolve agents list
    if args.rotate > 0:
        # Load rotation state
        state = json.loads(_ROTATION_STATE.read_text()) if _ROTATION_STATE.exists() else {"next": 0}
        start = state["next"] % len(ALL_AGENTS)
        agents_to_run = [ALL_AGENTS[(start + i) % len(ALL_AGENTS)] for i in range(args.rotate)]
        state["next"] = (start + args.rotate) % len(ALL_AGENTS)
        _ROTATION_STATE.write_text(json.dumps(state))
        print(f"Rotation: running {agents_to_run} (next run starts at index {state['next']})")
    elif args.agents == "all":
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
    existing_norm = _existing_norm_titles(recipes)

    print(f"Loaded {len(recipes)} existing recipes ({len(existing_urls)} with URLs)")
    print(f"Running agents: {agents_to_run}")
    print()

    # Run agents in parallel
    all_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(agents_to_run)) as pool:
        futures = {}
        for agent_name in agents_to_run:
            topic = args.topic or random.choice(QUERY_POOLS[agent_name])
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
        norm = _normalize_title(r.get("title", ""))

        if url and url in existing_urls:
            skipped.append(r.get("title", "?") + " (URL exists)")
            continue
        if title and title in existing_titles:
            skipped.append(r.get("title", "?") + " (title exists)")
            continue
        if norm and norm in existing_norm:
            skipped.append(r.get("title", "?") + " (fuzzy title match)")
            continue
        if not r.get("ingredients") or not r.get("instructions"):
            skipped.append(r.get("title", "?") + " (no ingredients/instructions)")
            continue
        if _is_condiment(r.get("title", "")):
            _save_agent_condiment(r)
            skipped.append(r.get("title", "?") + " (condiment → condiments.json)")
            continue

        new_recipes.append(r)
        # Track to avoid adding duplicates within the batch itself
        if url:
            existing_urls.add(url)
        if title:
            existing_titles.add(title)
        if norm:
            existing_norm.add(norm)

    print(f"New (not yet in collection): {len(new_recipes)}")
    print(f"Skipped (already in collection): {len(skipped)}")
    if skipped:
        for s in skipped:
            print(f"  - {s}")

    if not new_recipes:
        print("\nNothing new in the queue.")
    else:
        print(f"\nNew recipes available in Review UI (/New view):")
        for r in new_recipes:
            print(f"  + {r.get('title','?')} ({r.get('source','?')})")
        print(f"\nAgents wrote results to /tmp — open the Recipe Review UI and use Add to Collection.")

    # Post-run metadata cleanup — fix cuisine/source/meal_type + classify missing health/time
    print("\n--- Post-run cleanup ---")
    _cleanup = MENUBUILDER / "cleanup_agent.py"
    cmd = [sys.executable, str(_cleanup), "--fix-metadata", "--fix-classify", "--apply"]
    if args.dry_run:
        cmd.append("--dry-run")
    subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
