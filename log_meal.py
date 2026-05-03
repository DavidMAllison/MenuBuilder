#!/usr/bin/env python3
"""
log_meal.py -- Log a cooked meal into recipe_metadata.json.

Usage:
  python3 log_meal.py "Lamb Ragu with Pappardelle"
  python3 log_meal.py "Lamb Ragu" --date 2026-04-30 --adult-score 1 --notes "Kids ate noodles only"
  python3 log_meal.py "Lamb Ragu" --adult-score 0  # adults disliked it

Arguments:
  meal          Recipe name (fuzzy matched against metadata keys)
  --date        Cook date, YYYY-MM-DD (default: today)
  --adult-score 1 = liked, 0 = disliked (omit to log cook only)
  --notes       Free-text feedback note
"""

import json
import argparse
import os
import sys
from datetime import date
from difflib import get_close_matches

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
with open(_CONFIG_PATH) as _f:
    _CONFIG = json.load(_f)

METADATA_PATH = os.path.expanduser(_CONFIG['metadata_path'])


def find_recipe(recipes, query):
    keys = list(recipes.keys())
    # exact match first
    if query in keys:
        return query
    # case-insensitive exact
    lower_map = {k.lower(): k for k in keys}
    if query.lower() in lower_map:
        return lower_map[query.lower()]
    # fuzzy
    matches = get_close_matches(query, keys, n=3, cutoff=0.4)
    if not matches:
        matches = get_close_matches(query.lower(), [k.lower() for k in keys], n=3, cutoff=0.4)
        if matches:
            matches = [lower_map[m] for m in matches]
    return matches


def main():
    parser = argparse.ArgumentParser(description='Log a cooked meal')
    parser.add_argument('meal', help='Recipe name (fuzzy matched)')
    parser.add_argument('--date', default=str(date.today()), help='Cook date YYYY-MM-DD (default: today)')
    parser.add_argument('--adult-score', type=int, choices=[0, 1], dest='adult_score',
                        help='1 = liked, 0 = disliked')
    parser.add_argument('--notes', default='', help='Feedback notes')
    args = parser.parse_args()

    with open(METADATA_PATH) as f:
        data = json.load(f)
    recipes = data['recipes']

    result = find_recipe(recipes, args.meal)

    if isinstance(result, str):
        key = result
    elif isinstance(result, list) and len(result) == 1:
        key = result[0]
    elif isinstance(result, list) and len(result) > 1:
        print(f"Ambiguous match for '{args.meal}'. Did you mean:")
        for i, m in enumerate(result, 1):
            print(f"  {i}. {m}")
        choice = input("Enter number (or q to quit): ").strip()
        if choice == 'q' or not choice.isdigit():
            sys.exit(0)
        key = result[int(choice) - 1]
    else:
        print(f"No match found for '{args.meal}'.")
        sys.exit(1)

    entry = recipes[key]
    old_count = entry.get('times_cooked', 0)
    old_date = entry.get('last_cooked_date', 'never')

    entry['times_cooked'] = old_count + 1
    entry['last_cooked_date'] = args.date

    if args.adult_score is not None or args.notes:
        feedback_entry = {'date': args.date}
        if args.adult_score is not None:
            feedback_entry['adult_score'] = args.adult_score
        if args.notes:
            feedback_entry['notes'] = args.notes
        entry.setdefault('feedback', []).append(feedback_entry)

    data['last_updated'] = args.date

    with open(METADATA_PATH, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"Logged: {key}")
    print(f"  times_cooked: {old_count} -> {entry['times_cooked']}")
    print(f"  last_cooked_date: {old_date} -> {args.date}")
    if args.adult_score is not None:
        sentiment = 'liked' if args.adult_score == 1 else 'disliked'
        print(f"  feedback: adults {sentiment}" + (f" | {args.notes}" if args.notes else ''))
    elif args.notes:
        print(f"  feedback: {args.notes}")


if __name__ == '__main__':
    main()
