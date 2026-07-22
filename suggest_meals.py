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
import random
import re
from datetime import date
from collections import Counter, defaultdict

import candidate_scoring as cs
from candidate_scoring import (
    RECENCY_WEEKS, TRACKED_FRESH_HERBS, PANTRY_CATEGORIES,
    herbs_in_recipe, load_inventory_keywords, candidate_score as score,
)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
with open(_CONFIG_PATH) as _f:
    _CONFIG = json.load(_f)

METADATA_PATH = os.path.expanduser(_CONFIG['metadata_path'])
INVENTORY_PATH = os.path.expanduser(_CONFIG.get('inventory_path', ''))
ADULT_NAMES = set(name.lower() for name in _CONFIG['adult_names'])
BUDGET_PATH = os.path.expanduser('~/Dropbox/LLMContext/Personal/grocery_budget_status.json')
GARDEN_HERBS = [h.lower() for h in _CONFIG.get('garden_herbs', [])]

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


def load_budget():
    try:
        with open(BUDGET_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _is_condiment(title: str) -> bool:
    """Return True if title describes a standalone condiment rather than a dinner recipe."""
    words = re.sub(r"[^\w\s]", "", title.lower()).split()
    if not words or words[-1] not in _CONDIMENT_TERMINAL:
        return False
    return not any(anchor in title.lower() for anchor in _DISH_ANCHORS)


# Cuisine → family grouping for cap enforcement at step 4.
# Asian = Japanese + Korean + Chinese + Thai + Vietnamese (max 2/week as one family).
CUISINE_FAMILY_MAP: dict[str, str] = _CONFIG.get('cuisine_family_map', {})


def cuisine_family(cuisine: str) -> str:
    """Return the cuisine family string for display (e.g. 'Japanese' → '[Asian]')."""
    family = CUISINE_FAMILY_MAP.get(cuisine)
    if family and family != cuisine:
        return f' [{family}]'
    return ''

def load_candidates(quick_nights=False):
    with open(METADATA_PATH) as f:
        data = json.load(f)
    recipes = data['recipes']

    inventory_items = load_inventory_keywords(INVENTORY_PATH)
    candidates, is_grill_season = cs.load_candidates(
        recipes,
        adult_names=ADULT_NAMES,
        garden_herbs=GARDEN_HERBS,
        inventory_items=inventory_items,
    )
    for c in candidates:
        r = recipes[c['name']]
        c['bought_herbs'] = [h for h in herbs_in_recipe(r, TRACKED_FRESH_HERBS) if h not in GARDEN_HERBS]

    return candidates, is_grill_season


def print_group(title, items, limit=6):
    if not items:
        return
    print(f'\n  {title}')
    shuffled = list(items)
    random.shuffle(shuffled)
    for c in sorted(shuffled, key=score)[:limit]:
        time_str = f"{c['minutes']} min" if c['minutes'] < 900 else '?'
        if c['method'] == 'slow_cooker':
            time_str = 'slow cooker'
        last_str = f"last:{c['last_cooked']}" if c['last_cooked'] != 'never' else 'never cooked'
        effort = c.get('weeknight_effort', '')
        effort_tag = f' [{effort.upper()}]' if effort else ''
        garden = c.get('garden_herbs', [])
        garden_tag = f' [GARDEN: {garden[0]}]' if garden else ''
        grill_tag = ' [GRILL]' if c['is_grill'] else ''
        new_tag = ' [NEW]' if c['times_cooked'] == 0 else ''
        kid_tag = ' [KID ✓]' if c.get('kid_approved') else ''
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
        print(f"    - {c['name']}{effort_tag}{garden_tag}{grill_tag}{new_tag}{kid_tag}{score_tag}{stock_tag}{pantry_tag} | {c['cuisine']}{fam_tag} | {c['health']} | {time_str} | {last_str}")


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
        shuffled = list(candidates)
        random.shuffle(shuffled)
        for c in sorted(shuffled, key=score):
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
    inventory_items = load_inventory_keywords(INVENTORY_PATH)

    total = len(candidates)
    print(f"MEAL CANDIDATES  |  {date.today().strftime('%b %d, %Y')}")
    budget = load_budget()
    if budget:
        remaining = budget.get('grocery_remaining', 0)
        weekly = budget.get('suggested_weekly_spend', 0)
        as_of = budget.get('as_of', '')
        print(f"BUDGET: ${remaining:.0f} remaining this month | ${weekly:.0f}/week suggested (as of {as_of})")
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
        print(f"Busy nights (prefer LOW/MED effort): {', '.join(quick_days).upper()}")

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

    # --- FRESH HERB PAIRING (purchased herbs shared across candidates) ---
    herb_counter: Counter = Counter()
    for c in candidates:
        for herb in c.get('bought_herbs', []):
            herb_counter[herb] += 1
    shared_herbs = [(herb, cnt) for herb, cnt in herb_counter.items() if cnt >= 3]
    if shared_herbs:
        print('\n=== FRESH HERB PAIRING ===')
        for herb, cnt in sorted(shared_herbs, key=lambda x: -x[1]):
            print(f'  {herb.capitalize()}: {cnt} candidates use it — pair 2 to use the whole bunch')

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
