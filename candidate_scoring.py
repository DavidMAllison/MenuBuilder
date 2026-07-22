#!/usr/bin/env python3
"""
candidate_scoring.py -- Shared meal-candidate filtering, scoring, and
inventory-matching logic.

Used by both suggest_meals.py (manual weekly planning) and
mcp/menu_server.py (automated SMS/MCP workflow) so candidate ranking,
inventory matching, and cuisine resolution can't drift between the two
paths (Phase 4.3, architecture_review_and_fix_plan.md).
"""

import json
import re
from datetime import date, datetime

RECENCY_WEEKS = 3          # avoid meals cooked within this many weeks
QUICK_THRESHOLD = 35       # minutes -- "quick" meals for practice nights
SPRING_SUMMER = (4, 9)     # months April-September: prioritize grill
TRACKED_FRESH_HERBS = ['cilantro', 'mint', 'dill', 'parsley', 'tarragon', 'chives']

# Proteins currently excluded from candidates entirely. Empty for now --
# salmon was removed 2026-07-22 (back in rotation per CLAUDE.md, max 1/week
# enforced at proposal review, not here).
HIATUS_PROTEINS: list = []

# Proteins to group meals by (keyword -> label), checked in order.
PROTEIN_KEYWORDS = [
    ('salmon', 'Salmon'),
    ('fish', 'Fish'),
    ('shrimp', 'Shrimp'),
    ('cod', 'Fish'),
    ('tilapia', 'Fish'),
    ('pork', 'Pork'),
    ('lamb', 'Lamb'),
    ('beef', 'Beef'),
    ('chicken', 'Chicken'),
    ('turkey', 'Turkey'),
    ('tofu', 'Vegetarian'),
    ('chickpea', 'Vegetarian'),
    ('mushroom', 'Vegetarian'),
    ('lentil', 'Vegetarian'),
    ('bean', 'Vegetarian'),
    ('vegetarian', 'Vegetarian'),
    ('pasta', 'Pasta/Veg'),
    ('spaghetti', 'Pasta/Veg'),
    ('noodle', 'Pasta/Veg'),
]

# Words to strip when extracting food keywords from inventory item names.
INVENTORY_STOPWORDS = {
    "oz", "lb", "lbs", "pkg", "bag", "box", "can", "jar", "bottle", "fresh", "frozen", "dried",
    "costco", "package", "packages", "individual", "pieces", "piece", "thawed", "homemade",
    "batch", "bags", "kg", "g", "and", "the", "a", "an", "in", "bone", "boneless", "skinless",
    "country", "style",
    # brand names -- strip so they don't pollute keyword matching
    "kroger", "private", "selection", "prego", "cecco", "martins", "rosarita",
    "driscolls", "fage", "kind", "thomas", "stacys", "lgm", "saint", "humboldt",
    "pirate", "angel", "food", "chiquita", "stouffers", "mila",
}

PANTRY_CATEGORIES = {"Pantry", "Dry Goods", "Dairy"}


def resolve_cuisine(r: dict, default: str = 'Unknown') -> str:
    """Return a recipe's cuisine, preferring the live 'cuisine' key over the
    mostly-vestigial 'cuisine_type' key. Some entries have 'cuisine_type'
    present but set to null, which breaks a naive `.get(a, .get(b))` fallback
    since dict.get only falls back on a *missing* key, not a None value."""
    return r.get('cuisine') or r.get('cuisine_type') or default


def resolve_health(r: dict, default: str = 'Moderate') -> str:
    """Return a recipe's health classification, preferring the live 'health'
    key over the vestigial 'health_classification' key (same null-fallback
    hazard as resolve_cuisine -- see there)."""
    return r.get('health') or r.get('health_classification') or default


def parse_minutes(time_str: str) -> int:
    if not time_str:
        return 999
    mins = 0
    h = re.search(r'(\d+)\s*hour', time_str, re.IGNORECASE)
    if h:
        mins += int(h.group(1)) * 60
    m = re.search(r'(\d+)\s*min', time_str, re.IGNORECASE)
    if m:
        mins += int(m.group(1))
    return mins if mins > 0 else 999


def protein_label(name: str) -> str:
    lower = name.lower()
    for keyword, label in PROTEIN_KEYWORDS:
        if keyword in lower:
            return label
    return 'Other'


def is_hiatus(name: str) -> bool:
    lower = name.lower()
    return any(h in lower for h in HIATUS_PROTEINS)


def weeks_since(date_str) -> float:
    if not date_str:
        return 999
    try:
        cooked = datetime.strptime(date_str, '%Y-%m-%d').date()
        return (date.today() - cooked).days / 7
    except (ValueError, TypeError):
        return 999


def compute_adult_score(feedback: list, adult_names: set):
    """Fraction of adult feedback entries that are 'liked', or None if no adult feedback."""
    adult = [f for f in feedback if f.get('person', '').lower() in adult_names]
    if not adult:
        return None
    liked = sum(1 for f in adult if f.get('sentiment') == 'liked')
    return liked / len(adult)


def compute_kid_friendly(feedback: list, adult_names: set):
    """True if majority of kid feedback entries are 'liked', False if majority
    disliked, or None if no kid feedback."""
    kids = [f for f in feedback if f.get('person', '').lower() not in adult_names]
    if not kids:
        return None
    liked = sum(1 for f in kids if f.get('sentiment') == 'liked')
    return liked > len(kids) / 2


def herbs_in_recipe(r: dict, herb_list: list) -> list:
    """Return list of herbs from herb_list found in recipe's ingredients."""
    found = []
    structured = r.get('ingredients', [])
    if structured:
        for ing in structured:
            ing_name = str(ing.get('name', '')).lower()
            for herb in herb_list:
                if herb in ing_name and herb not in found:
                    found.append(herb)
    else:
        for raw in r.get('ingredients_raw', []):
            raw_lower = str(raw).lower()
            for herb in herb_list:
                if herb in raw_lower and herb not in found:
                    found.append(herb)
    return found


def load_inventory_keywords(inventory_path: str) -> list:
    """Return list of {name, keywords, category} from inventory.json."""
    if not inventory_path:
        return []
    try:
        with open(inventory_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []

    items = []
    for item in data.get('items', []):
        name = item.get('name', '').lower()
        qty = item.get('quantity', 0)
        if not name or qty == 0:
            continue
        words = [w for w in name.split() if w not in INVENTORY_STOPWORDS and len(w) > 2]
        items.append({
            'name': name,
            'keywords': words,
            'category': item.get('category', ''),
            'quantity': qty,
            'unit': item.get('unit', ''),
        })
    return items


def inventory_match(recipe_name: str, ingredients: list, inventory_items: list) -> tuple:
    """
    Check if a recipe matches any inventory items.
    Returns (broad_match: bool, protein_specific: list[str], pantry_specific: list[str])
      - broad_match: recipe protein category matches a stocked protein type
      - protein_specific: protein inventory items that specifically match (e.g. 'pork tenderloin')
      - pantry_specific: pantry/dry goods/dairy items that match (e.g. 'rigatoni', 'heavy cream')
    """
    if not inventory_items:
        return False, [], []

    name_lower = recipe_name.lower()
    ing_text = ' '.join(
        i.get('name', '') if isinstance(i, dict) else str(i)
        for i in ingredients
    ).lower()
    searchable = f"{name_lower} {ing_text}"

    protein_specific = []
    pantry_specific = []
    broad = False

    for item in inventory_items:
        keywords = item['keywords']
        if not keywords:
            continue

        if item['category'] == 'Proteins':
            if all(kw in searchable for kw in keywords):
                protein_specific.append(item['name'])
            elif any(kw in searchable for kw in keywords):
                broad = True
        elif item['category'] in PANTRY_CATEGORIES:
            if len(keywords) >= 2 and all(kw in searchable for kw in keywords):
                pantry_specific.append(item['name'])
            elif len(keywords) == 1 and len(keywords[0]) >= 5 and keywords[0] in searchable:
                pantry_specific.append(item['name'])

    return broad, protein_specific, pantry_specific


def candidate_score(c: dict) -> float:
    """Lower score = better candidate."""
    s = 0.0
    # Prefer not-recently-cooked (older = better)
    s -= min(c.get('age_weeks', 0), 52) * 2
    # Prefer heart-healthy
    health = c.get('health', 'Moderate')
    if health == 'Heart-Healthy':
        s -= 15
    elif health == 'Indulgent':
        s += 10
    # Prefer less-cooked (new/underused recipes)
    s += c.get('times_cooked', 0) * 2
    # Prefer grill in season
    if c.get('is_grill') and c.get('is_grill_season'):
        s -= 4
    # Family feedback signals
    adult_score = c.get('adult_score')
    if adult_score is not None:
        if adult_score < 0.5:
            s += 15   # adults didn't like it -- deprioritize
        elif adult_score >= 0.9:
            s -= 6    # family hit -- bring back sooner
    if c.get('kid_approved'):
        s -= 3
    # Inventory: lean toward recipes using what's on hand.
    # Capped lower than health bonus so freezer contents don't override health goals.
    if c.get('inv_specific'):
        s -= 7    # specific ingredient match (e.g. pork tenderloin in stock)
    elif c.get('inv_broad'):
        s -= 3    # broad protein match (e.g. any chicken recipe when chicken is stocked)
    if c.get('inv_pantry'):
        s -= 2    # pantry/dry goods match (e.g. rigatoni or heavy cream in stock)
    # Garden herb bonus (free fresh herb from garden)
    if c.get('garden_herbs'):
        s -= 4
    return s


def load_candidates(recipes: dict, *, adult_names: set, garden_herbs: list,
                     inventory_items: list) -> tuple:
    """Build the filtered candidate list from recipe_metadata.json's 'recipes' dict.

    Returns (candidates, is_grill_season). Candidates are NOT jitter-shuffled,
    scored, or sorted -- callers apply candidate_score() and their own
    tie-breaking (both current callers add small random jitter to the base
    score so near-equal candidates rotate week to week)."""
    today = date.today()
    is_grill_season = SPRING_SUMMER[0] <= today.month <= SPRING_SUMMER[1]

    candidates = []
    for name, r in recipes.items():
        if r.get('status') != 'active':
            continue
        if r.get('recommend_hold'):
            continue
        if is_hiatus(name):
            continue

        age_weeks = weeks_since(r.get('last_cooked_date'))
        if age_weeks < RECENCY_WEEKS:
            continue

        minutes = parse_minutes(r.get('time', ''))
        is_slow = r.get('cooking_method') == 'slow_cooker'
        is_quick = minutes <= QUICK_THRESHOLD or is_slow
        is_grill = r.get('cooking_method') == 'grill'
        protein = protein_label(name)
        feedback = r.get('feedback', [])

        adult_score = compute_adult_score(feedback, adult_names)
        kid_friendly = compute_kid_friendly(feedback, adult_names)
        kid_approved = bool(r.get('kid_approved')) or bool(kid_friendly)

        ingredients = r.get('ingredients', [])
        inv_broad, inv_specific, inv_pantry = inventory_match(name, ingredients, inventory_items)
        garden = herbs_in_recipe(r, garden_herbs)

        candidates.append({
            'name': name,
            'cuisine': resolve_cuisine(r),
            'health': resolve_health(r),
            'protein': protein,
            'minutes': minutes,
            'time_str': r.get('time', ''),
            'is_quick': is_quick,
            'is_grill': is_grill,
            'is_grill_season': is_grill_season,
            'times_cooked': r.get('times_cooked', 0),
            'last_cooked': r.get('last_cooked_date') or 'never',
            'age_weeks': age_weeks,
            'meal_type': r.get('meal_type', 'Weeknight'),
            'method': r.get('cooking_method', ''),
            'weeknight_effort': r.get('weeknight_effort', ''),
            'adult_score': adult_score,
            'kid_approved': kid_approved,
            'inv_broad': inv_broad,
            'inv_specific': inv_specific,
            'inv_pantry': inv_pantry,
            'garden_herbs': garden,
        })

    return candidates, is_grill_season
