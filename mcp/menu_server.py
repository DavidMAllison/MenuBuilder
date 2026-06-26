#!/usr/bin/env python3
"""
MenuBuilder MCP Server — weekly menu workflow tools.

Transport: stdio
Run via:  /Users/davidallison/projects/personal/MenuBuilder/.venv/bin/python3.12 mcp/menu_server.py

Tools:
  get_workflow_state       — current activity state
  start_menu_workflow      — initialize workflow, drain feedback, load last week
  log_meal_feedback        — record last-week ratings; "done" advances state
  get_meal_suggestions     — run candidate filter, auto-select 7 meals
  advance_to_meal_approval — hand off from local SMS phase to bridge phase with pre-selected meals
  swap_meal                — replace one day's meal (pre-signoff)
  approve_menu             — send selected meals to Ashley via Keanu
  handle_ashley_reply      — process Ashley's approval or swap request
  activate_idea_recipe     — activate a pending idea from provided markdown content
  generate_shopping_list   — write shopping CSV from finalized meals (authoritative — sms-assistant calls this)
  finalize_plan            — generate plan + shopping CSV, launch apps
  process_recipe_url       — check for similar recipe, add if new, optionally swap into day
  process_recipe_image     — extract recipe from a photo (cookbook page), add to collection
  set_recipe_image         — store a user-submitted photo as the image for an existing recipe
  get_prep_guide           — on-demand prep guide (mode: weekly | tonight | auto)
  sync_atk_recipes         — import new recipes from ATK favorite collections
"""

import csv
import difflib
import io
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

MENUBUILDER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MENUBUILDER_DIR))

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config and paths
# ---------------------------------------------------------------------------

_CONFIG_PATH = MENUBUILDER_DIR / "config.json"
_CONFIG = json.loads(_CONFIG_PATH.read_text())
METADATA_PATH = Path(_CONFIG["metadata_path"])
_CUISINE_FAMILY_MAP: dict[str, str] = _CONFIG.get("cuisine_family_map", {})
WEEKLYPLAN_DIR = METADATA_PATH.parent / "weeklyplan"
RECIPES_DIR = METADATA_PATH.parent / "recipes"
RECIPE_IMAGES_DIR = METADATA_PATH.parent / "recipe_images"
CONDIMENTS_PATH   = METADATA_PATH.parent / "condiments.json"
FEEDBACK_CURRENT_FILE = WEEKLYPLAN_DIR / "feedback_current.json"

ACTIVITY_FILE = MENUBUILDER_DIR / "menu_activity.json"
OUTBOX_FILE = Path("/Users/Shared/sms-assistant/.outbox.json")
PENDING_FILE = Path("/Users/Shared/sms-assistant/menu_feedback_pending.json")
LUNCH_STATE_FILE = Path("/Users/Shared/cooking/lunch_state.json")

PARTNER_HANDLE = _CONFIG.get("partner_handle", "")          # Ashley
ADMIN_HANDLE = _CONFIG.get("admin_handle", "")              # David (optional in config)

# Ensure files written by this process (davidallison or allisonbot) are group-writable
# so the other user can update them (meal swaps, plan updates, etc.).
os.umask(0o000)
GITHUB_BASE_URL = _CONFIG.get("github_pages_base_url", "")
DROPBOX_BASE_URL = _CONFIG.get("dropbox_recipe_base_url", "")

DAYS_ORDER = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
ADULT_NAMES = set(n.lower() for n in _CONFIG.get("adult_names", []))
_GARDEN_HERBS = set(h.lower() for h in _CONFIG.get("garden_herbs", []))

# Day-of-week helpers (date.weekday(): Mon=0 … Sun=6)
_WD_TO_ABBREV = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_ABBREV_TO_DOW = {day: i for i, day in enumerate(DAYS_ORDER)}  # Sun=0 … Sat=6

# Food-safety classification for prep timing
_DAYOF_NOTE_PATTERNS = [
    r"day.of\s+only",
    r"don'?t\s+(?:start|marinate)\s+more\s+than\s+[1-4]\s+h",
    r"max\s+[1-4]\s+hours?",
    r"marinate\s+day.of",
    r"within\s+[1-4]\s+hours?",
    r"no\s+more\s+than\s+[1-4]\s+h",
]
_CITRUS_TERMS    = ["lime", "lemon juice", "lemon zest", "orange juice"]
_MARINATE_KW     = ["marinate", "marinade", "toss in"]
_DAYOF_COMP_KW   = ["batter", "dredge", "bread the", "coat in", "guacamole", "avocado"]


def _recipe_is_dayof(prep_components: list, prep_notes: str, recipe_data: dict) -> bool:
    """
    Return True if this recipe's prep should be done day-of rather than prepped ahead.

    Priority order:
    1. Explicit timing constraint in prep_notes (e.g. "day-of only", "max 2 hours")
    2. Citrus marinade heuristic: recipe has lime/lemon + a marination step
    3. Component text: batter, dredge, avocado/guacamole (spoils/goes soggy)
    """
    notes_lower = prep_notes.lower()
    if any(re.search(p, notes_lower) for p in _DAYOF_NOTE_PATTERNS):
        return True

    has_citrus = any(
        any(c in (ing.get("name", "") + " " + ing.get("unit", "")).lower() for c in _CITRUS_TERMS)
        for ing in recipe_data.get("ingredients", [])
    ) or any(
        any(c in raw.lower() for c in _CITRUS_TERMS)
        for raw in recipe_data.get("ingredients_raw", [])
    )
    has_marinate = any(
        any(kw in comp.lower() for kw in _MARINATE_KW)
        for comp in prep_components
    )
    if has_citrus and has_marinate:
        return True

    for comp in prep_components:
        if any(kw in comp.lower() for kw in _DAYOF_COMP_KW):
            return True

    return False

DAY_NAME_MAP = {
    "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu",
    "friday": "Fri", "saturday": "Sat", "sunday": "Sun",
    "mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu",
    "fri": "Fri", "sat": "Sat", "sun": "Sun",
}

APPROVAL_PHRASES = (
    "looks good", "good", "ok", "okay", "approved", "go ahead", "perfect",
    "great", "sounds good", "yes", "yep", "yeah", "love it", "fine", "sure",
    "we're good", "all good", "\U0001f44d"
)

# ---------------------------------------------------------------------------
# Activity state I/O
# ---------------------------------------------------------------------------

def _load_activity() -> dict:
    if ACTIVITY_FILE.exists():
        try:
            return json.loads(ACTIVITY_FILE.read_text())
        except Exception:
            pass
    return {"state": "idle"}


def _save_activity(activity: dict) -> None:
    ACTIVITY_FILE.write_text(json.dumps(activity, indent=2))


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def _load_metadata() -> dict:
    return json.loads(METADATA_PATH.read_text()).get("recipes", {})


def _save_metadata(recipes: dict) -> None:
    raw = json.loads(METADATA_PATH.read_text()) if METADATA_PATH.exists() else {}
    raw["recipes"] = recipes
    raw["last_updated"] = date.today().isoformat()
    METADATA_PATH.write_text(json.dumps(raw, indent=2))


def _find_recipe_key(name: str, recipes: dict) -> Optional[str]:
    name_lower = name.lower()
    for key in recipes:
        if key.lower() == name_lower:
            return key
    for key in recipes:
        # Deduplicate words so repeated tokens (e.g. "Chicken ... Chicken") don't double-count
        words = {w for w in key.lower().split() if len(w) > 4}
        if len(words) >= 2 and sum(1 for w in words if w in name_lower) >= 2:
            return key
    # Substring match: plan title may be a shortened form of the full metadata key
    # (e.g. "Pasta e Ceci" stored as "Pasta e ceci (Pasta and Chickpeas)")
    if len(name_lower) > 8:
        for key in recipes:
            if name_lower in key.lower() or key.lower().startswith(name_lower):
                return key
    return None


def _title_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _find_similar_recipe(title: str, url: str, recipes: dict) -> Optional[tuple]:
    """
    Check if an existing recipe is similar to the given title or URL.
    Returns (recipe_name, score) if found (score 1.0 = exact URL match), else None.
    Threshold: 0.75 fuzzy title match.
    """
    if url:
        for name, r in recipes.items():
            if r.get("source_url") == url or r.get("url") == url:
                return (name, 1.0)
    if title:
        best_name, best_score = None, 0.0
        for name in recipes:
            score = _title_similarity(title, name)
            if score > best_score:
                best_name, best_score = name, score
        if best_score >= 0.75:
            return (best_name, round(best_score, 2))
        # Substring check: catches resubmissions where vision extracts a shorter title
        # than the stored name (e.g. "Chicken Fricassee" vs the full stored title).
        title_lower = title.lower()
        if len(title_lower) > 8:
            for name in recipes:
                name_k = name.lower()
                if title_lower in name_k or name_k in title_lower:
                    return (name, 0.85)
    return None


_DAY_ALIASES: dict[str, str] = {
    "monday": "Mon", "mon": "Mon",
    "tuesday": "Tue", "tue": "Tue", "tues": "Tue",
    "wednesday": "Wed", "wed": "Wed",
    "thursday": "Thu", "thu": "Thu", "thur": "Thu", "thurs": "Thu",
    "friday": "Fri", "fri": "Fri",
    "saturday": "Sat", "sat": "Sat",
    "sunday": "Sun", "sun": "Sun",
}


def _extract_day_from_text(text: str) -> str:
    """Return the first day abbreviation found in free text, or ''."""
    lower = text.lower()
    for word, abbrev in _DAY_ALIASES.items():
        if re.search(r'\b' + word + r'\b', lower):
            return abbrev
    return ""


def _classify_and_write(
    title: str,
    fetched_data: dict,
    source_url: str,
    source_name: str,
    source_credit: str = "",
    needs_review: bool = False,
    recipes: Optional[dict] = None,
) -> dict:
    """
    Shared final stage of recipe intake: health classify, write .md, save metadata.
    Called by both _full_recipe_add (URL path) and process_recipe_image (image path).

    source_url / source_name: populated for URL-sourced recipes.
    source_credit:            plain-text attribution for book/non-URL recipes.
    needs_review:             True for image-extracted recipes (vision can misread).
    recipes:                  pass in if already loaded to avoid a double load.

    Returns {title, health, time} on success.
    """
    import anthropic as _anthropic

    ings_raw = fetched_data.get("ingredients", [])

    try:
        client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        health_resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            messages=[{"role": "user", "content": (
                f"Classify as Heart-Healthy, Moderate, or Indulgent.\n"
                f"Recipe: {title}\nIngredients: {ings_raw[:10]}\n"
                "Reply with exactly one of: Heart-Healthy, Moderate, Indulgent"
            )}],
        )
        health = health_resp.content[0].text.strip()
        if health not in ("Heart-Healthy", "Moderate", "Indulgent"):
            health = "Moderate"
    except Exception:
        health = "Moderate"

    title_en: Optional[str] = None
    try:
        client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        trans_resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            messages=[{"role": "user", "content": (
                "If this recipe title is a full sentence or phrase in a non-English language "
                "(e.g. Spanish, Italian, French), provide a concise English translation. "
                "If it is already in English, or if it is an internationally recognized dish name "
                "used in English cooking contexts (e.g. yakisoba, bulgogi, mapo tofu, pad thai, "
                "ramen, pho, bibimbap), reply with exactly: null\n"
                f"Title: {title}"
            )}],
        )
        trans_text = trans_resp.content[0].text.strip()
        title_en = None if trans_text.lower() == "null" else trans_text
    except Exception:
        title_en = None

    md_path = _write_recipe_md(title, fetched_data, source_url, source_name, source_credit)

    if recipes is None:
        recipes = _load_metadata()

    if not any(k.lower() == title.lower() for k in recipes):
        time_str = fetched_data.get("time", "")
        mins = _parse_minutes(time_str)
        entry: dict = {
            "status": "active",
            "source": source_name or source_credit or "user submission",
            "source_url": source_url,
            "url": source_url,
            "filename": md_path.name,
            "time": time_str,
            "cuisine_type": fetched_data.get("cuisine", ""),
            "health_classification": health,
            "meal_type": "Weekend" if mins > 60 else "Weeknight",
            "times_cooked": 0,
            "last_cooked_date": None,
            "ingredients_raw": ings_raw,
            "instructions": fetched_data.get("instructions", []),
            "ingredients": [],
            "title_en": title_en,
        }
        if needs_review:
            entry["needs_review"] = True
        recipes[title] = entry
        _save_metadata(recipes)

    time_str = fetched_data.get("time", "")
    return {"title": title, "health": health, "time": time_str}


def _full_recipe_add(url: str, fetched_data: Optional[dict]) -> dict:
    """
    Full parse-and-add pipeline for a new recipe URL.
    fetched_data may already be populated from a prior _fetch_recipe_data call.
    Returns {title, health, time} on success or {error: ...} on failure.
    """
    import anthropic as _anthropic

    # Fall back to Haiku HTML extraction if ld+json was missing or incomplete.
    # ld+json often has a title but empty recipeIngredient/recipeInstructions.
    _needs_extraction = (
        not fetched_data
        or not fetched_data.get("title")
        or not fetched_data.get("ingredients")
        or not fetched_data.get("instructions")
    )
    if _needs_extraction:
        try:
            resp = httpx.get(url, headers=_FETCH_HEADERS, timeout=20, follow_redirects=True)
            resp.raise_for_status()
            html = resp.text[:40000]
        except Exception as e:
            return {"error": f"Could not fetch URL: {e}"}

        client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        try:
            extraction = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2000,
                messages=[{"role": "user", "content": (
                    "Extract this recipe from the HTML. Return valid JSON only with keys: "
                    "title (string), ingredients (list of strings), "
                    "instructions (list of step strings), time (e.g. '45 minutes'), "
                    "servings (string), cuisine (string).\n\nHTML:\n" + html
                )}],
            )
            extracted = json.loads(extraction.content[0].text)
            # Merge: keep ld+json title if we had one, fill missing fields from Haiku
            if fetched_data and fetched_data.get("title"):
                extracted["title"] = fetched_data["title"]
            fetched_data = extracted
        except Exception as e:
            return {"error": f"Could not extract recipe: {e}"}

    if not fetched_data or not fetched_data.get("title"):
        return {"error": "Could not determine recipe title from page"}

    title = fetched_data["title"].strip()
    source_name = urlparse(url).netloc.replace("www.", "")
    return _classify_and_write(title, fetched_data, url, source_name)


# ---------------------------------------------------------------------------
# Meal plan parsing
# ---------------------------------------------------------------------------

_PLAN_LINE_RE = re.compile(
    r"^(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+\d+/\d+\s+(.+?)\s*(?:\[|$)"
)

# Meal names containing these terms are not real recipes — skip feedback prompts
_SKIP_FEEDBACK_KEYWORDS = frozenset([
    "takeout", "eating out", "restaurant", "leftovers", "leftover",
    "friends", "no cooking", "out to dinner", "pizza delivery",
    "going out to eat", "going out",
])

# Signals in Ashley's reply that mean a day has no dinner to cook
_OUT_SIGNALS = frozenset([
    "going out", "eating out", "out to eat", "out to dinner",
    "dine out", "dining out", "no dinner", "no cooking",
])


def _parse_last_plan() -> list:
    if not WEEKLYPLAN_DIR.exists():
        return []
    today = date.today()
    dated = []
    for f in WEEKLYPLAN_DIR.glob("mealplan_*.json"):
        try:
            d = date.fromisoformat(f.stem.replace("mealplan_", ""))
            dated.append((d, f))
        except ValueError:
            continue
    dated.sort(key=lambda x: x[0], reverse=True)
    # Plan filename is the Monday date; week starts Sunday (d-1). Allow d up to tomorrow
    # so Sunday is covered by the current week's plan rather than falling back to last week.
    plan_file = next((f for d, f in dated if d <= today + timedelta(days=1)), None)
    if not plan_file:
        return []
    try:
        data = json.loads(plan_file.read_text())
    except Exception:
        return []
    meals = []
    for m in data.get("meals", []):
        name = m.get("title", "")
        if not name:
            continue
        if any(kw in name.lower() for kw in _SKIP_FEEDBACK_KEYWORDS):
            continue
        meals.append({"name": name, "day": m.get("day", ""), "sms_feedback": None})
    return meals


def _merge_feedback(meals: list) -> list:
    if not FEEDBACK_CURRENT_FILE.exists():
        return meals
    try:
        data = json.loads(FEEDBACK_CURRENT_FILE.read_text())
        for entry in data.get("entries", []):
            recipe = entry.get("recipe", "")
            note = entry.get("note", "")
            sentiment = entry.get("sentiment", "")
            if not recipe:
                continue
            for meal in meals:
                if recipe.lower() in meal["name"].lower() or meal["name"].lower() in recipe.lower():
                    if not meal["sms_feedback"]:
                        meal["sms_feedback"] = f"{sentiment}: {note}" if sentiment else note
                    break
    except Exception:
        pass
    return meals


# ---------------------------------------------------------------------------
# Week date helpers
# ---------------------------------------------------------------------------

def _get_week_start() -> date:
    today = date.today()
    days_ahead = (0 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def _day_date_map(week_start: date) -> dict:
    m = {"Sun": week_start - timedelta(days=1)}
    for i, day in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]):
        m[day] = week_start + timedelta(days=i)
    return m


# ---------------------------------------------------------------------------
# Candidate loading and scoring  (mirrors suggest_meals.py)
# ---------------------------------------------------------------------------

RECENCY_WEEKS = 3
QUICK_THRESHOLD = 35
HIATUS_PROTEINS = ["salmon"]
KNOWN_CUISINES: set[str] = {k.lower() for k in _CUISINE_FAMILY_MAP}
KNOWN_FAMILIES: set[str] = {v.lower() for v in _CUISINE_FAMILY_MAP.values()}


def _register_cuisine(cuisine: str) -> None:
    """Add an unknown cuisine to config.json with itself as its own family."""
    if cuisine in _CUISINE_FAMILY_MAP:
        return
    _CUISINE_FAMILY_MAP[cuisine] = cuisine
    KNOWN_CUISINES.add(cuisine.lower())
    raw = json.loads(_CONFIG_PATH.read_text())
    raw.setdefault("cuisine_family_map", {})[cuisine] = cuisine
    _CONFIG_PATH.write_text(json.dumps(raw, indent=2, ensure_ascii=False))

# Source-name → cuisine-type aliases for cuisine_direction normalization.
# Keys are lowercase substrings to match against; values are canonical cuisine types.
_SOURCE_CUISINE_ALIASES: list[tuple[str, str]] = [
    ("serious eats",              "American"),
    ("america's test kitchen",    "American"),
    ("americastestkitchen",       "American"),
    ("atk",                       "American"),
    ("alton brown",               "American"),
    ("good eats",                 "American"),
    ("smitten kitchen",           "American"),
    ("deb perelman",              "American"),
    ("kenji",                     "American"),
    ("maangchi",                  "Korean"),
    ("hot thai kitchen",          "Thai"),
    ("hot thai",                  "Thai"),
    ("woks of life",              "Chinese"),
    ("woksoflife",                "Chinese"),
    ("just one cookbook",         "Japanese"),
    ("joc",                       "Japanese"),
    ("viet world kitchen",        "Vietnamese"),
    ("indian healthy recipes",    "Indian"),
    ("hebbars kitchen",           "Indian"),
    ("hebbars",                   "Indian"),
    ("ranveer brar",              "Indian"),
    ("archana's kitchen",         "Indian"),
    ("archanaskitchen",           "Indian"),
    ("chetna makan",              "Indian"),
    ("chetna",                    "Indian"),
    ("kannamma cooks",            "Indian"),
    ("mediterranean dish",        "Mediterranean"),
    ("themediterraneandish",      "Mediterranean"),
    ("my greek dish",             "Greek"),
    ("mygreekdish",               "Greek"),
    ("feasting at home",          "Mediterranean"),
    ("feastingathome",            "Mediterranean"),
    ("giallozafferano",           "Italian"),
    ("cucchiaio",                 "Italian"),
    ("memorie di angelina",       "Italian"),
    ("memoriediangelina",         "Italian"),
    ("frank fariello",            "Italian"),
    ("cooking con claudia",       "Mexican"),
    ("pati jinich",               "Mexican"),
    ("patijinich",                "Mexican"),
    ("rick bayless",              "Mexican"),
    ("rickbayless",               "Mexican"),
    ("mexico in my kitchen",      "Mexican"),
    ("mexicoinmykitchen",         "Mexican"),
]


def _normalize_cuisine_direction(direction: str) -> tuple[str, Optional[str]]:
    """
    Normalize a cuisine_direction string, mapping source names to cuisine types.

    Returns (normalized_direction, note_for_caller).
    - If direction is empty → ("", None)
    - If direction matches a source alias → (cuisine_type, "Recognized 'X' as Y cuisine")
    - If direction contains a known cuisine keyword → (direction, None)
    - Otherwise → (direction, "Unrecognized cuisine direction 'X' — passing through")
    """
    if not direction or not direction.strip():
        return ("", None)

    d_lower = direction.strip().lower()

    # Check source aliases first (more specific)
    for alias, cuisine in _SOURCE_CUISINE_ALIASES:
        if alias in d_lower:
            note = f"Recognized '{direction}' as {cuisine} cuisine."
            return (cuisine, note)

    # Check if it already contains a known cuisine or family keyword
    for kc in KNOWN_CUISINES | KNOWN_FAMILIES:
        if kc in d_lower:
            return (direction.strip(), None)

    # Unknown — register it in config.json and pass through
    _register_cuisine(direction.strip())
    note = f"Registered new cuisine '{direction.strip()}' in config.json."
    return (direction.strip(), note)


# Keywords in reason string that map to protein labels (matches _PROTEIN_KEYWORDS labels)
_REASON_PROTEIN_MAP = [
    ("grilled fish", "Fish"), ("fish", "Fish"), ("salmon", "Fish"), ("cod", "Fish"),
    ("tilapia", "Fish"), ("seafood", "Fish"), ("shrimp", "Shrimp"),
    ("chicken", "Chicken"), ("beef", "Beef"), ("pork", "Pork"), ("lamb", "Lamb"),
    ("vegetarian", "Vegetarian"), ("veggie", "Vegetarian"), ("meatless", "Vegetarian"),
    ("pasta", "Pasta"), ("noodle", "Pasta"),
]

# Category keywords for plan-wide tally (e.g. pasta-heavy detection)
_CATEGORY_KEYWORDS = {
    "pasta": ["pasta", "spaghetti", "linguine", "penne", "rigatoni", "noodle", "fettuccine", "tagliatelle", "orzo"],
    "salad": ["salad"],
    "tacos": ["taco", "tinga"],
    "soup": ["soup", "stew", "chili"],
}

_INVENTORY_STOPWORDS = {
    "oz", "lb", "lbs", "pkg", "bag", "box", "can", "jar", "bottle", "fresh", "frozen", "dried",
    "costco", "package", "packages", "individual", "pieces", "piece", "thawed", "homemade",
    "batch", "bags", "kg", "g", "and", "the", "a", "an", "in", "bone", "boneless", "skinless",
    "country", "style",
    # brand names
    "kroger", "private", "selection", "prego", "cecco", "martins", "rosarita",
    "driscolls", "fage", "kind", "thomas", "stacys", "lgm", "saint", "humboldt",
    "pirate", "angel", "food", "chiquita", "stouffers", "mila",
}

_PANTRY_CATEGORIES = {"Pantry", "Dry Goods", "Dairy"}


def _load_inventory_keywords() -> list:
    """Return list of {name, keywords, category} from inventory.json."""
    try:
        inv_path = _CONFIG.get("inventory_path", "")
        if not inv_path:
            return []
        data = json.loads(Path(inv_path).read_text())
        items = []
        for item in data.get("items", []):
            name = item.get("name", "").lower()
            qty = item.get("quantity", 0)
            if not name or qty == 0:
                continue
            words = [w for w in name.split() if w not in _INVENTORY_STOPWORDS and len(w) > 2]
            items.append({"name": name, "keywords": words, "category": item.get("category", "")})
        return items
    except Exception:
        return []


def _deduct_inventory_protein(ingredients: list) -> list:
    """
    Deduct 1 unit from the best-matching protein inventory item for each protein
    ingredient in the recipe. Always 1 unit regardless of recipe quantity — everything
    is frozen in meal-sized portions.

    Returns list of {"item", "from_qty", "to_qty"} for each deduction made.
    """
    inv_path_str = _CONFIG.get("inventory_path", "")
    if not inv_path_str:
        return []
    inv_path = Path(inv_path_str)
    if not inv_path.exists():
        return []

    try:
        inv = json.loads(inv_path.read_text())
    except Exception:
        return []

    items = inv.get("items", [])
    protein_ingredients = [i for i in ingredients if i.get("category") == "Proteins"]
    deductions = []

    def _kw(name: str) -> set:
        """Extract match-ready keywords: strip stopwords, normalise plurals."""
        words = set()
        for w in name.lower().split():
            if w in _INVENTORY_STOPWORDS or len(w) <= 2:
                continue
            words.add(w)
            if w.endswith("s") and len(w) > 4:
                words.add(w[:-1])  # "tenderloins" → also add "tenderloin"
        return words

    for ing in protein_ingredients:
        ing_words = _kw(ing.get("name", ""))
        if not ing_words:
            continue

        best_item = None
        best_score = 0
        for item in items:
            if item.get("category") != "Proteins":
                continue
            if item.get("quantity", 0) <= 0:
                continue
            item_words = _kw(item.get("name", ""))
            score = len(ing_words & item_words)
            if score > best_score:
                best_score = score
                best_item = item

        if best_item and best_score >= 1:
            old_qty = best_item["quantity"]
            best_item["quantity"] = max(0, old_qty - 1)
            deductions.append({
                "item": best_item["name"],
                "from_qty": old_qty,
                "to_qty": best_item["quantity"],
            })

    if deductions:
        inv["last_updated"] = date.today().isoformat()
        try:
            inv_path.write_text(json.dumps(inv, indent=2))
        except Exception:
            pass

    return deductions


def _inventory_match(recipe_name: str, ingredients: list, inventory: list) -> tuple:
    """
    Return (broad_match, protein_specific, pantry_specific) for a recipe.
      broad_match:      bool — any protein keyword in recipe matches a stocked protein
      protein_specific: list — protein inventory items with all keywords in recipe
      pantry_specific:  list — pantry/dry-goods/dairy items with specific match
    Mirrors inventory_match() in suggest_meals.py exactly.
    """
    if not inventory:
        return False, [], []

    name_lower = recipe_name.lower()
    ing_text = " ".join(
        i.get("name", "") if isinstance(i, dict) else str(i) for i in ingredients
    ).lower()
    searchable = f"{name_lower} {ing_text}"

    protein_specific = []
    pantry_specific = []
    broad = False

    for item in inventory:
        kws = item["keywords"]
        if not kws:
            continue
        if item["category"] == "Proteins":
            if all(kw in searchable for kw in kws):
                protein_specific.append(item["name"])
            elif any(kw in searchable for kw in kws):
                broad = True
        elif item["category"] in _PANTRY_CATEGORIES:
            if len(kws) >= 2 and all(kw in searchable for kw in kws):
                pantry_specific.append(item["name"])
            elif len(kws) == 1 and len(kws[0]) >= 5 and kws[0] in searchable:
                pantry_specific.append(item["name"])

    return broad, protein_specific, pantry_specific


def _inventory_boost(recipe_name: str, ingredients: list, inventory: list) -> int:
    """Return a score bonus (negative = better) if recipe uses inventory items."""
    if not inventory:
        return 0
    name_lower = recipe_name.lower()
    ing_text = " ".join(
        i.get("name", "") if isinstance(i, dict) else str(i) for i in ingredients
    ).lower()
    searchable = f"{name_lower} {ing_text}"
    bonus = 0
    for item in inventory:
        kws = item["keywords"]
        if not kws:
            continue
        if item["category"] == "Proteins" and all(kw in searchable for kw in kws):
            bonus -= 12
        elif any(kw in searchable for kw in kws):
            bonus -= 5
    return bonus


def _plan_tallies(selected: dict, recipes: dict) -> dict:
    """Return cuisine counts and category counts for the current plan."""
    cuisine_counts: dict = {}
    category_counts: dict = {}
    for name in selected.values():
        key = _find_recipe_key(name, recipes)
        r = recipes.get(key, {}) if key else {}
        cuisine = r.get("cuisine_type", r.get("cuisine", "")).lower()
        if cuisine:
            cuisine_counts[cuisine] = cuisine_counts.get(cuisine, 0) + 1
        name_lower = name.lower()
        for cat, keywords in _CATEGORY_KEYWORDS.items():
            if any(kw in name_lower for kw in keywords):
                category_counts[cat] = category_counts.get(cat, 0) + 1
    return {"cuisine": cuisine_counts, "category": category_counts}

_PROTEIN_KEYWORDS = [
    ("salmon", "Fish"), ("fish", "Fish"), ("shrimp", "Shrimp"), ("cod", "Fish"),
    ("tilapia", "Fish"), ("pork", "Pork"), ("lamb", "Lamb"), ("beef", "Beef"),
    ("chicken", "Chicken"), ("turkey", "Turkey"), ("tofu", "Vegetarian"),
    ("chickpea", "Vegetarian"), ("mushroom", "Vegetarian"), ("lentil", "Vegetarian"),
    ("bean", "Vegetarian"), ("vegetarian", "Vegetarian"),
    ("pasta", "Pasta"), ("spaghetti", "Pasta"), ("noodle", "Pasta"),
]


def _get_protein(name: str) -> str:
    lower = name.lower()
    for keyword, label in _PROTEIN_KEYWORDS:
        if keyword in lower:
            return label
    return "Other"


def _weeks_since(date_str: Optional[str]) -> float:
    if not date_str:
        return 999.0
    try:
        cooked = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (date.today() - cooked).days / 7
    except Exception:
        return 999.0


def _parse_minutes(time_str: str) -> int:
    if not time_str:
        return 999
    mins = 0
    h = re.search(r"(\d+)\s*hour", time_str, re.IGNORECASE)
    if h:
        mins += int(h.group(1)) * 60
    m = re.search(r"(\d+)\s*min", time_str, re.IGNORECASE)
    if m:
        mins += int(m.group(1))
    return mins if mins > 0 else 999


def _candidate_score(c: dict) -> float:
    s = 0.0
    age_weeks = c.get("age_weeks", 0)
    s -= min(age_weeks, 52) * 2
    health = c.get("health", "Moderate")
    if health == "Heart-Healthy":
        s -= 15
    elif health == "Indulgent":
        s += 10
    s += c.get("times_cooked", 0) * 2
    if c.get("is_grill") and c.get("is_grill_season"):
        s -= 4
    adult_score = c.get("adult_score")
    if adult_score is not None:
        if adult_score < 0.5:
            s += 15
        elif adult_score >= 0.9:
            s -= 6
    if c.get("kid_approved"):
        s -= 3
    if c.get("inv_specific"):
        s -= 7
    elif c.get("inv_broad"):
        s -= 3
    if c.get("inv_pantry"):
        s -= 2
    return s


def _load_candidates(quick_days: list) -> list:
    recipes = _load_metadata()
    today = date.today()
    is_grill_season = 4 <= today.month <= 9
    inventory = _load_inventory_keywords()  # loaded once for the whole pass

    candidates = []
    for name, r in recipes.items():
        if r.get("status") != "active":
            continue
        if any(h in name.lower() for h in HIATUS_PROTEINS):
            continue

        age_weeks = _weeks_since(r.get("last_cooked_date"))
        if age_weeks < RECENCY_WEEKS:
            continue

        minutes = _parse_minutes(r.get("time", ""))
        is_slow = r.get("cooking_method") == "slow_cooker"
        is_quick = minutes <= QUICK_THRESHOLD or is_slow
        is_grill = r.get("cooking_method") == "grill"
        protein = _get_protein(name)
        feedback = r.get("feedback", [])

        adults = [f for f in feedback if f.get("person", "").lower() in ADULT_NAMES]
        adult_score = None
        if adults:
            liked = sum(1 for f in adults if f.get("sentiment") == "liked")
            adult_score = liked / len(adults)

        kids = [f for f in feedback if f.get("person", "").lower() not in ADULT_NAMES]
        kid_friendly = None
        if kids:
            liked = sum(1 for f in kids if f.get("sentiment") == "liked")
            kid_friendly = liked > len(kids) / 2
        kid_approved = bool(r.get("kid_approved")) or bool(kid_friendly)

        inv_broad, inv_specific, inv_pantry = _inventory_match(
            name, r.get("ingredients", []), inventory
        )

        c = {
            "name": name,
            "cuisine": r.get("cuisine_type", r.get("cuisine", "Unknown")),
            "health": r.get("health", "Moderate"),
            "protein": protein,
            "minutes": minutes,
            "time_str": r.get("time", ""),
            "is_quick": is_quick,
            "is_grill": is_grill,
            "is_grill_season": is_grill_season,
            "times_cooked": r.get("times_cooked", 0),
            "last_cooked": r.get("last_cooked_date") or "never",
            "age_weeks": age_weeks,
            "meal_type": r.get("meal_type", "Weeknight"),
            "method": r.get("cooking_method", ""),
            "weeknight_effort": r.get("weeknight_effort", ""),
            "adult_score": adult_score,
            "kid_approved": kid_approved,
            "inv_broad": inv_broad,
            "inv_specific": inv_specific,
            "inv_pantry": inv_pantry,
        }
        c["score"] = _candidate_score(c)
        candidates.append(c)

    return sorted(candidates, key=lambda c: c["score"])


def _direction_matches_cuisine(cuisine: str, direction_lower: str) -> bool:
    """Return True if direction names this cuisine directly or by family."""
    if cuisine.lower() in direction_lower:
        return True
    fam = _CUISINE_FAMILY_MAP.get(cuisine, "")
    return bool(fam) and fam.lower() in direction_lower


def _parse_cuisine_slots(direction: str) -> dict:
    """Parse 'one Asian, one Mexican' → {'Asian': 1, 'Mexican': 1}.
    Keys are family names. Matches both specific cuisines and family names."""
    if not direction:
        return {}
    d_lower = direction.lower()
    slots: dict = {}

    def _set_slot(family: str, count: int) -> None:
        slots[family] = min(slots.get(family, count), count)

    # Specific cuisines → key by their family
    for cuisine, family in _CUISINE_FAMILY_MAP.items():
        c_lower = cuisine.lower()
        if re.search(rf"\bone\s+{re.escape(c_lower)}\b", d_lower):
            _set_slot(family, 1)
        else:
            m = re.search(rf"\b(\d+)\s+{re.escape(c_lower)}\b", d_lower)
            if m:
                _set_slot(family, int(m.group(1)))

    # Family names directly (e.g. "one Asian")
    for family in set(_CUISINE_FAMILY_MAP.values()):
        f_lower = family.lower()
        if re.search(rf"\bone\s+{re.escape(f_lower)}\b", d_lower):
            _set_slot(family, 1)
        else:
            m = re.search(rf"\b(\d+)\s+{re.escape(f_lower)}\b", d_lower)
            if m:
                _set_slot(family, int(m.group(1)))

    return slots


def _select_meals(candidates: list, quick_days: list, cuisine_direction: Optional[str]) -> dict:
    pool = list(candidates)

    if cuisine_direction and cuisine_direction.lower() not in ("what we've got", ""):
        c_lower = cuisine_direction.lower()
        pool.sort(key=lambda c: (0 if _direction_matches_cuisine(c.get("cuisine", ""), c_lower) else 1, c["score"]))

    # Include never-tried recipes matching cuisine direction
    recipes = _load_metadata()
    if cuisine_direction and cuisine_direction.lower() not in ("what we've got", ""):
        c_lower = cuisine_direction.lower()
        for name, meta in recipes.items():
            cuisine_val = meta.get("cuisine_type", meta.get("cuisine", ""))
            if (
                meta.get("times_cooked", 0) == 0
                and meta.get("status") not in ("disliked", "ignored")
                and _direction_matches_cuisine(cuisine_val, c_lower)
                and not any(c["name"] == name for c in pool)
            ):
                pool.insert(0, {
                    "name": name,
                    "cuisine": meta.get("cuisine_type", meta.get("cuisine", "")),
                    "health": meta.get("health", "Moderate"),
                    "protein": _get_protein(name),
                    "minutes": 30,
                    "time_str": meta.get("time", ""),
                    "is_quick": True,
                    "meal_type": "Weeknight",
                    "score": -100,
                })

    cuisine_slots = _parse_cuisine_slots(cuisine_direction or "")
    quick_set = {d.lower() for d in quick_days}
    selected: dict = {}
    used_proteins: set = set()
    cuisine_family_counts: dict = {}
    indulgent_count = 0

    def _pick(days_subset, require_quick=False, require_weekend=False):
        nonlocal indulgent_count
        for day in days_subset:
            if day in selected:
                continue
            for c in pool:
                if c["name"] in selected.values():
                    continue
                if require_quick and not c["is_quick"]:
                    continue
                if require_weekend and c.get("meal_type") != "Weekend":
                    continue
                # Cap Indulgent at 1 per week
                if c.get("health") == "Indulgent" and indulgent_count >= 1:
                    continue
                protein = c["protein"]
                if protein in used_proteins and len(pool) > len(days_subset) * 2:
                    continue
                # Enforce per-cuisine slots when direction explicitly quantifies them
                fam = _CUISINE_FAMILY_MAP.get(c.get("cuisine", ""), c.get("cuisine", ""))
                slot_cap = cuisine_slots.get(fam)
                if slot_cap is not None and cuisine_family_counts.get(fam, 0) >= slot_cap and len(pool) > len(days_subset) * 2:
                    continue
                selected[day] = c["name"]
                used_proteins.add(protein)
                cuisine_family_counts[fam] = cuisine_family_counts.get(fam, 0) + 1
                if c.get("health") == "Indulgent":
                    indulgent_count += 1
                break

    _pick(["Sat", "Sun"], require_weekend=True)
    quick_abbrevs = [a for a in DAYS_ORDER if a.lower() in quick_set or a[:3].lower() in quick_set]
    _pick(quick_abbrevs, require_quick=True)
    _pick(DAYS_ORDER)
    leftovers = [c for c in pool if c["name"] not in selected.values()]
    for day in DAYS_ORDER:
        if day not in selected and leftovers:
            selected[day] = leftovers.pop(0)["name"]

    return selected


# ---------------------------------------------------------------------------
# Swap helpers  (structured parse + Claude fallback, mirrors menu_workflow.py)
# ---------------------------------------------------------------------------

def _parse_swap(text: str, selected: dict, week_start: date) -> Optional[dict]:
    """Parse structured swap: 'swap 3 to pasta', 'change tuesday to tacos'."""
    lowered = text.lower()
    ordered = [(day, selected[day]) for day in DAYS_ORDER if day in selected]

    # Numbered swap: "swap 3 to X"
    num_m = re.search(r'(?:swap|change|replace)\s+(\d+)\s+(?:to|with|for)\s+(.+)', lowered)
    if num_m:
        idx = int(num_m.group(1)) - 1
        new_name = num_m.group(2).strip().rstrip(".").title()
        if 0 <= idx < len(ordered):
            day = ordered[idx][0]
            new_selected = dict(selected)
            new_selected[day] = new_name
            return new_selected

    # Day-named swap: "change tuesday to X"
    for day_name, abbrev in DAY_NAME_MAP.items():
        if day_name in lowered and abbrev in selected:
            day_m = re.search(rf'{re.escape(day_name)}\s+(?:to|with|for)\s+(.+)', lowered)
            if day_m:
                new_name = day_m.group(1).strip().rstrip(".").title()
                new_selected = dict(selected)
                new_selected[abbrev] = new_name
                return new_selected

    return None


def _claude_swap(text: str, selected: dict, cuisine_direction: Optional[str]) -> Optional[dict]:
    """Use Claude Haiku to interpret natural language swap requests."""
    import anthropic as _anthropic

    ordered = [(day, selected[day]) for day in DAYS_ORDER if day in selected]
    meal_list = "\n".join(f"{i + 1}. {day}: {name}" for i, (day, name) in enumerate(ordered))
    already_selected = set(selected.values())
    direction_lower = (cuisine_direction or "").lower()

    want_idea = any(kw in text.lower() for kw in ("idea", "new", "something different", "haven't tried", "never had"))

    recipes = _load_metadata()
    active_candidates = [
        (name, meta)
        for name, meta in recipes.items()
        if meta.get("status") not in ("disliked", "ignored")
        and meta.get("times_cooked", 0) > 0
        and name not in already_selected
        and not any(h in name.lower() for h in HIATUS_PROTEINS)
    ]
    # "idea_candidates" = untried recipes (times_cooked == 0), regardless of status
    idea_candidates = [
        (name, meta)
        for name, meta in recipes.items()
        if meta.get("status") not in ("disliked", "ignored")
        and meta.get("times_cooked", 0) == 0
        and name not in already_selected
        and not any(h in name.lower() for h in HIATUS_PROTEINS)
    ]

    def _score(item):
        name, meta = item
        times = meta.get("times_cooked", 0)
        cuisine_bonus = -1 if direction_lower and meta.get("cuisine_type", meta.get("cuisine", "")).lower() in direction_lower else 0
        return (times, cuisine_bonus)

    active_candidates.sort(key=_score)
    idea_candidates.sort(key=_score)

    # Prepend ideas when user signals wanting something new; otherwise append
    if want_idea:
        candidates = idea_candidates + active_candidates
    else:
        candidates = active_candidates + idea_candidates

    candidate_names = [name for name, _ in candidates[:60]]

    try:
        client = _anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    "A user is reviewing a weekly dinner plan and giving natural language feedback.\n\n"
                    f"Current plan:\n{meal_list}\n\n"
                    f"User feedback: \"{text}\"\n\n"
                    f"Available replacements (in preference order): {json.dumps(candidate_names)}\n\n"
                    "Identify which meals to replace (by day abbreviation Mon/Tue/Wed/Thu/Fri/Sat/Sun) "
                    "and choose the best replacement from the available list. "
                    "Return JSON array only, no explanation:\n"
                    '[{"day": "Mon", "to": "Replacement Meal Name"}, ...]\n\n'
                    "If the feedback doesn't clearly request any changes, return []."
                ),
            }],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        swaps = json.loads(raw)
        if not isinstance(swaps, list) or not swaps:
            return None
        new_selected = dict(selected)
        candidate_set = {n.lower() for n in candidate_names}
        changed = False
        for swap in swaps:
            day = (swap.get("day") or "").strip()
            to_meal = (swap.get("to") or "").strip()
            if day and to_meal and day in new_selected:
                to_lower = to_meal.lower()
                is_placeholder = any(kw in to_lower for kw in _SKIP_FEEDBACK_KEYWORDS)
                if not is_placeholder and to_lower not in candidate_set:
                    return None
                new_selected[day] = "Going Out to Eat" if is_placeholder else to_meal
                changed = True
        return new_selected if changed else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Recipe URL helpers
# ---------------------------------------------------------------------------

def _ensure_url_in_library(url: str) -> Optional[str]:
    """
    Given a URL from Ashley's message, ensure the recipe is in the library.
    - If a recipe already has this source_url, returns its name.
    - If not, fetches recipe data and adds it as a new active entry.
    - Returns the recipe name, or None if the URL couldn't be fetched.
    """
    url = url.rstrip(".,)")
    recipes = _load_metadata()

    # Check if already in library by source_url
    for name, meta in recipes.items():
        if isinstance(meta, dict) and meta.get("source_url", "").rstrip("/") == url.rstrip("/"):
            return name

    # Try to fetch
    recipe_data = _fetch_recipe_data(url)
    if not recipe_data or not recipe_data.get("title"):
        return None

    title = recipe_data["title"]
    source = url.split("/")[2]  # domain as source name

    existing_key = _find_recipe_key(title, recipes)
    if existing_key:
        recipes[existing_key]["source_url"] = url
        _save_metadata(recipes)
        return existing_key

    # New recipe — write .md and add to metadata
    RECIPES_DIR.mkdir(exist_ok=True)
    md_path = _write_recipe_md(title, recipe_data, url, source)
    recipes[title] = {
        "title": title,
        "filename": md_path.name,
        "source": source,
        "source_url": url,
        "cuisine": recipe_data.get("cuisine", ""),
        "meal_type": "",
        "health": "",
        "times_cooked": 0,
        "time": recipe_data.get("time", ""),
        "servings": str(recipe_data.get("servings", "")),
        "status": "active",
        "cooking_method": "",
        "last_cooked_date": None,
        "ingredients": [],
        "ingredients_raw": recipe_data.get("ingredients", []),
        "instructions": recipe_data.get("instructions", []),
        "needs_review": True,
    }
    _save_metadata(recipes)
    return title


def _recipe_url(recipe_name: str, metadata_entry: dict) -> str:
    """Return the best URL for a recipe — GitHub Pages if .md exists, else Dropbox."""
    filename = metadata_entry.get("filename", "")
    if filename and filename.endswith(".md"):
        stem = Path(filename).stem
        if GITHUB_BASE_URL:
            return f"{GITHUB_BASE_URL}/{stem}"
    if DROPBOX_BASE_URL and filename:
        return f"{DROPBOX_BASE_URL}&preview={filename}"
    return ""


# ---------------------------------------------------------------------------
# Idea recipe: fetch + activate
# ---------------------------------------------------------------------------

_FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def _parse_duration(iso: str) -> str:
    if not iso:
        return ""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", iso)
    if not m:
        return ""
    h, mins = int(m.group(1) or 0), int(m.group(2) or 0)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h > 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins > 1 else ''}")
    return " ".join(parts)


def _fetch_recipe_data(url: str) -> Optional[dict]:
    """Fetch ld+json Recipe schema from URL. Returns structured dict or None."""
    try:
        resp = httpx.get(url, headers=_FETCH_HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, AttributeError):
            continue
        candidates = data if isinstance(data, list) else data.get("@graph", [data])
        for item in candidates:
            if not isinstance(item, dict):
                continue
            t = item.get("@type", "")
            if "Recipe" not in (t if isinstance(t, list) else [t]):
                continue
            instructions = [
                (s.get("text", "") if isinstance(s, dict) else s).strip()
                for s in item.get("recipeInstructions", [])
            ]
            return {
                "title": item.get("name", "").strip(),
                "time": _parse_duration(item.get("totalTime") or item.get("cookTime", "")),
                "servings": str(item.get("recipeYield", "")),
                "ingredients": item.get("recipeIngredient", []),
                "instructions": instructions,
                "cuisine": item.get("recipeCuisine", ""),
            }
    return None


def _write_recipe_md(title: str, recipe_data: dict, source_url: str, source_name: str,
                     source_credit: str = "") -> Path:
    """Write a recipe .md file to RECIPES_DIR. Returns the path."""
    filename = title.replace(" ", "_") + ".md"
    path = RECIPES_DIR / filename
    if source_url:
        attribution = f"**Adapted from**: [{source_name or source_url}]({source_url})"
    elif source_credit:
        attribution = f"**Source**: {source_credit}"
    else:
        attribution = ""
    video_url = recipe_data.get("video_url", "")
    video_line = f"**Watch**: [YouTube]({video_url})" if video_url and video_url != source_url else ""
    lines = [
        f"# {title}", "",
        f"**Time**: {recipe_data.get('time', '')}  ",
        f"**Yield**: {recipe_data.get('servings', '')}  ",
        attribution,
        video_line,
        "", "## Ingredients", "",
    ]
    for ing in recipe_data.get("ingredients", []):
        lines.append(f"- {ing}")
    lines += ["", "## Instructions", ""]
    for i, step in enumerate(recipe_data.get("instructions", []), 1):
        lines.append(f"{i}. {step}")
    lines.append("")
    path.write_text("\n".join(l for l in lines if l is not None), encoding="utf-8")
    return path


def _activate_idea_in_metadata(name: str, filename: str, recipe_data: dict,
                                source_url: str) -> bool:
    """Set recipe status to 'active' in metadata. Returns True on success."""
    recipes = _load_metadata()
    key = _find_recipe_key(name, recipes)
    if not key:
        return False
    recipes[key].update({
        "status": "active",
        "filename": filename,
        "time": recipe_data.get("time", recipes[key].get("time", "")),
        "cuisine": recipe_data.get("cuisine_type", recipe_data.get("cuisine", recipes[key].get("cuisine_type", recipes[key].get("cuisine", "")))),
    })
    _save_metadata(recipes)
    return True


def _try_auto_activate(name: str, recipes: dict) -> bool:
    """
    Attempt to fetch and activate an idea recipe from its source_url.
    Returns True if successful, False if fetch failed or no URL.
    """
    key = _find_recipe_key(name, recipes)
    if not key:
        return False
    entry = recipes[key]
    source_url = entry.get("source_url", "")
    if not source_url:
        return False

    recipe_data = _fetch_recipe_data(source_url)
    if not recipe_data:
        return False

    title = recipe_data.get("title") or name
    source_name = entry.get("source", source_url)
    md_path = _write_recipe_md(title, recipe_data, source_url, source_name)
    return _activate_idea_in_metadata(name, md_path.name, recipe_data, source_url)


# ---------------------------------------------------------------------------
# Plan JSON generation
# ---------------------------------------------------------------------------

def _build_plan_json(selected: dict, week_start: date, schedule_notes: list) -> dict:
    """Build mealplan dict. week_start is Monday; Sunday is week_start - 1."""
    import anthropic as _anthropic

    recipes = _load_metadata()
    day_to_date = _day_date_map(week_start)
    # Strip suggest_meals display annotations before writing to plan
    ordered = [(day, re.sub(r'\s*\[[^\]]+\]', '', selected[day]).strip()) for day in DAYS_ORDER if day in selected]

    week_sunday = week_start - timedelta(days=1)
    week_saturday = week_start + timedelta(days=5)

    meals_info = []
    for day, name in ordered:
        dt = day_to_date.get(day)
        date_iso = dt.isoformat() if dt else ""
        if any(kw in name.lower() for kw in _SKIP_FEEDBACK_KEYWORDS):
            meals_info.append({
                "day": day, "date": date_iso, "title": name,
                "health": "", "time": "", "url": "", "reminder": "Going out to eat — no dinner to cook",
            })
            continue
        key = _find_recipe_key(name, recipes)
        meta = recipes.get(key, {}) if key else {}
        health = meta.get("health", "Moderate")
        time_str = meta.get("time", "?")
        url = _recipe_url(name, meta)
        meals_info.append({
            "day": day, "date": date_iso, "title": name,
            "health": health, "time": time_str, "url": url, "reminder": "",
        })

    health_counts: dict = {}
    for m in meals_info:
        h = m["health"]
        if h:  # skip eating-out/placeholder days
            health_counts[h] = health_counts.get(h, 0) + 1

    week_start_display = week_sunday.strftime("%B %d")
    week_end_display = week_saturday.strftime("%B %d, %Y")
    schedule_context = "\n".join(schedule_notes) if schedule_notes else "No special schedule notes."
    meal_lines = "\n".join(
        f"{m['day']} {m['date']}: {m['title']} ({m['health']}, {m['time']})"
        for m in meals_info
        if m["health"]  # skip eating-out/placeholder days
    )

    reminder_prompt = (
        f"Generate the REMINDERS section for this weekly meal plan.\n\n"
        f"Week: {week_start_display} - {week_end_display}\n"
        f"Schedule notes: {schedule_context}\n\n"
        f"Meals:\n{meal_lines}\n\n"
        "Format: One line per day that has a meal, like:\n"
        "- MON: one-line timing/prep note\n\n"
        "Rules:\n"
        "- Only include days that have meals\n"
        "- Include suggested start times for weeknight meals over 30 min\n"
        "- Note schedule constraints from the schedule notes\n"
        "- Note special prep (marinating, chilling, slow cooker setup)\n"
        "- One line per day — these populate calendar events\n\n"
        "Return ONLY the reminder lines, no header."
    )

    try:
        client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": reminder_prompt}],
        )
        reminders_text = response.content[0].text.strip()
    except Exception:
        reminders_text = "\n".join(f"- {m['day'].upper()}: {m['title']}" for m in meals_info)

    day_reminders: dict = {}
    for line in reminders_text.splitlines():
        rm = re.match(r"^-\s+(Sun|Mon|Tue|Wed|Thu|Fri|Sat)[^:]*:\s*(.+)", line, re.IGNORECASE)
        if rm:
            day_reminders[rm.group(1)[:3].title()] = rm.group(2).strip()
    for m in meals_info:
        if not m["reminder"]:  # eating-out days already have reminder set
            m["reminder"] = day_reminders.get(m["day"], "")

    return {
        "week_start": week_sunday.isoformat(),
        "week_end": week_saturday.isoformat(),
        "generated_date": date.today().isoformat(),
        "balance": health_counts,
        "meals": meals_info,
    }


def _plan_summary_text(data: dict) -> str:
    """Plain-text plan summary for SMS notification."""
    sun = date.fromisoformat(data["week_start"])
    sat = date.fromisoformat(data["week_end"])
    lines = [f"WEEKLY MEAL PLAN: {sun.strftime('%B %d')} - {sat.strftime('%B %d, %Y')}", ""]
    for m in data.get("meals", []):
        d = date.fromisoformat(m["date"])
        lines.append(f"{m['day']} {d.month}/{d.day}  {m['title']} [{m['health']}] | {m['time']}")
    balance = data.get("balance", {})
    lines.append("")
    lines.append("BALANCE: " + ", ".join(f"{v} {k}" for k, v in sorted(balance.items())))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shopping CSV generation
# ---------------------------------------------------------------------------

# Canonical ingredient aliases — normalizes pantry staples before deduplication
_ING_ALIASES: dict[str, str] = {
    # Olive oil variants
    "extra-virgin olive oil":   "olive oil",
    "extra virgin olive oil":   "olive oil",
    # Salt variants
    "table salt":               "salt",
    "kosher salt":              "salt",
    "kosher or sea salt":       "salt",
    "fine salt":                "salt",
    "sea salt":                 "salt",
    "fleur de sel":             "salt",
    "flaky salt":               "salt",
    # Pepper variants
    "black pepper":             "pepper",
    "ground black pepper":      "pepper",
    "freshly ground pepper":    "pepper",
    "freshly ground black pepper": "pepper",
    "cracked black pepper":     "pepper",
    "white pepper":             "pepper",
    "ground white pepper":      "pepper",
    # Garlic variants
    "garlic clove":             "garlic",
    "garlic cloves":            "garlic",
    "minced garlic":            "garlic",
    # Ginger variants (fresh only — "ground ginger" is a dried spice, not aliased)
    "fresh ginger":             "ginger",
    "ginger root":              "ginger",
    "minced ginger":            "ginger",
    # Neutral oil variants
    "vegetable oil":            "neutral oil",
    "canola oil":               "neutral oil",
    "peanut oil":               "neutral oil",
    "grapeseed oil":            "neutral oil",
    "sunflower oil":            "neutral oil",
    "corn oil":                 "neutral oil",
    "oil":                      "neutral oil",
    # Chicken broth variants
    "chicken stock":            "chicken broth",
    "chicken bouillon":         "chicken broth",
    "low sodium chicken stock": "chicken broth",
    "low-sodium chicken stock": "chicken broth",
    "low sodium chicken broth": "chicken broth",
    "low-sodium chicken broth": "chicken broth",
    # Vegetable broth variants
    "vegetable stock":          "vegetable broth",
    "low sodium vegetable broth": "vegetable broth",
    "low-sodium vegetable broth": "vegetable broth",
    # Soy sauce variants
    "low sodium soy sauce":     "soy sauce",
    "low-sodium soy sauce":     "soy sauce",
    "tamari soy sauce":         "soy sauce",
    # Rice vinegar variants
    "natural rice vinegar":     "rice vinegar",
    "unseasoned rice vinegar":  "rice vinegar",
    "seasoned rice vinegar":    "rice vinegar",
    # Scallion / green onion variants
    "scallion":                 "green onions",
    "scallions":                "green onions",
    "green onion":              "green onions",
    # Cilantro variants
    "fresh cilantro":           "cilantro",
    "chopped cilantro":         "cilantro",
    "cilantro leaves":          "cilantro",
    # Parsley variants
    "fresh parsley":            "parsley",
    "chopped parsley":          "parsley",
    "flat-leaf parsley":        "parsley",
    "italian parsley":          "parsley",
    "fresh flat-leaf parsley":  "parsley",
    # Butter variants
    "unsalted butter":          "butter",
    "salted butter":            "butter",
    # Heavy cream variants
    "heavy whipping cream":     "heavy cream",
    "whipping cream":           "heavy cream",
    # Lemon juice variants
    "fresh lemon juice":        "lemon juice",
    "freshly squeezed lemon juice": "lemon juice",
    # Lime juice variants
    "fresh lime juice":         "lime juice",
    "freshly squeezed lime juice": "lime juice",
    # Flour variants
    "unbleached all-purpose flour": "all-purpose flour",
    "unbleached flour":         "all-purpose flour",
    "plain flour":              "all-purpose flour",
    # Onion variants
    "yellow onion":             "onion",
    "white onion":              "onion",
    "diced onion":              "onion",
    "chopped onion":            "onion",
    "chopped white onion":      "onion",
    "chopped yellow onion":     "onion",
    "medium onion":             "onion",
    # Red onion variants
    "red onion":                "red onions",
    # Chicken thigh variants
    "boneless, skinless chicken thighs":             "boneless skinless chicken thighs",
    "boneless skinless chicken thighs or breast cutlets": "boneless skinless chicken thighs",
    "boneless skinless chicken breast or thighs":   "boneless skinless chicken thighs",
    "chicken thighs boneless skinless":             "boneless skinless chicken thighs",
    "skinless boneless chicken thighs":             "boneless skinless chicken thighs",
    # Chicken breast variants
    "boneless, skinless chicken breasts":           "boneless skinless chicken breasts",
    "boneless skinless chicken breast":             "boneless skinless chicken breasts",
    # Mint variants (fresh only — "dried mint" is distinct, don't merge)
    "fresh mint":               "mint",
    "mint leaves":              "mint",
    # Basil variants (fresh only — dried basil is distinct)
    "fresh basil":              "basil",
    "basil leaves":             "basil",
    # NOTE: fresh thyme/rosemary intentionally NOT aliased to "thyme"/"rosemary"
    # because "dried thyme" and "dried rosemary" are different shopping items.
    # NOTE: cumin seeds, coriander seeds, ground ginger intentionally NOT aliased
    # to ground cumin / coriander / ginger — different products.
    # NOTE: plain "chopped tomatoes" intentionally NOT aliased — could be fresh or canned.
    # Only explicitly-canned variants are merged.
    "canned diced tomatoes":    "diced tomatoes",
    "canned chopped tomatoes":  "diced tomatoes",
}

def _canonical_ing(name: str) -> str:
    lower = name.lower().strip()
    return _ING_ALIASES.get(lower, lower)


def _display_ing(name: str) -> str:
    """Return canonical alias if one exists, else the original name (preserves case)."""
    lower = name.lower().strip()
    return _ING_ALIASES.get(lower, name)


def _condiment_ingredients(name: str) -> list[dict]:
    """Return the structured ingredients list for a named condiment, or [] if not found."""
    try:
        data = json.loads(CONDIMENTS_PATH.read_text())
        return data.get(name, {}).get("ingredients", [])
    except Exception:
        return []


def _build_shopping_csv(selected: dict, week_start: date) -> str:
    """
    Generate shopping CSV content, aggregating duplicate ingredients across recipes.
    Format: Item, Notes (qty + meal context), Date (earliest date needed as YYYY-MM-DD)

    Ingredient fallback order per CLAUDE.md:
      1. structured `ingredients` array
      2. `ingredients_raw` strings
      3. no ingredients → skip recipe
    """
    recipes = _load_metadata()
    day_to_date = _day_date_map(week_start)

    # Collect all occurrences: (canonical_key, display_name, qty_unit, meal_label, date_str)
    occurrences: list[tuple] = []

    def _add_structured(ing_list, meal_label, date_str):
        for ing in ing_list:
            ing_name = str(ing.get("name", "")).strip()
            if not ing_name:
                continue
            if _GARDEN_HERBS and any(herb in ing_name.lower() for herb in _GARDEN_HERBS):
                continue
            qty  = str(ing.get("quantity", "")).strip()
            unit = str(ing.get("unit", "")).strip()
            qty_unit = f"{qty} {unit}".strip()
            occurrences.append((_canonical_ing(ing_name), _display_ing(ing_name), qty_unit, meal_label, date_str))

    def _add_raw(raw_list, meal_label, date_str):
        for raw in raw_list:
            raw = str(raw).strip()
            if raw and not (_GARDEN_HERBS and any(herb in raw.lower() for herb in _GARDEN_HERBS)):
                occurrences.append((_canonical_ing(raw), _display_ing(raw), "", meal_label, date_str))

    for day in DAYS_ORDER:
        if day not in selected:
            continue
        name = selected[day]
        key = _find_recipe_key(name, recipes)
        if not key:
            continue
        cook_date = day_to_date.get(day)
        if not cook_date:
            continue
        date_str = cook_date.isoformat()
        structured = recipes[key].get("ingredients", [])
        if structured:
            _add_structured(structured, name, date_str)
        else:
            _add_raw(recipes[key].get("ingredients_raw", []), name, date_str)

        # Pull in condiment ingredients for any condiment_deps on this recipe
        for cdep in recipes[key].get("condiment_deps", []):
            cing = _condiment_ingredients(cdep)
            if cing:
                _add_structured(cing, f"{cdep} (for {name})", date_str)

    # Append lunch ingredients if Ashley has picked this week
    if LUNCH_STATE_FILE.exists():
        try:
            lunch = json.loads(LUNCH_STATE_FILE.read_text())
            pick = lunch.get("current_pick")
            if pick and lunch.get("status") == "selected":
                key = _find_recipe_key(pick, recipes)
                if key:
                    sat_date = (week_start - timedelta(days=2)).isoformat()
                    structured = recipes[key].get("ingredients", [])
                    if structured:
                        _add_structured(structured, "Ashley's lunch", sat_date)
                    else:
                        _add_raw(recipes[key].get("ingredients_raw", []), "Ashley's lunch", sat_date)
        except Exception:
            pass

    # Aggregate: group by canonical key, earliest date wins
    grouped: dict[str, dict] = {}
    for canon, display, qty_unit, meal_label, date_str in occurrences:
        if canon not in grouped:
            grouped[canon] = {"display": display, "date": date_str, "parts": []}
        elif date_str < grouped[canon]["date"]:
            grouped[canon]["date"] = date_str
        grouped[canon]["parts"].append((qty_unit, meal_label))

    # Build final rows
    rows = []
    for canon, g in grouped.items():
        parts = g["parts"]
        if len(parts) == 1:
            qty_unit, meal_label = parts[0]
            notes = f"{qty_unit} | {meal_label}".strip(" |") if qty_unit else meal_label
        else:
            note_parts = []
            seen: set = set()
            for qty_unit, meal_label in parts:
                short = meal_label.split(" with ")[0][:28]
                entry = f"{qty_unit} ({short})" if qty_unit else short
                if entry not in seen:
                    note_parts.append(entry)
                    seen.add(entry)
            notes = " + ".join(note_parts)
        rows.append({"Item": g["display"], "Notes": notes, "Date": g["date"]})

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["Item", "Notes", "Date"])
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Keanu outbox
# ---------------------------------------------------------------------------

def _send_outbox(handle: str, text: str) -> None:
    if not handle:
        return
    outbox = json.loads(OUTBOX_FILE.read_text()) if OUTBOX_FILE.exists() else []
    outbox.append({"handle": handle, "text": text})
    OUTBOX_FILE.write_text(json.dumps(outbox))


# ---------------------------------------------------------------------------
# Core finalization logic  (shared by handle_ashley_reply and finalize_plan)
# ---------------------------------------------------------------------------

def _do_finalize(activity: dict) -> dict:
    """
    Generate plan text, shopping CSV, launch apps, notify admin.
    Prep guide is no longer sent automatically — it is on-demand via get_prep_guide().
    Mutates and saves activity state to 'complete'.
    Returns the completed activity dict.
    """
    selected = activity.get("selected_meals", {})
    week_start = date.fromisoformat(activity["week_start"])
    schedule_notes = activity.get("schedule_notes", [])

    # Generate plan JSON
    plan_data = _build_plan_json(selected, week_start, schedule_notes)

    # Write plan file
    plan_path = WEEKLYPLAN_DIR / f"mealplan_{week_start.isoformat()}.json"
    shopping_path = WEEKLYPLAN_DIR / f"shopping_{week_start.isoformat()}.csv"
    WEEKLYPLAN_DIR.mkdir(exist_ok=True)
    plan_path.write_text(json.dumps(plan_data, indent=2, ensure_ascii=False))
    shopping_path.write_text(_build_shopping_csv(selected, week_start))
    os.chmod(plan_path, 0o666)
    os.chmod(shopping_path, 0o666)

    # Launch apps
    subprocess.Popen(["open", "/Applications/WeeklyShoppingList.app"])
    subprocess.Popen(["open", "/Applications/WeeklyMealCalendar.app"])

    # Summary notification to admin only
    summary = _plan_summary_text(plan_data)
    if ADMIN_HANDLE:
        _send_outbox(ADMIN_HANDLE, f"Plan ready!\n\n{summary}")

    # Clear the pending approval file
    if PENDING_FILE.exists():
        PENDING_FILE.unlink()

    activity["state"] = "complete"
    activity["plan_path"] = str(plan_path)
    activity["shopping_path"] = str(shopping_path)
    _save_activity(activity)
    return activity


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("menubuilder")


@mcp.tool()
def get_workflow_state() -> dict:
    """
    Return the current menu workflow activity state.

    Returns the full activity dict, or {"state": "idle"} if no active workflow.

    State values:
      idle                    — no active workflow
      awaiting_meal_logging   — last week meals loaded; record ratings or call done
      awaiting_suggestions    — feedback logged; call get_meal_suggestions
      awaiting_meal_approval  — meals proposed; review + swap or approve_menu
      awaiting_ashley_signoff — sent to Ashley; waiting for her reply
      awaiting_idea_activation — ideas on menu need content before finalizing
      complete                — plan written, apps launched, prep guide sent
    """
    return _load_activity()


@mcp.tool()
def start_menu_workflow() -> dict:
    """
    Initialize a new weekly menu build workflow.

    1. Drain the SMS feedback queue (process_feedback_queue.py)
    2. Parse the most recent mealplan_*.txt for last week's meals
    3. Merge any feedback from feedback_current.json onto those meals
    4. Write fresh activity state (menu_activity.json)

    Returns the new activity state. last_week_meals will show each meal
    with any pre-existing SMS feedback merged in.

    State after call: awaiting_meal_logging
    """
    cmd = [sys.executable, str(MENUBUILDER_DIR / "process_feedback_queue.py")]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception:
        pass

    meals = _merge_feedback(_parse_last_plan())

    # Cross-reference recipe_metadata: add times_cooked and skip feedback for recently logged meals
    try:
        meta_path = Path("/Users/Shared/cooking/recipe_metadata.json")
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            recipes = meta.get("recipes", meta) if isinstance(meta, dict) else meta
            cutoff = (date.today() - timedelta(days=10)).isoformat()
            for meal in meals:
                rkey = _find_recipe_key(meal["name"], recipes)
                if rkey:
                    meal["times_cooked"] = recipes[rkey].get("times_cooked", 0)
                    if not meal.get("sms_feedback") and recipes[rkey].get("last_cooked_date", "") >= cutoff:
                        meal["sms_feedback"] = "already logged"
    except Exception:
        pass

    week_start = _get_week_start()

    activity = {
        "state": "awaiting_meal_logging",
        "initiated_at": datetime.now().isoformat(),
        "week_start": week_start.isoformat(),
        "last_week_meals": meals,
        "schedule_notes": [],
        "cuisine_direction": None,
        "selected_meals": {},
        "ideas_on_menu": [],
        "quick_days": [],
    }
    _save_activity(activity)
    return activity


@mcp.tool()
def log_meal_feedback(feedback: str) -> dict:
    """
    Record feedback for last week's meals.

    Call with natural language feedback for each meal, then call with "done" to
    finalize. Feedback text is matched to the closest meal by keyword.

    When feedback == "done":
    - Processes each meal by rule:
        - sms_feedback starts with "not_cooked" / contains "not cooked" / "did not make"
          → skipped; not logged
        - sms_feedback starts with "disliked"
          → logged as cooked (it was made), added to disliked_meals for tombstone discussion
        - times_cooked == 0 (first cook)
          → logged as cooked, added to first_cook_meals for keep-it discussion
        - all other meals → auto-logged silently
    - Clears feedback_current.json
    - Advances state to awaiting_suggestions
    - Returns first_cook_meals, disliked_meals, not_cooked_meals for caller to action

    Args:
        feedback: Natural language feedback (e.g. "Monday was great, Thursday needed
                  more seasoning") or "done" to finalize.

    Returns the updated activity state.
    """
    activity = _load_activity()
    if activity.get("state") == "idle":
        return {"error": "No active workflow. Call start_menu_workflow first."}

    meals = activity.get("last_week_meals", [])

    if feedback.strip().lower() == "done":
        recipes = _load_metadata()
        today_str = date.today().isoformat()

        first_cook_meals = []   # times_cooked == 0; caller should ask keep-it question
        disliked_meals = []     # sentiment was disliked; caller should discuss tombstone
        not_cooked_meals = []   # explicitly skipped; do not log
        logged_count = 0
        all_deductions = []     # inventory protein deductions

        for meal in meals:
            key = _find_recipe_key(meal["name"], recipes)
            if not key:
                continue

            fb = (meal.get("sms_feedback") or "").lower()
            is_not_cooked = fb.startswith("not_cooked") or "not cooked" in fb or "did not make" in fb
            is_already_logged = fb == "already logged"
            is_disliked = fb.startswith("disliked")

            if is_not_cooked or is_already_logged:
                if is_not_cooked:
                    not_cooked_meals.append(meal["name"])
                continue  # don't log

            if is_disliked:
                disliked_meals.append(meal["name"])
                # still log as cooked — it was made, just didn't land

            was_first_cook = recipes[key].get("times_cooked", 0) == 0
            recipes[key]["times_cooked"] = recipes[key].get("times_cooked", 0) + 1
            recipes[key]["last_cooked_date"] = today_str
            logged_count += 1

            if was_first_cook:
                first_cook_meals.append(meal["name"])

            deductions = _deduct_inventory_protein(recipes[key].get("ingredients", []))
            all_deductions.extend(deductions)

        _save_metadata(recipes)

        if FEEDBACK_CURRENT_FILE.exists():
            FEEDBACK_CURRENT_FILE.write_text(json.dumps({"entries": []}, indent=2))
            try:
                FEEDBACK_CURRENT_FILE.chmod(0o666)
            except Exception:
                pass

        activity["state"] = "awaiting_suggestions"
        activity["first_cook_meals"] = first_cook_meals
        activity["disliked_meals"] = disliked_meals
        activity["not_cooked_meals"] = not_cooked_meals
        _save_activity(activity)
        return {
            **activity,
            "logged_count": logged_count,
            "first_cook_meals": first_cook_meals,
            "disliked_meals": disliked_meals,
            "not_cooked_meals": not_cooked_meals,
            "inventory_deductions": all_deductions,
        }

    lowered = feedback.lower()
    matched = False
    for meal in meals:
        words = [w for w in meal["name"].lower().split() if len(w) > 3]
        if words and any(w in lowered for w in words):
            existing = meal.get("sms_feedback") or ""
            meal["sms_feedback"] = (existing + " " + feedback).strip()
            matched = True
            break

    if not matched and meals:
        existing = meals[-1].get("sms_feedback") or ""
        meals[-1]["sms_feedback"] = (existing + " " + feedback).strip()

    activity["last_week_meals"] = meals
    _save_activity(activity)
    return activity


@mcp.tool()
def get_meal_suggestions(cuisine_direction: str = "", constraints: str = "") -> dict:
    """
    Run the candidate filter and auto-select a proposed weekly meal plan.

    Args:
        cuisine_direction: Cuisine preference (e.g. "Mediterranean", "Asian",
                           "Mexican", "Indian"). Empty = no preference.
        constraints: Schedule notes (e.g. "practice Monday and Wednesday, game Friday").
                     Days mentioned with activity keywords become quick-meal slots.

    Returns:
        {
          "candidates": top-20 eligible recipes (name/cuisine/health/minutes/meal_type),
          "selected_meals": {day: recipe_name} auto-proposed for the week,
          "quick_days": days flagged for quick meals,
          "week_start": "YYYY-MM-DD"
        }

    selected_meals is stored in the activity state. Use swap_meal() to adjust
    individual days, then call approve_menu() to send to Ashley.

    State after call: awaiting_meal_approval
    """
    activity = _load_activity()
    if activity.get("state") == "idle":
        return {"error": "No active workflow. Call start_menu_workflow first."}

    cuisine_note = None
    if cuisine_direction:
        cuisine_direction, cuisine_note = _normalize_cuisine_direction(cuisine_direction)
        activity["cuisine_direction"] = cuisine_direction
    if constraints:
        notes = activity.get("schedule_notes", [])
        notes.append(constraints)
        activity["schedule_notes"] = notes

    quick_days = list(activity.get("quick_days", []))
    if constraints:
        c_lower = constraints.lower()
        quick_signals = ("game", "practice", "busy", "quick", "early", "tournament")
        if any(s in c_lower for s in quick_signals):
            for day_name, abbrev in DAY_NAME_MAP.items():
                if day_name in c_lower and abbrev not in quick_days:
                    quick_days.append(abbrev)
    activity["quick_days"] = quick_days

    candidates = _load_candidates(quick_days)
    selected = _select_meals(candidates, quick_days, cuisine_direction or activity.get("cuisine_direction"))
    activity["selected_meals"] = selected
    activity["state"] = "awaiting_meal_approval"
    _save_activity(activity)

    clean_candidates = [
        {
            "name": c["name"],
            "cuisine": c["cuisine"],
            "health": c["health"],
            "minutes": c["minutes"],
            "time_str": c["time_str"],
            "meal_type": c["meal_type"],
            "is_quick": c["is_quick"],
            "last_cooked": c["last_cooked"],
            "weeknight_effort": c["weeknight_effort"],
        }
        for c in candidates[:20]
    ]

    result = {
        "candidates": clean_candidates,
        "selected_meals": selected,
        "quick_days": quick_days,
        "week_start": activity["week_start"],
    }
    if cuisine_note:
        result["cuisine_direction_note"] = cuisine_note
    return result


@mcp.tool()
def advance_to_meal_approval(
    selected_meals: dict,
    quick_days: list = [],
    schedule_notes: list = [],
    cuisine_direction: str = "",
) -> dict:
    """
    Advance the workflow to awaiting_meal_approval using meals selected locally
    by the SMS workflow (bypassing the bridge's get_meal_suggestions step).

    Called by the sms-assistant after _handle_cuisine() selects meals locally.
    Writes selected_meals and supporting fields into menu_activity.json so the
    bridge phase (approve_menu, swap_meal, etc.) can operate normally.

    Args:
        selected_meals:    Dict mapping day abbreviations to recipe names,
                           e.g. {"Sun": "Roast Chicken", "Mon": "Bulgogi", ...}
        quick_days:        Day abbreviations flagged as quick-cook nights.
        schedule_notes:    Schedule notes collected during the local phase.
        cuisine_direction: Cuisine preference the user expressed.

    Returns:
        {
          "state": "awaiting_meal_approval",
          "selected_meals": {"Sun": "...", ...},
          "week_start": "YYYY-MM-DD"
        }

    State after call: awaiting_meal_approval
    """
    activity = _load_activity()
    if activity.get("state") == "idle":
        return {"error": "No active workflow. Call start_menu_workflow first."}

    activity["selected_meals"] = selected_meals
    activity["quick_days"] = quick_days or []
    if schedule_notes:
        activity["schedule_notes"] = schedule_notes
    cuisine_note = None
    if cuisine_direction:
        cuisine_direction, cuisine_note = _normalize_cuisine_direction(cuisine_direction)
        activity["cuisine_direction"] = cuisine_direction
    activity["state"] = "awaiting_meal_approval"
    _save_activity(activity)

    result = {
        "state": "awaiting_meal_approval",
        "selected_meals": selected_meals,
        "week_start": activity.get("week_start", ""),
    }
    if cuisine_note:
        result["cuisine_direction_note"] = cuisine_note
    return result


@mcp.tool()
def swap_meal(day: str, reason: str, replacement: str = "", cuisine_direction: str = "") -> dict:
    """
    Replace the planned meal for a given day (pre-signoff).

    Args:
        day: Day abbreviation (Mon, Tue, Wed, Thu, Fri, Sat, Sun).
        reason: Natural language reason (e.g. "we've had too much chicken").
        replacement: Optional explicit recipe name. If empty, auto-picks from candidates.
        cuisine_direction: Optional cuisine preference to override activity state (e.g. "Asian", "Indian").

    Auto-pick logic: excludes already-selected recipes, prefers cuisine_direction
    match, prefers same meal_type (weekend/weeknight) as the displaced day.

    Returns:
        {
          "selected_meals": updated {day: recipe} dict,
          "swapped_day": "Thu",
          "new_recipe": "New Recipe Name",
          "outgoing_recipe": "Old Recipe Name"
        }

    State: stays awaiting_meal_approval
    """
    activity = _load_activity()
    if activity.get("state") == "idle":
        return {"error": "No active workflow. Call start_menu_workflow first."}

    selected = dict(activity.get("selected_meals", {}))
    outgoing = selected.get(day)
    filter_notes = []

    all_recipes = _load_metadata()

    if not replacement:
        # Check if reason names a specific recipe (e.g. "swap to Pasta e Ceci")
        name_m = re.search(r'\bto\s+(.{4,})', reason.lower())
        if name_m:
            rkey = _find_recipe_key(name_m.group(1).strip().rstrip(" .,"), all_recipes)
            if rkey:
                replacement = rkey

    if not replacement:
        # Exclude every recipe already in the plan (including outgoing — we want something different)
        currently_selected = set(selected.values())
        candidates = _load_candidates(activity.get("quick_days", []))
        eligible = [c for c in candidates if c["name"] not in currently_selected]

        reason_lower = reason.lower()

        # Inject idea recipes — prepend if reason signals "new" or "idea", else append
        want_idea = any(kw in reason_lower for kw in ("idea", "new", "something different", "haven't tried"))
        idea_candidates = [
            {"name": name,
             "cuisine": r.get("cuisine_type", r.get("cuisine", "Unknown")),
             "health": r.get("health_classification", r.get("health", "Moderate")),
             "protein": _get_protein(name),
             "minutes": _parse_minutes(r.get("time", "")),
             "time_str": r.get("time", ""),
             "meal_type": r.get("meal_type", "Weeknight"),
             "is_idea": True,
             "ingredients": r.get("ingredients", [])}
            for name, r in all_recipes.items()
            if r.get("status") not in ("disliked", "ignored")
            and r.get("times_cooked", 0) == 0
            and name not in currently_selected
            and not any(h in name.lower() for h in HIATUS_PROTEINS)
        ]
        eligible = (idea_candidates + eligible) if want_idea else (eligible + idea_candidates)

        if not eligible:
            return {"error": f"No eligible replacement candidates found for {day}."}

        filter_notes = []  # collects messages about unmet filters for the caller

        # 1. Protein/method filter from reason (highest priority — "grilled fish Monday")
        reason_protein = next((label for kw, label in _REASON_PROTEIN_MAP if kw in reason_lower), None)
        if reason_protein:
            protein_filtered = [c for c in eligible if _get_protein(c["name"]) == reason_protein]
            if protein_filtered:
                eligible = protein_filtered
            else:
                filter_notes.append(f"No {reason_protein.lower()} recipes available in the pool right now")

        # 2. Cuisine filter from reason (tight), else fall back to cuisine_direction
        reason_cuisine = next((c for c in KNOWN_CUISINES | KNOWN_FAMILIES if c in reason_lower), None)
        if reason_cuisine:
            cuisine_filtered = [c for c in eligible if _direction_matches_cuisine(c.get("cuisine", ""), reason_cuisine)]
            if cuisine_filtered:
                eligible = cuisine_filtered
            else:
                filter_notes.append(f"No {reason_cuisine.title()} recipes available — picked best alternative")
        else:
            cuisine_dir = cuisine_direction or activity.get("cuisine_direction", "")
            if cuisine_dir and cuisine_dir.lower() not in ("what we've got", ""):
                c_lower = cuisine_dir.lower()
                cuisine_match = [c for c in eligible if _direction_matches_cuisine(c.get("cuisine", ""), c_lower)]
                if cuisine_match:
                    eligible = cuisine_match + [c for c in eligible if c not in cuisine_match]

        # 3. Effort filter from reason ("low effort", "easy", "simple")
        effort_signals = ("low effort", "easy", "simple", "not too hard", "quick and easy")
        if any(s in reason_lower for s in effort_signals):
            effort_filtered = [c for c in eligible if c.get("weeknight_effort") in ("low", "medium")]
            if effort_filtered:
                eligible = effort_filtered
            else:
                filter_notes.append("No low/medium effort recipes available — picked best alternative")

        # 4. Category diversity — deprioritise overrepresented categories (e.g. pasta ≥ 2)
        tallies = _plan_tallies({k: v for k, v in selected.items() if k != day}, all_recipes)
        cat_counts = tallies["category"]
        overloaded_cats = {cat for cat, cnt in cat_counts.items() if cnt >= 2}
        if overloaded_cats:
            non_overloaded = [
                c for c in eligible
                if not any(
                    any(kw in c["name"].lower() for kw in _CATEGORY_KEYWORDS[cat])
                    for cat in overloaded_cats
                )
            ]
            if non_overloaded:
                eligible = non_overloaded

        # 5. Cuisine slot balance — don't exceed direction-specified quota on a swap
        swap_cuisine_slots = _parse_cuisine_slots(activity.get("cuisine_direction", ""))
        if swap_cuisine_slots:
            current_fam_counts: dict = {}
            for d, name in selected.items():
                if d == day:
                    continue
                rkey = _find_recipe_key(name, all_recipes)
                if rkey:
                    rfam = _CUISINE_FAMILY_MAP.get(
                        all_recipes[rkey].get("cuisine_type", all_recipes[rkey].get("cuisine", "")), ""
                    )
                    if rfam:
                        current_fam_counts[rfam] = current_fam_counts.get(rfam, 0) + 1
            balanced = [
                c for c in eligible
                if current_fam_counts.get(
                    _CUISINE_FAMILY_MAP.get(c.get("cuisine", ""), c.get("cuisine", "")), 0
                ) < swap_cuisine_slots.get(
                    _CUISINE_FAMILY_MAP.get(c.get("cuisine", ""), c.get("cuisine", "")), 99
                )
            ]
            if balanced:
                eligible = balanced

        # 6. Cook-time filter — weeknight days default to quick meals
        weeknight_days = {"Mon", "Tue", "Wed", "Thu", "Fri"}
        if day in weeknight_days:
            quick_eligible = [c for c in eligible if c.get("minutes", 999) <= QUICK_THRESHOLD
                              or c.get("method", "") == "slow_cooker"]
            if quick_eligible:
                eligible = quick_eligible

        # 7. Inventory boost — sort by inventory match, preserving existing order otherwise
        inventory = _load_inventory_keywords()
        if inventory:
            def _swap_score(c):
                ing = c.get("ingredients", [])
                if not ing:
                    key = _find_recipe_key(c["name"], all_recipes)
                    ing = all_recipes[key].get("ingredients", []) if key else []
                return _inventory_boost(c["name"], ing, inventory)
            eligible.sort(key=_swap_score)

        # 8. meal_type match (weekend vs weeknight) — soft filter, only if pool survives
        if outgoing:
            key = _find_recipe_key(outgoing, all_recipes)
            if key:
                target_type = all_recipes[key].get("meal_type", "Weeknight")
                same_type = [c for c in eligible if c.get("meal_type") == target_type]
                if same_type:
                    eligible = same_type

        replacement = eligible[0]["name"]

    selected[day] = replacement
    activity["selected_meals"] = selected
    _save_activity(activity)

    new_effort = ""
    new_key = _find_recipe_key(replacement, all_recipes)
    if new_key:
        new_effort = all_recipes[new_key].get("weeknight_effort", "")

    result = {
        "selected_meals": selected,
        "swapped_day": day,
        "new_recipe": replacement,
        "outgoing_recipe": outgoing,
    }
    if new_effort:
        result["new_recipe_effort"] = new_effort
    if filter_notes:
        result["note"] = " | ".join(filter_notes)
    return result


@mcp.tool()
def approve_menu() -> dict:
    """
    Send the currently selected meals to Ashley for approval via Keanu.

    Calls send_menu_partner.py with the selected_meals from the active workflow.
    Writes to Keanu's outbox (.outbox.json) and creates menu_feedback_pending.json.

    Returns:
        {
          "state": "awaiting_ashley_signoff",
          "message": "Menu sent to Ashley via Keanu.",
          "meals_sent": [{"day": "Mon 5/26", "recipe": "..."}]
        }

    State after call: awaiting_ashley_signoff
    """
    activity = _load_activity()
    if activity.get("state") == "idle":
        return {"error": "No active workflow. Call start_menu_workflow first."}

    selected = activity.get("selected_meals", {})
    if not selected:
        return {"error": "No meals selected. Call get_meal_suggestions first."}

    week_start = date.fromisoformat(activity["week_start"])
    day_to_date = _day_date_map(week_start)

    meals_json = []
    for day in DAYS_ORDER:
        if day in selected:
            dt = day_to_date.get(day)
            date_str = dt.strftime("%-m/%-d") if dt else ""
            meals_json.append({"day": f"{day} {date_str}", "recipe": selected[day]})

    cmd = [
        sys.executable, str(MENUBUILDER_DIR / "send_menu_partner.py"),
        "--meals", json.dumps(meals_json),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return {"error": f"send_menu_partner.py failed: {result.stderr.strip()}"}
    except Exception as e:
        return {"error": f"Could not run send_menu_partner.py: {e}"}

    activity["state"] = "awaiting_ashley_signoff"
    _save_activity(activity)

    return {
        "state": "awaiting_ashley_signoff",
        "message": "Menu sent to Ashley via Keanu. Waiting for her reply.",
        "meals_sent": meals_json,
    }


@mcp.tool()
def process_recipe_url(url: str, day: str = "", force_add: bool = False) -> dict:
    """
    Process a recipe URL for use in meal planning.

    First checks if a similar recipe already exists in the collection (by URL or
    fuzzy title match). If a match is found, surfaces it rather than auto-adding —
    the caller should ask the user whether to use the existing recipe or add the new one.

    Args:
        url:       Recipe URL to process.
        day:       Optional day abbreviation (Mon/Tue/Wed/Thu/Fri/Sat/Sun).
                   If provided and the recipe is new or force_add=True, the recipe
                   is swapped into that day in the current activity.
        force_add: If True, skip similarity check and add as a new recipe even if
                   a similar one already exists. Use this after the user confirms
                   they want the new variety.

    Returns when similar exists (status="similar_exists"):
        {
          status: "similar_exists",
          recipe: existing recipe name,
          match_score: 0.0–1.0 (1.0 = exact URL match),
          url: the input URL,
          message: human-readable prompt to surface to the user,
        }

    Returns when added (status="added"):
        {
          status: "added",
          recipe: new recipe name,
          health: Heart-Healthy | Moderate | Indulgent,
          time: cook time string,
          swapped_day: day (if day was provided),
          outgoing_recipe: recipe replaced (if day was provided),
          message: summary string,
        }
    """
    recipes = _load_metadata()

    # 1. Lightweight fetch for title (used in similarity check)
    fetched_data = _fetch_recipe_data(url)
    title_guess = fetched_data.get("title", "") if fetched_data else ""

    # 2. Similarity check (unless force_add)
    if not force_add:
        match = _find_similar_recipe(title_guess, url, recipes)
        if match:
            existing_name, score = match
            msg = (
                f"Similar recipe already in collection: \"{existing_name}\" "
                f"({score:.0%} match). "
                "Ask the user: use the existing recipe, or add this new variety? "
                "If new variety: call process_recipe_url again with force_add=true."
            )
            result: dict = {
                "status": "similar_exists",
                "recipe": existing_name,
                "match_score": score,
                "url": url,
                "message": msg,
            }
            if day:
                result["note"] = (
                    f"Day '{day}' requested. Confirm recipe choice first, "
                    f"then call swap_meal(day='{day}', replacement='<chosen recipe>')."
                )
            return result

    # 3. No match (or force_add) — full parse and add
    add_result = _full_recipe_add(url, fetched_data)
    if "error" in add_result:
        return add_result

    title = add_result["title"]

    # 4. Swap into plan if day provided
    swap_result: dict = {}
    if day:
        activity = _load_activity()
        selected = dict(activity.get("selected_meals", {}))
        outgoing = selected.get(day)
        selected[day] = title
        activity["selected_meals"] = selected
        _save_activity(activity)
        swap_result = {"swapped_day": day, "outgoing_recipe": outgoing}

    return {
        "status": "added",
        "recipe": title,
        "health": add_result.get("health", ""),
        "time": add_result.get("time", ""),
        "message": f"Added '{title}' to recipe collection.{' Swapped into ' + day + '.' if day else ''}",
        **swap_result,
    }


@mcp.tool()
def handle_ashley_reply(reply: str) -> dict:
    """
    Process Ashley's reply to the proposed menu.

    Args:
        reply: Ashley's text message (e.g. "looks good", "swap Tuesday to salmon",
               "can we do something lighter on Wednesday?")

    Approval:
      - Detects standard approval phrases ("looks good", "ok", "perfect", etc.)
      - All recipes are now status="active" at intake; no activation step needed.
        The awaiting_idea_activation path is kept for backward compatibility but
        will not be triggered under normal operation.
      - Generates plan files, launches apps → state: complete

    Swap request:
      - Parses structured commands ("swap 3 to X", "change Tuesday to tacos")
      - Falls back to Claude Haiku for natural language ("we've had too much chicken")
      - Re-sends updated menu to Ashley, stays in awaiting_ashley_signoff

    Unparseable:
      - Returns {parsed: false, reply: ...} for manual handling

    Returns the updated activity state plus any action-specific fields.
    """
    activity = _load_activity()
    if activity.get("state") != "awaiting_ashley_signoff":
        return {"error": f"Not in awaiting_ashley_signoff state (current: {activity.get('state')})"}

    lowered = reply.lower().strip()
    selected = activity.get("selected_meals", {})
    week_start = date.fromisoformat(activity["week_start"])

    # ── URL in reply — route to process_recipe_url ──
    url_match = re.search(r'https?://\S+', reply)
    if url_match:
        url = url_match.group(0).rstrip(".,)")
        extracted_day = _extract_day_from_text(reply)
        return {
            "parsed": False,
            "has_url": True,
            "url": url,
            "extracted_day": extracted_day,
            "message": (
                f"Reply contains a recipe URL{' for ' + extracted_day if extracted_day else ''}. "
                f"Call process_recipe_url(url='{url}'"
                + (f", day='{extracted_day}'" if extracted_day else "")
                + ") to check for similar recipes before swapping."
            ),
        }

    # ── Eating out / going out ──
    if any(s in lowered for s in _OUT_SIGNALS):
        eating_out_day = _extract_day_from_text(reply)
        if eating_out_day and eating_out_day in selected:
            new_selected = dict(selected)
            new_selected[eating_out_day] = "Going Out to Eat"
            activity["selected_meals"] = new_selected
            if any(p in lowered for p in APPROVAL_PHRASES):
                _save_activity(activity)
                return _do_finalize(activity)
            day_to_date = _day_date_map(week_start)
            meals_json = [
                {"day": f"{day} {day_to_date[day].strftime('%-m/%-d')}", "recipe": new_selected[day]}
                for day in DAYS_ORDER if day in new_selected
            ]
            cmd = [
                sys.executable, str(MENUBUILDER_DIR / "send_menu_partner.py"),
                "--meals", json.dumps(meals_json),
            ]
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            except Exception:
                pass
            _save_activity(activity)
            return {
                **activity,
                "message": f"Ashley says going out {eating_out_day}. Updated menu re-sent.",
                "meals_sent": meals_json,
            }

    # ── Approval ──
    if any(lowered == p or lowered.startswith(p + " ") for p in APPROVAL_PHRASES):
        # Check for idea recipes on the menu
        recipes = _load_metadata()
        ideas_on_menu = [
            name for name in selected.values()
            if (key := _find_recipe_key(name, recipes)) and recipes[key].get("status") == "idea"
        ]

        # Try to auto-activate each idea
        failed_ideas = []
        for name in ideas_on_menu:
            if not _try_auto_activate(name, recipes):
                key = _find_recipe_key(name, recipes)
                source_url = recipes[key].get("source_url", "") if key else ""
                failed_ideas.append({"name": name, "source_url": source_url})

        activity["ideas_on_menu"] = ideas_on_menu

        if failed_ideas:
            activity["state"] = "awaiting_idea_activation"
            activity["pending_ideas"] = failed_ideas
            _save_activity(activity)
            return {
                **activity,
                "message": (
                    f"Ashley approved! But {len(failed_ideas)} idea recipe(s) couldn't be "
                    f"auto-fetched. Activate them manually then call finalize_plan()."
                ),
                "pending_ideas": failed_ideas,
            }

        # All clear — finalize
        return _do_finalize(activity)

    # ── Swap ──
    new_selected = _parse_swap(reply, selected, week_start)
    if not new_selected:
        new_selected = _claude_swap(reply, selected, activity.get("cuisine_direction"))

    if new_selected:
        activity["selected_meals"] = new_selected
        # If the reply also signals approval, finalize immediately
        if any(p in lowered for p in APPROVAL_PHRASES):
            _save_activity(activity)
            return _do_finalize(activity)
        day_to_date = _day_date_map(week_start)
        meals_json = [
            {"day": f"{day} {day_to_date[day].strftime('%-m/%-d')}", "recipe": new_selected[day]}
            for day in DAYS_ORDER if day in new_selected
        ]
        cmd = [
            sys.executable, str(MENUBUILDER_DIR / "send_menu_partner.py"),
            "--meals", json.dumps(meals_json),
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception:
            pass
        _save_activity(activity)
        return {
            **activity,
            "message": "Ashley's swap applied. Updated menu re-sent to her.",
            "meals_sent": meals_json,
        }

    # ── Unparseable ──
    return {
        **activity,
        "parsed": False,
        "message": f"Could not parse Ashley's reply: {reply!r}. Handle manually.",
    }


@mcp.tool()
def activate_idea_recipe(name: str, content: str = "", source_url: str = "") -> dict:
    """
    Activate a pending idea recipe from provided markdown content or URL.

    Called when handle_ashley_reply returns pending_ideas that couldn't be auto-fetched.
    After activating all pending ideas, call finalize_plan().

    Args:
        name: Recipe name (fuzzy-matched against metadata).
        content: Full markdown content for the recipe file (title, ingredients,
                 instructions). Provide the complete recipe text. If empty and
                 source_url is provided, auto-fetch is attempted first.
        source_url: Source URL. If content is empty, fetch is attempted from this URL.
                    If fetch fails, returns needs_content: True so caller can ask for paste.

    Returns:
        {
          "success": bool,
          "canonical_name": str,
          "filename": str,
          "remaining_pending": int  — how many pending ideas are still unactivated
        }
        Or on auto-fetch success:
        {
          "success": True,
          "auto_activated": True,
          "canonical_name": str
        }
        Or on auto-fetch failure:
        {
          "success": False,
          "needs_content": True,
          "canonical_name": str
        }
    """
    activity = _load_activity()
    recipes = _load_metadata()
    key = _find_recipe_key(name, recipes)

    if not key:
        return {"success": False, "error": f"Recipe not found in metadata: {name!r}"}

    # If no content but a URL is provided, try auto-fetch first
    if not content and source_url:
        recipes[key]["source_url"] = source_url
        _save_metadata(recipes)
        if _try_auto_activate(name, recipes):
            return {"success": True, "auto_activated": True, "canonical_name": key}
        return {"success": False, "needs_content": True, "canonical_name": key}

    # Write .md file
    filename = re.sub(r"[^\w\s-]", "", key).strip().replace(" ", "_") + ".md"
    md_path = RECIPES_DIR / filename
    RECIPES_DIR.mkdir(exist_ok=True)

    # Prepend attribution if source_url provided and not already in content
    if source_url and "Adapted from" not in content:
        source_label = recipes[key].get("source", source_url)
        header = f"# {key}\n\n**Adapted from**: [{source_label}]({source_url})\n\n"
        if not content.startswith("#"):
            content = header + content
        else:
            content = content  # assume caller included attribution

    md_path.write_text(content, encoding="utf-8")

    # Update metadata
    recipes[key]["status"] = "active"
    recipes[key]["filename"] = filename

    # Populate prep data if not already present from the idea intake stage
    if not recipes[key].get("prep_components"):
        try:
            from prep_utils import classify_prep_single, parse_md_instructions
            ingr = recipes[key].get("ingredients", [])
            ingr_list = ([i.get("name", "") for i in ingr] if ingr
                         else recipes[key].get("ingredients_raw", []))
            instructions = parse_md_instructions(content)
            prep = classify_prep_single(key, ingr_list, instructions)
            recipes[key]["prep_components"] = prep.get("prep_components", [])
            recipes[key]["prep_notes"]      = prep.get("prep_notes", "")
        except Exception as _e:
            pass  # prep classification is best-effort; don't block activation

    _save_metadata(recipes)

    # Update pending_ideas list in activity
    pending = [p for p in activity.get("pending_ideas", []) if p.get("name") != key]
    activity["pending_ideas"] = pending
    if activity.get("state") == "awaiting_idea_activation" and not pending:
        # All pending ideas resolved; signal that finalize_plan can proceed
        activity["state"] = "awaiting_finalization"
    _save_activity(activity)

    return {
        "success": True,
        "canonical_name": key,
        "filename": filename,
        "remaining_pending": len(pending),
    }


@mcp.tool()
def finalize_plan() -> dict:
    """
    Generate plan files, launch apps, and send prep guide.

    Can be called:
    - After handle_ashley_reply returns state=awaiting_finalization (all ideas activated)
    - Directly if all selected recipes are already active (no ideas to resolve)

    Steps:
    1. Verify all selected_meals ideas are activated (fails if any still have status="idea")
    2. Generate mealplan_YYYY-MM-DD.txt (DINNERS block + REMINDERS via Claude Sonnet)
    3. Generate shopping_YYYY-MM-DD.csv (Item / Notes(qty) / Date(cook date) format)
    4. Launch WeeklyShoppingList.app and WeeklyMealCalendar.app
    5. Send plan summary to admin (David) via Keanu outbox
    6. Send prep guide to both David and Ashley via Keanu outbox

    Requires ANTHROPIC_API_KEY in environment for REMINDERS generation (falls back
    to simple day-name reminders if not available).

    Returns:
        {
          "state": "complete",
          "plan_path": "/Users/Shared/cooking/weeklyplan/mealplan_YYYY-MM-DD.txt",
          "shopping_path": "/Users/Shared/cooking/weeklyplan/shopping_YYYY-MM-DD.csv",
          "prep_guide": "PREP GUIDE: ..."  (empty string if no prep components)
        }
    """
    activity = _load_activity()
    if activity.get("state") == "idle":
        return {"error": "No active workflow. Call start_menu_workflow first."}

    selected = activity.get("selected_meals", {})
    if not selected:
        return {"error": "No meals selected. Call get_meal_suggestions first."}

    # Verify no ideas remain un-activated
    recipes = _load_metadata()
    still_ideas = [
        name for name in selected.values()
        if (key := _find_recipe_key(name, recipes)) and recipes[key].get("status") == "idea"
    ]
    if still_ideas:
        return {
            "error": "Some selected recipes are still ideas and need activation first.",
            "pending_ideas": still_ideas,
        }

    return _do_finalize(activity)


@mcp.tool()
def generate_shopping_list(meals: dict, week_start: str) -> dict:
    """
    Generate shopping_YYYY-MM-DD.csv from a finalized meal plan.

    This is the single authoritative source for shopping CSV generation.
    sms-assistant should call this instead of generating its own CSV.

    Args:
        meals:      Dict mapping day abbreviations to recipe names,
                    e.g. {"Sun": "Roast Chicken", "Mon": "Bulgogi", ...}
        week_start: ISO date string for the Monday of the plan week ("YYYY-MM-DD").
                    Used to derive per-day cook dates and the output filename.

    Ingredient fallback order:
      1. structured `ingredients` array  → Item=name, Notes=qty unit
      2. `ingredients_raw` strings       → Item=full raw string, Notes=''
      3. recipe not in metadata          → skipped

    Returns:
        {
          "shopping_path": "/abs/path/to/shopping_YYYY-MM-DD.csv",
          "row_count":     int,
          "skipped_recipes": ["name", ...]   recipes with no ingredient data
        }
    """
    try:
        ws = date.fromisoformat(week_start)
    except ValueError:
        return {"error": f"Invalid week_start date: {week_start!r}"}

    recipes = _load_metadata()
    skipped = [name for name in meals.values() if not _find_recipe_key(name, recipes)]

    csv_content = _build_shopping_csv(meals, ws)

    shopping_path = WEEKLYPLAN_DIR / f"shopping_{week_start}.csv"
    WEEKLYPLAN_DIR.mkdir(exist_ok=True)
    shopping_path.write_text(csv_content)

    row_count = max(0, csv_content.count("\n") - 1)  # subtract header

    return {
        "shopping_path": str(shopping_path),
        "row_count": row_count,
        "skipped_recipes": skipped,
    }


@mcp.tool()
def get_current_plan() -> dict:
    """
    Return the current week's meal plan.

    Finds the most recent mealplan_YYYY-MM-DD.json that covers today
    (plan filename is Monday; week starts Sunday, so Sunday is included).

    Returns:
        {
          "found": bool,
          "week_start": "YYYY-MM-DD",   # Sunday
          "week_end":   "YYYY-MM-DD",   # Saturday
          "balance": {"Heart-Healthy": N, ...},
          "meals": [
            {"day": "Sun", "date": "YYYY-MM-DD", "title": "...",
             "health": "...", "time": "...", "url": "...", "reminder": "..."},
            ...
          ],
          "formatted_text": "WEEKLY MEAL PLAN: ...\n\nDINNERS\n..."
        }

    formatted_text is suitable for injection into agent context (same layout
    the old .txt files used, URLs included). Callers that want a clean version
    can strip URLs themselves.

    "found": false is returned when no plan covers today.
    """
    if not WEEKLYPLAN_DIR.exists():
        return {"found": False}

    today = date.today()
    dated = []
    for f in WEEKLYPLAN_DIR.glob("mealplan_*.json"):
        try:
            d = date.fromisoformat(f.stem.replace("mealplan_", ""))
            dated.append((d, f))
        except ValueError:
            continue
    dated.sort(key=lambda x: x[0], reverse=True)
    plan_file = next((f for d, f in dated if d <= today + timedelta(days=1)), None)
    if not plan_file:
        return {"found": False}

    try:
        data = json.loads(plan_file.read_text())
    except Exception:
        return {"found": False}

    formatted = _plan_summary_text(data)
    reminders = [
        f"- {m['day'].upper()}: {m['reminder']}"
        for m in data.get("meals", [])
        if m.get("reminder")
    ]
    if reminders:
        formatted += "\n\nREMINDERS\n" + "\n".join(reminders)

    return {
        "found": True,
        "week_start": data.get("week_start", ""),
        "week_end":   data.get("week_end", ""),
        "balance":    data.get("balance", {}),
        "meals":      data.get("meals", []),
        "formatted_text": formatted,
    }


@mcp.tool()
def update_plan_meal(day: str, title: str) -> dict:
    """
    Update the recipe title for a single day in the current week's plan.

    Lightweight admin override — only changes the title field in the JSON.
    Does NOT update the shopping list or calendar. Use swap_meal (via the
    execute_swap pipeline) when a full mid-week swap with shopping changes
    is needed.

    Args:
        day:   Day abbreviation: Sun, Mon, Tue, Wed, Thu, Fri, Sat.
        title: New recipe title to put on that day.

    Returns:
        {"success": bool, "day": day, "title": title, "error": "..."}
    """
    if not WEEKLYPLAN_DIR.exists():
        return {"success": False, "day": day, "title": title, "error": "No weeklyplan directory found."}

    today = date.today()
    dated = []
    for f in WEEKLYPLAN_DIR.glob("mealplan_*.json"):
        try:
            d = date.fromisoformat(f.stem.replace("mealplan_", ""))
            dated.append((d, f))
        except ValueError:
            continue
    dated.sort(key=lambda x: x[0], reverse=True)
    plan_file = next((f for d, f in dated if d <= today + timedelta(days=1)), None)
    if not plan_file:
        return {"success": False, "day": day, "title": title, "error": "No current meal plan found."}

    try:
        data = json.loads(plan_file.read_text())
    except Exception as e:
        return {"success": False, "day": day, "title": title, "error": str(e)}

    recipes = _load_metadata()
    is_skip = any(kw in title.lower() for kw in _SKIP_FEEDBACK_KEYWORDS)
    if is_skip:
        new_url = ""
        new_time = ""
        new_health = ""
        new_reminder = "Going out to eat — no dinner to cook"
    else:
        key = _find_recipe_key(title, recipes)
        meta = recipes.get(key, {}) if key else {}
        new_url = _recipe_url(title, meta)
        new_time = meta.get("time", "")
        new_health = meta.get("health", "")
        new_reminder = ""

    # Strip suggest_meals display annotations ([LOW], [IN STOCK], [GARDEN: x], etc.)
    title = re.sub(r'\s*\[[^\]]+\]', '', title).strip()

    updated = False
    for meal in data.get("meals", []):
        if meal.get("day") == day:
            meal["title"] = title
            meal["url"] = new_url
            meal["time"] = new_time
            meal["health"] = new_health
            meal["reminder"] = new_reminder
            updated = True
            break

    if not updated:
        return {"success": False, "day": day, "title": title, "error": f"Day '{day}' not found in plan."}

    try:
        plan_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        os.chmod(plan_file, 0o666)
    except Exception as e:
        return {"success": False, "day": day, "title": title, "error": str(e)}

    return {"success": True, "day": day, "title": title}


@mcp.tool()
def get_recipe_url(name: str) -> dict:
    """
    Look up the GitHub Pages URL for a recipe by name.

    Uses the same fuzzy key lookup and URL construction as the rest of the
    planning tools. Prefers GitHub Pages URL; falls back to source_url.

    Args:
        name: Recipe name (exact or fuzzy match).

    Returns:
        {"name": resolved_name, "url": url_string}
        url is "" if the recipe is not found or has no URL.
    """
    recipes = _load_metadata()
    key = _find_recipe_key(name, recipes)
    if not key:
        return {"name": name, "url": ""}
    meta = recipes[key]
    url = _recipe_url(key, meta)
    return {"name": key, "url": url}


@mcp.tool()
def get_prep_guide(mode: str = "auto") -> dict:
    """
    On-demand prep guide for the current week's meal plan.

    mode:
      "auto"    — Sunday → "weekly"; any other day → "tonight"
      "weekly"  — what can be prepped right now for all remaining meals this week,
                  split into safe-to-prep-now vs. day-of-only (timing-sensitive)
      "tonight" — prep tasks for tonight's dinner only

    Food-safety classification (applied automatically):
    - day-of only if prep_notes says "day-of only" / "max X hours" / "don't marinate
      more than X hours" (X ≤ 4)
    - day-of only if recipe has citrus (lime/lemon) in ingredients AND a marination step
      (acid breaks down proteins if left too long)
    - day-of only if a prep component involves batter, dredge, or avocado/guacamole
    - everything else: safe to prep ahead

    Works at any time without an active workflow. Reflects the actual current plan
    including any mid-week swaps.

    Returns:
        {
          "prep_guide":           str,   # formatted text ready to send
          "week_start":           str,   # "YYYY-MM-DD"
          "mode":                 str,   # resolved mode ("weekly" | "tonight")
          "recipes_with_prep":    list,  # names with prep_components
          "recipes_without_prep": list,  # names without prep_components
        }
    """
    today = date.today()
    today_abbrev = _WD_TO_ABBREV[today.weekday()]  # "Sun", "Mon", …
    today_dow    = _ABBREV_TO_DOW[today_abbrev]    # Sun=0 … Sat=6

    if mode == "auto":
        mode = "weekly" if today.weekday() == 6 else "tonight"  # weekday 6 = Sunday

    # ── Locate current meal plan ──────────────────────────────────────────────
    if not WEEKLYPLAN_DIR.exists():
        return {"error": "No meal plans found.", "prep_guide": "", "mode": mode}

    dated = []
    for f in WEEKLYPLAN_DIR.glob("mealplan_*.json"):
        try:
            d = date.fromisoformat(f.stem.replace("mealplan_", ""))
            dated.append((d, f))
        except ValueError:
            continue
    dated.sort(key=lambda x: x[0], reverse=True)
    # Plan filename is the Monday date; week starts Sunday (d-1). Allow d up to tomorrow
    # so Sunday is covered by the current week's plan rather than falling back to last week.
    plan_file = next((f for d, f in dated if d <= today + timedelta(days=1)), None)
    if not plan_file:
        return {"error": "No current meal plan found.", "prep_guide": "", "mode": mode}

    week_start_str = plan_file.stem.replace("mealplan_", "")

    try:
        plan_data = json.loads(plan_file.read_text())
    except Exception:
        return {"error": "Could not read meal plan.", "prep_guide": "", "week_start": week_start_str, "mode": mode}
    selected = {m["day"]: m["title"] for m in plan_data.get("meals", []) if m.get("title")}

    if not selected:
        return {"error": "Could not parse meals from plan.", "prep_guide": "", "week_start": week_start_str, "mode": mode}

    recipes = _load_metadata()

    # ── Tonight mode ──────────────────────────────────────────────────────────
    if mode == "tonight":
        tonight_meal = selected.get(today_abbrev)
        if not tonight_meal:
            return {
                "prep_guide": f"No meal in the plan for today ({today_abbrev}).",
                "week_start": week_start_str,
                "mode": "tonight",
                "recipes_with_prep": [],
                "recipes_without_prep": [],
            }
        key = _find_recipe_key(tonight_meal, recipes)
        if not key:
            return {
                "prep_guide": f"No prep data found for {tonight_meal}.",
                "week_start": week_start_str,
                "mode": "tonight",
                "recipes_with_prep": [],
                "recipes_without_prep": [tonight_meal],
            }
        prep  = recipes[key].get("prep_components", [])
        notes = recipes[key].get("prep_notes", "")
        if not prep:
            guide = f"No advance prep needed for {tonight_meal} — cook as-is."
            return {
                "prep_guide": guide,
                "week_start": week_start_str,
                "mode": "tonight",
                "recipes_with_prep": [],
                "recipes_without_prep": [tonight_meal],
            }
        lines = [f"Prep for tonight — {tonight_meal}:"]
        for p in prep:
            lines.append(f"- {p}")
        if notes:
            lines.append(f"\nNote: {notes}")
        return {
            "prep_guide": "\n".join(lines),
            "week_start": week_start_str,
            "mode": "tonight",
            "meal": tonight_meal,
            "recipes_with_prep": [tonight_meal],
            "recipes_without_prep": [],
        }

    # ── Weekly mode ───────────────────────────────────────────────────────────
    # Only consider meals from today onward (past days are already cooked).
    prep_now  = []   # safe to prep today
    prep_dayof = []  # timing-sensitive — must be done day-of
    no_prep   = []   # no prep_components in metadata

    for day in DAYS_ORDER:
        if day not in selected:
            continue
        meal_dow   = _ABBREV_TO_DOW[day]
        days_until = meal_dow - today_dow   # negative = already past
        if days_until < 0:
            continue  # skip meals that have already passed

        name = selected[day]
        key  = _find_recipe_key(name, recipes)
        if not key:
            no_prep.append(name)
            continue

        prep  = recipes[key].get("prep_components", [])
        notes = recipes[key].get("prep_notes", "")

        if not prep:
            no_prep.append(name)
            continue

        entry = {"meal": name, "day": day, "prep": prep, "notes": notes}
        if _recipe_is_dayof(prep, notes, recipes[key]):
            prep_dayof.append(entry)
        else:
            prep_now.append(entry)

    # ── Format output ─────────────────────────────────────────────────────────
    header = "Sunday prep:" if today_abbrev == "Sun" else "What you can prep now:"
    sections: list[str] = []

    if prep_now:
        sections.append(header)
        for e in prep_now:
            sections.append(f"\n{e['meal']} ({e['day']}):")
            for p in e["prep"]:
                sections.append(f"  - {p}")
            if e["notes"]:
                sections.append(f"  Note: {e['notes']}")

    if prep_dayof:
        sections.append("\nDay-of only (timing-sensitive):")
        for e in prep_dayof:
            sections.append(f"\n{e['meal']} ({e['day']}):")
            for p in e["prep"]:
                sections.append(f"  - {p}")
            if e["notes"]:
                sections.append(f"  Note: {e['notes']}")

    if not prep_now and not prep_dayof:
        guide = "No advance prep needed for the remaining meals this week."
    else:
        guide = "\n".join(sections)

    return {
        "prep_guide":           guide,
        "week_start":           week_start_str,
        "mode":                 "weekly",
        "recipes_with_prep":    [e["meal"] for e in prep_now + prep_dayof],
        "recipes_without_prep": no_prep,
    }


@mcp.tool()
def sync_atk_recipes(target: int = 5, dry_run: bool = False, collection: str = "") -> dict:
    """
    Sync ATK favorite collections into recipe_metadata.json.

    Pulls from your saved ATK collections in order:
      - "Try Out", "Sunday Dinner", "Dinners" (configured in config.json)
    Falls back to your top-rated ATK recipes if collections don't yield enough new entries.
    Skips any recipe already in recipe_metadata.json (dedup by URL and title).

    Each imported recipe gets:
      - A .md file in ~/Dropbox/LLMContext/cooking/recipes/
      - A full metadata entry (health, cuisine, ingredients, instructions, cook_time)
      - needs_review: true if content looks thin (< 2 steps, < 3 ingredients)

    After importing, run generate_github_pages_data.py and push to update the recipe site.

    Args:
        target:     Max number of new recipes to import (default 5).
        dry_run:    If True, preview what would be imported without writing anything.
        collection: Restrict to one collection name (e.g. "Try Out"). Empty = all collections.

    Returns:
        {
          "added":   [str],   # recipe titles successfully added
          "count":   int,     # number added
          "dry_run": bool,
        }
    """
    import atk_agent
    added = atk_agent.sync_atk(
        target=target,
        dry_run=dry_run,
        collection_filter=collection or None,
    )
    return {
        "added":   added,
        "count":   len(added),
        "dry_run": dry_run,
    }


@mcp.tool()
def get_lunch_pick() -> dict:
    """
    Return Ashley's current weekly lunch pick (name and URL).

    Returns:
        {"name": recipe_name, "url": github_pages_url, "status": "selected" | "none"}
        status is "none" if no pick has been made this week.
    """
    if not LUNCH_STATE_FILE.exists():
        return {"name": "", "url": "", "status": "none"}
    try:
        state = json.loads(LUNCH_STATE_FILE.read_text())
    except Exception:
        return {"name": "", "url": "", "status": "none"}

    if state.get("status") != "selected" or not state.get("current_pick"):
        return {"name": "", "url": "", "status": "none"}

    name = state["current_pick"]
    url = state.get("url", "")

    # Fallback: look up URL from metadata if not stored in state
    if not url:
        recipes = _load_metadata()
        key = _find_recipe_key(name, recipes)
        if key:
            url = _recipe_url(key, recipes[key])

    return {"name": name, "url": url, "status": "selected"}


@mcp.tool()
def get_lunch_suggestions(exclude: str = "") -> dict:
    """
    Return 3 lunch suggestions for Ashley.

    Filters recipes tagged lunch_suitable or meal_type=="lunch", avoids anything
    eaten in the last 4 weeks. URLs are GitHub Pages clickable links.

    Args:
        exclude: Recipe name to skip (e.g. last week's pick).

    Returns:
        {"suggestions": [{"name": str, "url": str, "health": str}]}
    """
    import subprocess as _sp
    script = Path(__file__).parent.parent / "suggest_lunch.py"
    cmd = [sys.executable, str(script), "--json"]
    if exclude:
        cmd += ["--exclude", exclude]
    result = _sp.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "suggestions": []}
    suggestions = json.loads(result.stdout)
    return {"suggestions": suggestions}


@mcp.tool()
def set_lunch_pick(recipe_name: str) -> dict:
    """
    Record Ashley's lunch pick for the week.

    Writes to /Users/Shared/cooking/lunch_state.json. The shopping list
    generator reads this file and appends lunch ingredients dated Saturday
    (prep day before the Sunday cook).

    Args:
        recipe_name: Exact recipe name (or close match) from recipe_metadata.json.

    Returns:
        {"ok": bool, "recipe": str, "message": str}
    """
    data = json.loads(METADATA_PATH.read_text())
    recipes = data["recipes"]
    key = _find_recipe_key(recipe_name, recipes)
    if not key:
        return {"ok": False, "recipe": recipe_name, "message": f"Recipe not found: {recipe_name}"}

    base_url = _CONFIG.get("github_pages_base_url", "https://davidmallison.github.io/menubuilder-recipes")
    filename = recipes[key].get("filename", "")
    url = f"{base_url.rstrip('/')}/{filename.replace('.md', '')}" if filename else ""

    state: dict = {}
    if LUNCH_STATE_FILE.exists():
        try:
            state = json.loads(LUNCH_STATE_FILE.read_text())
        except Exception:
            state = {}

    first_week = not state.get("last_pick")
    state.update({
        "current_pick": key,
        "status":       "selected",
        "first_week":   first_week,
        "set_date":     date.today().isoformat(),
        "url":          url,
    })
    LUNCH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LUNCH_STATE_FILE.write_text(json.dumps(state, indent=2))

    return {"ok": True, "recipe": key, "message": f"Lunch pick set: {key}. Ingredients will be added to Saturday's shopping list."}


@mcp.tool()
def log_lunch_feedback(recipe: str, sentiment: str, note: str = "") -> dict:
    """
    Log Ashley's feedback on last week's lunch and update tracking fields.

    Updates times_eaten_lunch and last_lunch_date in recipe_metadata.json.
    Clears current_pick from lunch_state.json after logging.

    Args:
        recipe:    Recipe name (from lunch_state or direct).
        sentiment: "liked" | "disliked" | "ok"
        note:      Optional freeform note.

    Returns:
        {"ok": bool, "recipe": str, "times_eaten_lunch": int}
    """
    data = json.loads(METADATA_PATH.read_text())
    recipes = data["recipes"]
    key = _find_recipe_key(recipe, recipes)
    if not key:
        return {"ok": False, "recipe": recipe, "times_eaten_lunch": 0}

    r = recipes[key]
    r["times_eaten_lunch"] = r.get("times_eaten_lunch", 0) + 1
    r["last_lunch_date"]   = date.today().isoformat()
    if note:
        r.setdefault("notes", "")
        r["notes"] = (r["notes"] + " " + note).strip()

    if sentiment == "disliked":
        r["lunch_suitable"] = False

    meta_path.write_text(json.dumps(data, indent=2))

    # Clear pick from state
    if LUNCH_STATE_FILE.exists():
        try:
            state = json.loads(LUNCH_STATE_FILE.read_text())
            state["status"]       = "logged"
            state["last_pick"]    = key
            state["current_pick"] = None
            LUNCH_STATE_FILE.write_text(json.dumps(state, indent=2))
        except Exception:
            pass

    return {"ok": True, "recipe": key, "times_eaten_lunch": r["times_eaten_lunch"]}


@mcp.tool()
def add_lunch_recipe_url(url: str) -> dict:
    """
    Fetch a recipe URL Ashley texted, create a .md file + metadata entry tagged for lunch.

    Fetches the URL, extracts title/ingredients/instructions, writes the .md file in
    ~/Dropbox/LLMContext/cooking/recipes/, and adds a metadata entry with:
      - meal_type: "lunch"
      - lunch_suitable: true
      - status: "active"
      - needs_review: true (so it surfaces in the next review cycle)

    Args:
        url: Full URL to the recipe page.

    Returns:
        {"ok": bool, "recipe": str, "url": str, "message": str}
    """
    from bs4 import BeautifulSoup as _BS

    try:
        resp = httpx.get(url, follow_redirects=True, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as e:
        return {"ok": False, "recipe": "", "url": url, "message": f"Fetch failed: {e}"}

    soup = _BS(resp.text, "html.parser")
    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else "Ashley's Lunch Recipe"

    body_text    = soup.get_text(separator="\n", strip=True)
    instructions = body_text[:4000]

    safe_name = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_")
    filename  = safe_name + ".md"
    md_path   = RECIPES_DIR / filename

    md_content = f"# {title}\n\n**Adapted from**: [{url}]({url})\n\n## Instructions\n\n{instructions}\n"
    md_path.write_text(md_content)

    data    = json.loads(METADATA_PATH.read_text())
    recipes = data["recipes"]

    if title not in recipes:
        recipes[title] = {
            "filename":          filename,
            "source":            url,
            "cuisine":           "American",
            "meal_type":         "lunch",
            "health":            "Heart-Healthy",
            "status":            "active",
            "lunch_suitable":    True,
            "times_cooked":      0,
            "times_eaten_lunch": 0,
            "last_cooked_date":  None,
            "last_lunch_date":   None,
            "ingredients":       [],
            "ingredients_raw":   [],
            "instructions":      instructions[:2000],
            "needs_review":      True,
            "url":               url,
        }
        meta_path.write_text(json.dumps(data, indent=2))

    base_url = _CONFIG.get("github_pages_base_url", "https://davidmallison.github.io/menubuilder-recipes")
    gh_url   = f"{base_url.rstrip('/')}/{safe_name}"

    return {
        "ok":      True,
        "recipe":  title,
        "url":     gh_url,
        "message": f"Added '{title}' as a lunch recipe. Marked needs_review=true. Run generate_github_pages_data.py + push to publish.",
    }


@mcp.tool()
def process_recipe_image(image_b64: str, mime_type: str = "image/jpeg",
                         source_note: str = "", force_add: bool = False) -> dict:
    """
    Extract a recipe from a photo (e.g. a cookbook page) and add it to the collection.

    Uses Claude vision to extract title, ingredients, and instructions from the image,
    then runs the same intake pipeline as process_recipe_url: similarity check,
    health classification, metadata write, and .md file creation.

    Args:
        image_b64:   Base64-encoded image bytes.
        mime_type:   MIME type (default: image/jpeg).
        source_note: Caption or context from the user, e.g. "Julia Child book, chicken fricassee".
                     Used for source attribution in the recipe .md file.
        force_add:   If True, skip similarity check and add even if a similar recipe exists.
                     Use this after the user confirms they want the new variety added.
                     Re-pass the same image_b64 from the original call.

    Returns when similar exists (status="similar_exists"):
        { status, recipe, match_score, message }

    Returns when added (status="added"):
        { status, recipe, health, time, needs_review, message }

    Returns on failure (status="error"):
        { status, error }
    """
    import anthropic as _anthropic

    client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    # 1. Vision extraction — Sonnet for complex OCR (cookbook layouts, small text)
    try:
        extraction = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Extract the complete recipe from this image. "
                            "Return valid JSON only with these keys:\n"
                            "  title: recipe name (string)\n"
                            "  ingredients: list of ingredient strings exactly as written "
                            "(e.g. '3 lbs chicken thighs', '1/2 cup dry white wine')\n"
                            "  instructions: list of instruction step strings\n"
                            "  time: total cook/prep time if shown (string, e.g. '1 hour 30 minutes')\n"
                            "  servings: serving size if shown (string)\n"
                            "  cuisine: cuisine type (string)\n"
                            "Return only the JSON object, no commentary."
                        ),
                    },
                ],
            }],
        )
        raw = extraction.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw.strip())
        fetched_data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"Vision extraction returned non-JSON: {raw[:300]!r}"}
    except Exception as e:
        return {"status": "error", "error": f"Vision extraction API error: {e}"}

    title = (fetched_data.get("title") or "").strip()
    if not title:
        return {"status": "error", "error": "Could not determine recipe title from image"}

    ings_raw = fetched_data.get("ingredients", [])
    if not ings_raw:
        return {"status": "error", "error": "Could not extract ingredients from image"}

    # 2. Similarity check (title only — no URL for book recipes)
    recipes = _load_metadata()
    if not force_add:
        match = _find_similar_recipe(title, "", recipes)
        if match:
            existing_name, score = match
            return {
                "status": "similar_exists",
                "recipe": existing_name,
                "match_score": score,
                "message": (
                    f"Similar recipe already in collection: \"{existing_name}\" "
                    f"({score:.0%} match). Use the existing one, or confirm to add this new variety? "
                    "If adding new: call process_recipe_image again with force_add=true and the same image_b64."
                ),
            }

    # 3. Extract source credit from caption — look for "from <X>" pattern
    _source_credit = ""
    if source_note:
        _m = re.search(r'\bfrom\s+(.+?)(?:\s*[,:]|$)', source_note.strip(), re.IGNORECASE)
        if _m:
            _source_credit = _m.group(1).strip()

    # 4. Classify, write .md, and save metadata
    result = _classify_and_write(
        title, fetched_data,
        source_url="", source_name="",
        source_credit=_source_credit,
        needs_review=True,
        recipes=recipes,
    )
    return {
        "status": "added",
        "recipe": result["title"],
        "health": result["health"],
        "time": result["time"],
        "needs_review": True,
        "message": (
            f"Added '{result['title']}' to the collection ({result['health']}). "
            "Run generate_github_pages_data.py + push to publish to GitHub Pages."
        ),
    }


@mcp.tool()
def set_recipe_image(recipe_name: str, image_b64: str, mime_type: str = "image/jpeg") -> dict:
    """
    Store a user-submitted photo as the image for an existing recipe.

    Used when a user texts a photo of a cooked meal to Keanu with a caption like
    "photo of [meal name]". Fuzzy-matches recipe_name against the collection,
    saves the image to disk, and updates the 'image' field in recipe_metadata.json.

    The saved path is understood by the recipe review server's /api/img endpoint.

    Returns:
        status="updated":   { status, recipe, image_path, message }
        status="ambiguous": { status, candidates, message }
        status="not_found": { status, message }
        status="error":     { status, error }
    """
    import base64 as _b64

    recipes = _load_metadata()

    # 1. Fuzzy-match recipe name
    key = _find_recipe_key(recipe_name, recipes)
    if not key:
        candidates_lower = difflib.get_close_matches(
            recipe_name.lower(),
            [k.lower() for k in recipes],
            n=3, cutoff=0.5,
        )
        if candidates_lower:
            lower_to_key = {k.lower(): k for k in recipes}
            matched = [lower_to_key[c] for c in candidates_lower if c in lower_to_key]
            return {
                "status": "ambiguous",
                "candidates": matched,
                "message": (
                    f"No exact match for '{recipe_name}'. Did you mean: "
                    + ", ".join(f'"{m}"' for m in matched) + "?"
                ),
            }
        return {
            "status": "not_found",
            "message": f"No recipe found matching '{recipe_name}'.",
        }

    # 2. Decode and save image
    RECIPE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", key)
    ext = "jpg" if any(x in mime_type for x in ("jpeg", "jpg")) else mime_type.split("/")[-1]
    image_path = RECIPE_IMAGES_DIR / f"{slug}.{ext}"

    try:
        image_bytes = _b64.standard_b64decode(image_b64)
        image_path.write_bytes(image_bytes)
    except Exception as e:
        return {"status": "error", "error": f"Failed to save image: {e}"}

    # 3. Update metadata
    recipes[key]["image"] = str(image_path)
    _save_metadata(recipes)

    return {
        "status": "updated",
        "recipe": key,
        "image_path": str(image_path),
        "message": f"Photo saved as image for '{key}'.",
    }


if __name__ == "__main__":
    mcp.run()
