#!/usr/bin/env python3
"""
normalize_cuisines.py -- One-shot (re-runnable) cuisine normalization.

Fixes:
  1. Empty cuisine values — assigned by recipe name/source lookup
  2. Variant cuisine strings — collapsed to canonical values
  3. Tombstones Texas BBQ Sauce (condiment in active pool)

Run: python3 normalize_cuisines.py [--dry-run]
"""

import json
import sys
import os
from datetime import date

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
with open(_CONFIG_PATH) as f:
    _CONFIG = json.load(f)
METADATA_PATH = os.path.expanduser(_CONFIG['metadata_path'])

DRY_RUN = '--dry-run' in sys.argv

# ---------------------------------------------------------------------------
# Variant → canonical mapping (covers non-standard cuisine strings)
# ---------------------------------------------------------------------------
VARIANT_MAP = {
    'Italian-American':              'Italian',
    'Chinese-American':              'Chinese',
    'Vietnamese-American':           'Vietnamese',
    'Chinese home-style':            'Chinese',
    'Cantonese Chinese':             'Chinese',
    'Moroccan / North African':      'Moroccan',
    'Moroccan':                      'Moroccan',
    'Peruvian / Latin American':     'Peruvian',
    'Middle Eastern / Levantine':    'Middle Eastern',
    'French / European bistro':      'French',
    'American / technique':          'American',
    'Mexican (Baja California)':     'Mexican',
    'Mexican (Central Mexico / mole-adjacent)': 'Mexican',
    'Mexican (Mexico City classic)': 'Mexican',
    'Mexican (Northern coastal / Sonora)': 'Mexican',
    # "Asian" catch-alls resolved per-recipe below in EMPTY_MAP
}

# ---------------------------------------------------------------------------
# Empty-cuisine assignments (recipe name → correct cuisine)
# Determined by recipe name + source
# ---------------------------------------------------------------------------
EMPTY_MAP = {
    'Khao Man Gai Thai Chicken and Rice':                        'Thai',
    'Moo Shu Chicken':                                           'Chinese',
    'Cantonese Steamed Fish':                                    'Chinese',
    'Halal Cart Chicken and Rice':                               'Middle Eastern',
    'Mesquite Grilled Tacos Rasurados':                          'Mexican',
    'Seared Pork Tenderloin with Roasted Tomatillos and Apples': 'Mexican',
    'Rosemary Lemon Grilled Spatchcock Chicken':                 'American',
    "Chairman Mao's Red Braised Pork Belly":                     'Chinese',
    'Grilled Brined Pork Chops with Garlic-Herb Oil':            'American',
    'Gochujang-Glazed Ribs':                                     'Korean',
    'Grilled Spice-Rubbed Pork Tenderloin with Charred Fingerling Potato Salad': 'American',
    'Smash Burgers':                                             'American',
    'Texas BBQ Sauce':                                           'American',  # condiment, already ignored
    'Spicy Peanut Noodles with Shrimp and Snow Peas':            'Chinese',
    'Yakisoba':                                                  'Japanese',
    'Braised Pork Belly Tacos':                                  'Mexican',
    'Lemon Chicken with Potatoes and Chickpeas':                 'Mediterranean',
    'Soy-Glazed Chicken':                                        'Japanese',
    'Miso Chicken and Rice':                                     'Japanese',
    'One-Pan Fish & Broccoli Curry':                             'Indian',
    'Onion Masala Chicken':                                      'Indian',
    'Fish Curry':                                                'Indian',
    'Barbecued Chuck Roast':                                     'American',
    'Shrimp with Black Bean Sauce For Two':                      'Chinese',
}

# "Asian" catch-alls resolved per recipe name
ASIAN_RESOLVE = {
    'Ginger-Sesame Chicken and Broccoli Stir Fry': 'Chinese',
    'Pan-Seared Broccolini':                       'Chinese',  # side dish / stir-fry technique
}

TOMBSTONE: set = set()  # nothing to tombstone — Texas BBQ Sauce is already ignored


def main():
    with open(METADATA_PATH) as f:
        data = json.load(f)

    recipes = data['recipes']
    changes = []

    for name, entry in recipes.items():
        if not isinstance(entry, dict):
            continue

        # Tombstone condiments
        if name in TOMBSTONE and entry.get('status') == 'active':
            if not DRY_RUN:
                entry['status'] = 'ignored'
                entry['ignore_reason'] = 'condiment — not a dinner recipe'
            changes.append(f'  TOMBSTONE  {name!r}')
            continue

        current = entry.get('cuisine', '')

        # Fix "Asian" catch-alls
        if current == 'Asian' and name in ASIAN_RESOLVE:
            new = ASIAN_RESOLVE[name]
            if not DRY_RUN:
                entry['cuisine'] = new
            changes.append(f'  VARIANT    {name!r}: "Asian" → "{new}"')
            continue

        # Fix known variants
        if current in VARIANT_MAP:
            new = VARIANT_MAP[current]
            if new != current:
                if not DRY_RUN:
                    entry['cuisine'] = new
                changes.append(f'  VARIANT    {name!r}: "{current}" → "{new}"')
            continue

        # Fix empty cuisine
        if current == '' and name in EMPTY_MAP:
            new = EMPTY_MAP[name]
            if not DRY_RUN:
                entry['cuisine'] = new
            changes.append(f'  EMPTY      {name!r}: "" → "{new}"')
            continue

        # Warn about remaining empty/unknown
        if current in ('', 'unknown'):
            changes.append(f'  UNRESOLVED {name!r}: cuisine={current!r} source={entry.get("source","?")}')

    if changes:
        print(f'{"DRY RUN — " if DRY_RUN else ""}Changes ({len([c for c in changes if not c.startswith("  UNRESOLVED")])} applied, see UNRESOLVED for gaps):')
        for c in changes:
            print(c)
    else:
        print('No changes needed.')

    if not DRY_RUN and changes:
        data['last_updated'] = date.today().isoformat()
        with open(METADATA_PATH, 'w') as f:
            json.dump(data, f, indent=2)
        print(f'\nWrote {METADATA_PATH}')


if __name__ == '__main__':
    main()
