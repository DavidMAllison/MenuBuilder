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
from datetime import date, datetime, timedelta
from collections import defaultdict

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
with open(_CONFIG_PATH) as _f:
    _CONFIG = json.load(_f)

METADATA_PATH = os.path.expanduser(_CONFIG['metadata_path'])
ADULT_NAMES = set(name.lower() for name in _CONFIG['adult_names'])
RECENCY_WEEKS = 3          # avoid meals cooked within this many weeks
QUICK_THRESHOLD = 35       # minutes -- "quick" meals for practice nights
SPRING_SUMMER = (4, 9)     # months April-September: prioritize grill

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

    candidates = []
    for name, r in recipes.items():
        if r.get('status') != 'active':
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
        })

    return candidates, is_grill_season


def score(c):
    """Lower score = better candidate."""
    s = 0
    # Prefer not-recently-cooked (older = better)
    s -= min(c['age_weeks'], 52) * 2
    # Prefer heart-healthy
    if c['health'] == 'Heart-Healthy':
        s -= 5
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
        print(f"    - {c['name']}{grill_tag}{new_tag}{kid_tag}{score_tag} | {c['cuisine']} | {c['health']} | {time_str} | {last_str}")


def main():
    parser = argparse.ArgumentParser(description='Suggest meals for the week')
    parser.add_argument('--quick', type=str, default='',
                        help='Comma-separated days with early practices needing quick meals (e.g. mon,tue,thu)')
    parser.add_argument('--week', type=str, default='',
                        help='Week start date YYYY-MM-DD (default: next Monday)')
    args = parser.parse_args()

    quick_days = [d.strip().lower() for d in args.quick.split(',') if d.strip()]

    candidates, is_grill_season = load_candidates()

    total = len(candidates)
    print(f"MEAL CANDIDATES  |  {date.today().strftime('%b %d, %Y')}")
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

    # --- IDEAS FROM RECIPEIDEAS (status=idea) ---
    with open(METADATA_PATH) as f:
        data = json.load(f)
    ideas = [(k, v) for k, v in data['recipes'].items() if v.get('status') == 'idea']
    if ideas:
        print(f'\n=== FROM RECIPEIDEAS ({len(ideas)} untried) ===')
        for name, r in sorted(ideas, key=lambda x: x[0])[:10]:
            print(f"    - {name} | {r.get('cuisine','?')} | {r.get('health','?')} | {r.get('time','?')}")


if __name__ == '__main__':
    main()
