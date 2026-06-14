#!/usr/bin/env python3
"""
suggest_meals.py -- Candidate meal filter for weekly menu planning.

Usage:
  python3 suggest_meals.py
  python3 suggest_meals.py --quick mon,tue,thu   # flag nights with early practice
  python3 suggest_meals.py --week 2026-04-13     # override week start date

Output: ranked candidate list per slot category, filtered by recency, health, variety.
"""

import json
import argparse
import os
import re
from datetime import date, datetime, timedelta
from collections import defaultdict

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
with open(_CONFIG_PATH) as _f:
    _CONFIG = json.load(_f)

METADATA_PATH = os.path.expanduser(_CONFIG['metadata_path'])
INVENTORY_PATH = os.path.expanduser(_CONFIG.get('inventory_path', ''))
ADULT_NAMES = set(name.lower() for name in _CONFIG['adult_names'])
RECENCY_WEEKS = 3          # avoid meals cooked within this many weeks
QUICK_THRESHOLD = 35       # minutes -- "quick" meals for practice nights
SPRING_SUMMER = (4, 9)     # months April-September: prioritize grill

_CONDIMENT_TERMINAL = {
    "sauce", "salsa", "dressing", "marinade", "rub", "glaze", "vinaigrette",
    "relish", "chutney", "gravy", "dip", "spread", "jam", "compote", "paste",
    "aioli", "mayo", "mayonnaise", "pesto", "tapenade", "hummus", "tzatziki",
    "brine", "pickle", "pickles", "oil",
}
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
    """Return True if title describes a standalone condiment rather than a dinner recipe."""
    words = re.sub(r"[^\w\s]", "", title.lower()).split()
    if not words or words[-1] not in _CONDIMENT_TERMINAL:
        return False
    return not any(anchor in title.lower() for anchor in _DISH_ANCHORS)


# Proteins to group meals by (keyword -> label)
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

HIATUS = ['salmon']  # proteins currently on hiatus

# Cuisine → family grouping for cap enforcement at step 4.
# Asian = Japanese + Korean + Chinese + Thai + Vietnamese (max 2/week as one family).
# Indian is its own family (not folded into Asian).
# All other cuisines are their own family.
CUISINE_FAMILY_MAP = {
    'Japanese':    'Asian',
    'Korean':      'Asian',
    'Chinese':     'Asian',
    'Thai':        'Asian',
    'Vietnamese':  'Asian',
    'Indian':      'Indian',
    'Mexican':     'Mexican',
    'Italian':     'Italian',
    'American':    'American',
    'Mediterranean': 'Mediterranean',
    'Greek':       'Mediterranean',
    'Moroccan':    'Mediterranean',
    'Middle Eastern': 'Middle Eastern',
    'French':      'French',
    'Spanish':     'European',
    'German':      'European',
    'Caribbean':   'Caribbean',
    'Peruvian':    'Peruvian',
}


def cuisine_family(cuisine: str) -> str:
    """Return the cuisine family string for display (e.g. 'Japanese' → '[Asian]')."""
    family = CUISINE_FAMILY_MAP.get(cuisine)
    if family and family != cuisine:
        return f' [{family}]'
    return ''

# Words to strip when extracting food keywords from inventory item names
_INVENTORY_STOPWORDS = {
    'costco', 'package', 'packages', 'individual', 'pieces', 'piece',
    'frozen', 'fresh', 'thawed', 'homemade', 'batch', 'bags', 'bag',
    'lbs', 'lb', 'oz', 'kg', 'g', 'and', 'the', 'a', 'an', 'in',
    'bone', 'boneless', 'skinless', 'country', 'style',
    # brand names -- strip so they don't pollute keyword matching
    'kroger', 'private', 'selection', 'prego', 'cecco',
    'martins', 'rosarita', 'driscolls', 'fage', 'kind',
    'thomas', 'stacys', 'lgm', 'saint', 'humboldt', 'pirate',
    'angel', 'food', 'chiquita', 'stouffers', 'mila',
}

PANTRY_CATEGORIES = {'Pantry', 'Dry Goods', 'Dairy'}


def load_inventory():
    """Load inventory.json and return a list of (item_name, keywords, category) tuples."""
    if not INVENTORY_PATH:
        return []
    try:
        with open(INVENTORY_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    items = []
    for item in data.get('items', []):
        name = item.get('name', '').lower()
        qty = item.get('quantity', 0)
        if not name or qty == 0:
            continue
        # Extract meaningful food keywords from the item name
        words = [w for w in name.split() if w not in _INVENTORY_STOPWORDS and len(w) > 2]
        items.append({
            'name': name,
            'keywords': words,
            'category': item.get('category', ''),
            'quantity': qty,
            'unit': item.get('unit', ''),
        })
    return items


def inventory_match(recipe_name, ingredients, inventory_items):
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
            # Specific match: all meaningful keywords present in recipe name or ingredients
            if all(kw in searchable for kw in keywords):
                protein_specific.append(item['name'])
            # Broad protein match: any single protein keyword matches
            elif any(kw in searchable for kw in keywords):
                broad = True

        elif item['category'] in PANTRY_CATEGORIES:
            # Require 2+ keywords for a match (avoids noisy single-word hits like 'butter', 'eggs')
            # Exception: single keyword allowed if specific enough (5+ chars, e.g. 'rigatoni')
            if len(keywords) >= 2 and all(kw in searchable for kw in keywords):
                pantry_specific.append(item['name'])
            elif len(keywords) == 1 and len(keywords[0]) >= 5 and keywords[0] in searchable:
                pantry_specific.append(item['name'])

    return broad, protein_specific, pantry_specific


def parse_minutes(time_str):
    if not time_str:
        return 999
    import re
    mins = 0
    h = re.search(r'(\d+)\s*hour', time_str, re.IGNORECASE)
    if h:
        mins += int(h.group(1)) * 60
    m = re.search(r'(\d+)\s*min', time_str, re.IGNORECASE)
    if m:
        mins += int(m.group(1))
    return mins if mins > 0 else 999


def protein_label(name):
    lower = name.lower()
    for keyword, label in PROTEIN_KEYWORDS:
        if keyword in lower:
            return label
    return 'Other'


def is_hiatus(name):
    lower = name.lower()
    return any(h in lower for h in HIATUS)


def weeks_since(date_str):
    if not date_str:
        return 999
    try:
        cooked = datetime.strptime(date_str, '%Y-%m-%d').date()
        return (date.today() - cooked).days / 7
    except:
        return 999




def compute_adult_score(feedback):
    """
    Returns fraction of adult feedback entries that are 'liked', or None if no adult feedback.
    """
    adult = [f for f in feedback if f.get('person', '').lower() in ADULT_NAMES]
    if not adult:
        return None
    liked = sum(1 for f in adult if f.get('sentiment') == 'liked')
    return liked / len(adult)


def compute_kid_friendly(feedback):
    """
    Returns True if majority of kid feedback entries are 'liked', False if majority disliked,
    or None if no kid feedback.
    """
    kids = [f for f in feedback if f.get('person', '').lower() not in ADULT_NAMES]
    if not kids:
        return None
    liked = sum(1 for f in kids if f.get('sentiment') == 'liked')
    return liked > len(kids) / 2


def load_candidates(quick_nights=False):
    with open(METADATA_PATH) as f:
        data = json.load(f)
    recipes = data['recipes']

    today = date.today()
    month = today.month
    is_grill_season = SPRING_SUMMER[0] <= month <= SPRING_SUMMER[1]
    inventory_items = load_inventory()

    candidates = []
    for name, r in recipes.items():
        if r.get('status') in ('disliked', 'ignored'):
            continue
        if is_hiatus(name):
            continue

        last_cooked = r.get('last_cooked_date')
        age_weeks = weeks_since(last_cooked)
        if age_weeks < RECENCY_WEEKS:
            continue  # too recent

        minutes = parse_minutes(r.get('time', ''))
        is_quick = minutes <= QUICK_THRESHOLD or r.get('cooking_method') == 'slow_cooker'
        is_grill = r.get('cooking_method') == 'grill'
        protein = protein_label(name)
        health = r.get('health', 'Moderate')
        cuisine = r.get('cuisine', 'Unknown')
        times_cooked = r.get('times_cooked', 0)
        meal_type = r.get('meal_type', 'Weeknight')
        feedback = r.get('feedback', [])
        ingredients = r.get('ingredients', [])
        broad_match, specific_matches, pantry_matches = inventory_match(name, ingredients, inventory_items)

        candidates.append({
            'name': name,
            'cuisine': cuisine,
            'health': health,
            'protein': protein,
            'minutes': minutes,
            'is_quick': is_quick,
            'is_grill': is_grill,
            'is_grill_season': is_grill_season,
            'times_cooked': times_cooked,
            'last_cooked': last_cooked or 'never',
            'age_weeks': age_weeks,
            'meal_type': meal_type,
            'method': r.get('cooking_method', ''),
            'adult_score': compute_adult_score(feedback),
            'kid_friendly': compute_kid_friendly(feedback),
            'inv_broad': broad_match,
            'inv_specific': specific_matches,
            'inv_pantry': pantry_matches,
        })

    return candidates, is_grill_season


def score(c):
    """Lower score = better candidate."""
    s = 0
    # Prefer not-recently-cooked (older = better)
    s -= min(c['age_weeks'], 52) * 2
    # Prefer heart-healthy
    if c['health'] == 'Heart-Healthy':
        s -= 15
    elif c['health'] == 'Indulgent':
        s += 10
    # Prefer less-cooked (new/underused recipes)
    s += c['times_cooked'] * 2
    # Prefer grill in season
    if c['is_grill'] and c['is_grill_season']:
        s -= 4
    # Family feedback signals
    adult_score = c.get('adult_score')
    if adult_score is not None:
        if adult_score < 0.5:
            s += 15   # adults didn't like it -- deprioritize
        elif adult_score >= 0.9:
            s -= 6    # family hit -- bring back sooner
    if c.get('kid_friendly'):
        s -= 3
    # Inventory: lean toward recipes using what's on hand
    # Capped lower than health bonus so freezer contents don't override health goals
    if c.get('inv_specific'):
        s -= 7    # specific ingredient match (e.g. pork tenderloin in stock)
    elif c.get('inv_broad'):
        s -= 3    # broad protein match (e.g. any chicken recipe when chicken is stocked)
    if c.get('inv_pantry'):
        s -= 2    # pantry/dry goods match (e.g. rigatoni or heavy cream in stock)
    return s


def print_group(title, items, limit=6):
    if not items:
        return
    print(f'\n  {title}')
    for c in sorted(items, key=score)[:limit]:
        time_str = f"{c['minutes']} min" if c['minutes'] < 900 else '?'
        if c['method'] == 'slow_cooker':
            time_str = 'slow cooker'
        last_str = f"last:{c['last_cooked']}" if c['last_cooked'] != 'never' else 'never cooked'
        grill_tag = ' [GRILL]' if c['is_grill'] else ''
        new_tag = ' [NEW]' if c['times_cooked'] == 0 else ''
        kid_tag = ' [KID-FRIENDLY]' if c.get('kid_friendly') else ''
        adult_score = c.get('adult_score')
        score_tag = f' [ADULT:{adult_score:.0%}]' if adult_score is not None else ''
        if c.get('inv_specific'):
            stock_tag = f" [IN STOCK: {c['inv_specific'][0]}]"
        elif c.get('inv_broad'):
            stock_tag = ' [IN STOCK]'
        else:
            stock_tag = ''
        if c.get('inv_pantry'):
            pantry_tag = f" [PANTRY: {', '.join(c['inv_pantry'][:2])}]"
        else:
            pantry_tag = ''
        fam_tag = cuisine_family(c['cuisine'])
        print(f"    - {c['name']}{grill_tag}{new_tag}{kid_tag}{score_tag}{stock_tag}{pantry_tag} | {c['cuisine']}{fam_tag} | {c['health']} | {time_str} | {last_str}")


def main():
    parser = argparse.ArgumentParser(description='Suggest meals for the week')
    parser.add_argument('--quick', type=str, default='',
                        help='Comma-separated days with early practices needing quick meals (e.g. mon,tue,thu)')
    parser.add_argument('--week', type=str, default='',
                        help='Week start date YYYY-MM-DD (default: next Monday)')
    parser.add_argument('--json', action='store_true',
                        help='Output candidates as JSON array sorted by score (for programmatic use)')
    args = parser.parse_args()

    quick_days = [d.strip().lower() for d in args.quick.split(',') if d.strip()]

    candidates, is_grill_season = load_candidates()

    if args.json:
        import json as _json
        out = []
        for c in sorted(candidates, key=score):
            time_str = f"{c['minutes']} min" if c['minutes'] < 900 else '?'
            if c['method'] == 'slow_cooker':
                time_str = 'slow cooker'
            out.append({
                'name':       c['name'],
                'cuisine':    c['cuisine'],
                'health':     c['health'],
                'minutes':    c['minutes'],
                'time_str':   time_str,
                'is_quick':   c['is_quick'],
                'is_grill':   c['is_grill'],
                'meal_type':  c['meal_type'],
                'times_cooked': c['times_cooked'],
                'last_cooked':  c['last_cooked'],
                'score':      score(c),
            })
        print(_json.dumps(out))
        return
    inventory_items = load_inventory()

    total = len(candidates)
    print(f"MEAL CANDIDATES  |  {date.today().strftime('%b %d, %Y')}")
    if inventory_items:
        protein_stock = [i for i in inventory_items if i['category'] == 'Proteins']
        if protein_stock:
            stock_summary = ', '.join(
                f"{i['name']} x{int(i['quantity'])}" if i['quantity'] == int(i['quantity'])
                else f"{i['name']} x{i['quantity']}"
                for i in protein_stock
            )
            print(f"IN STOCK (proteins): {stock_summary}")
        pantry_stock = [i for i in inventory_items if i['category'] in PANTRY_CATEGORIES]
        if pantry_stock:
            pantry_summary = ', '.join(i['name'] for i in pantry_stock[:8])
            if len(pantry_stock) > 8:
                pantry_summary += f' (+{len(pantry_stock) - 8} more)'
            print(f"IN STOCK (pantry/dairy): {pantry_summary}")
    print(f"{total} active recipes eligible (not cooked in last {RECENCY_WEEKS} weeks)")
    if is_grill_season:
        print("** GRILL SEASON -- grill options highlighted **")
    if quick_days:
        print(f"Quick nights (<={QUICK_THRESHOLD} min or slow cooker): {', '.join(quick_days).upper()}")

    quick = [c for c in candidates if c['is_quick']]
    standard = [c for c in candidates if not c['is_quick'] and c['meal_type'] == 'Weeknight']
    weekend = [c for c in candidates if c['meal_type'] == 'Weekend']
    grill = [c for c in candidates if c['is_grill']]

    # --- QUICK MEALS ---
    print('\n=== QUICK (<= 35 min or slow cooker) ===')
    by_protein = defaultdict(list)
    for c in quick:
        by_protein[c['protein']].append(c)
    for protein in ['Fish', 'Chicken', 'Pork', 'Beef', 'Lamb', 'Vegetarian', 'Pasta/Veg', 'Other']:
        if by_protein[protein]:
            print_group(protein, by_protein[protein])

    # --- STANDARD WEEKNIGHT ---
    print('\n=== STANDARD WEEKNIGHT (35-60 min) ===')
    by_protein = defaultdict(list)
    for c in standard:
        by_protein[c['protein']].append(c)
    for protein in ['Fish', 'Chicken', 'Pork', 'Beef', 'Lamb', 'Vegetarian', 'Pasta/Veg', 'Other']:
        if by_protein[protein]:
            print_group(protein, by_protein[protein])

    # --- GRILL OPTIONS ---
    if is_grill_season and grill:
        print('\n=== GRILL OPTIONS (spring/summer) ===')
        by_protein = defaultdict(list)
        for c in grill:
            by_protein[c['protein']].append(c)
        for protein in ['Fish', 'Chicken', 'Pork', 'Beef', 'Lamb', 'Vegetarian', 'Other']:
            if by_protein[protein]:
                print_group(protein, by_protein[protein])

    # --- WEEKEND ---
    print('\n=== WEEKEND / LONGER COOK ===')
    by_protein = defaultdict(list)
    for c in weekend:
        by_protein[c['protein']].append(c)
    for protein in ['Fish', 'Chicken', 'Pork', 'Beef', 'Lamb', 'Vegetarian', 'Pasta/Veg', 'Other']:
        if by_protein[protein]:
            print_group(protein, by_protein[protein], limit=4)

    # --- NEEDS REVIEW (auto-generated .md, verify before first cook) ---
    with open(METADATA_PATH) as f:
        data = json.load(f)
    needs_review = [
        (k, v) for k, v in data['recipes'].items()
        if v.get('needs_review') and not _is_condiment(k)
        and v.get('status') not in ('disliked', 'ignored')
    ]
    if needs_review:
        print(f'\n=== NEEDS REVIEW ({len(needs_review)} entries — verify .md before first cook) ===')
        for name, r in sorted(needs_review, key=lambda x: x[0])[:10]:
            print(f"    - {name} | {r.get('cuisine','?')} | {r.get('health','?')} | {r.get('time','?')}")


if __name__ == '__main__':
    main()
