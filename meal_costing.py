#!/usr/bin/env python3
"""
meal_costing.py

Estimates avg_cost_per_serving for a recipe from grocery price history.
Matching and unit conversion are deliberately conservative: an ingredient
only contributes to the total if its recipe unit and the purchased unit
share a family (weight, volume, or count) -- no cross-family guessing
(e.g. converting "2 tbsp olive oil" into a $/lb price is not attempted).

Used by compute_meal_costs.py.
"""

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "mcp"))
from menu_server import _canonical_ing  # noqa: E402

COVERAGE_THRESHOLD_PCT = 50.0

# Manually-maintained reference for ingredients where matching the recipe's
# literal quantity against a per-unit receipt price gives the wrong answer:
#   - Bulk proteins, where receipt OCR can't reliably capture a real
#     per-pound price (e.g. Costco meat often rings up as a flat "each" line
#     even though it's sold by weight), and where actual usage is "one
#     repackaged portion per meal" regardless of how many pieces a recipe's
#     ingredient list literally calls for.
#   - Fresh herbs (cilantro, parsley, dill, ...) sold as a whole bunch, where
#     a recipe might call for "2 tbsp" but buying anything less than the
#     whole bunch isn't an option -- the real cost incurred is the full
#     bunch price, not a fraction of it. Garden-grown herbs (see
#     config.json's garden_herbs) are deliberately NOT listed here since
#     they're free, not purchased.
# Update prices here as they change; see config.json. Never applies to
# "dried X" ingredients (see the guard in _match_override) -- those are
# genuinely priced/used per pinch from a spice jar, not a fresh bunch.
with open(Path(__file__).parent / "config.json") as _f:
    _INGREDIENT_PORTION_OVERRIDES: dict = json.load(_f).get("ingredient_portion_overrides", {})

_WEIGHT_TO_OZ = {
    "oz": 1, "ounce": 1, "ounces": 1,
    "lb": 16, "lbs": 16, "pound": 16, "pounds": 16,
    "g": 1 / 28.3495, "gram": 1 / 28.3495, "grams": 1 / 28.3495,
    "kg": 35.274, "kilogram": 35.274, "kilograms": 35.274,
}

_VOLUME_TO_TSP = {
    "tsp": 1, "teaspoon": 1, "teaspoons": 1,
    "tbsp": 3, "tablespoon": 3, "tablespoons": 3,
    "fl oz": 6, "fluid ounce": 6, "fluid ounces": 6,
    "cup": 48, "cups": 48,
    "pint": 96, "pints": 96,
    "quart": 192, "quarts": 192,
    "gallon": 768, "gallons": 768,
    "ml": 0.202884, "milliliter": 0.202884, "milliliters": 0.202884,
    "l": 202.884, "liter": 202.884, "liters": 202.884,
}

# Discrete/count units, treated as interchangeable "1 purchased item" for
# costing. Deliberately excludes sub-divisions of a purchased item -- a
# garlic "clove" is not a whole bulb, a "slice" of bacon is not a whole
# package, a "sprig" is not a whole bunch -- matching those against an
# each-priced item would wildly overcount (e.g. 40 cloves x $/bulb). Recipes
# using those units are left unmatched rather than guessed.
_COUNT_UNITS = {"", "ea", "each", "ct", "count", "whole", "large", "medium", "small"}

_FRACTION_RE = re.compile(r"^(\d+)?\s*(\d+)/(\d+)$")
_RANGE_RE = re.compile(r"^([\d.]+)\s*-\s*([\d.]+)$")

# Same subdivision concept as the units excluded from _COUNT_UNITS above, but
# checked against the ingredient *name* -- some structured entries fold the
# unit into the name instead of the unit field (name: "garlic cloves",
# unit: "", quantity: "40"), which would otherwise silently default to the
# count family and get treated as 40 whole bulbs.
_SUBDIVISION_WORDS_RE = re.compile(
    r"\b(cloves?|slices?|sprigs?|stalks?|pieces?)\b", re.IGNORECASE
)

# Count-family ("ea") matching only makes sense for items genuinely bought
# and used as whole units (1 onion, 1 chicken, 1 block of cheese). Spices,
# oils, and other pantry staples are priced per whole container on a
# receipt but used in teaspoon/tablespoon amounts in a recipe -- matching
# those on "ea" would price a pinch of salt as a whole $2.69 box.
_COUNT_ELIGIBLE_CATEGORIES = {"Produce", "Proteins", "Dairy"}

# A per-serving estimate above this is treated as almost certainly a bad
# match (e.g. a multi-item package price misread as a single-item "ea"
# price) rather than a real result, given this family's weekly grocery
# budget implies dinners average well under this per serving.
_SANITY_CEILING_PER_SERVING = 20.0


def _unit_family(unit: str):
    """Returns (family, factor_to_base_unit) or (None, None) if unrecognized."""
    u = (unit or "").lower().strip()
    if u in _WEIGHT_TO_OZ:
        return "weight", _WEIGHT_TO_OZ[u]
    if u in _VOLUME_TO_TSP:
        return "volume", _VOLUME_TO_TSP[u]
    if u in _COUNT_UNITS:
        return "count", 1.0
    return None, None


def _parse_qty(raw: str):
    s = (raw or "").strip()
    if not s:
        return 1.0  # blank quantity + count unit (e.g. "eggs" / "large") implies 1 each
    m = _RANGE_RE.match(s)
    if m:
        lo, hi = m.groups()
        return (float(lo) + float(hi)) / 2
    m = _FRACTION_RE.match(s)
    if m:
        whole, num, den = m.groups()
        try:
            return (float(whole) if whole else 0.0) + float(num) / float(den)
        except ZeroDivisionError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _avg_price_per_unit(entries: list):
    """Average price_per_unit across entries, keyed by their most common quantity_unit."""
    valid = [e for e in entries if e.get("price_per_unit") is not None and e.get("quantity_unit")]
    if not valid:
        return None, None
    unit = Counter(e["quantity_unit"] for e in valid).most_common(1)[0][0]
    same_unit = [e["price_per_unit"] for e in valid if e["quantity_unit"] == unit]
    if not same_unit:
        return None, None
    return sum(same_unit) / len(same_unit), unit


# Descriptors that show up in recipe ingredient phrasing but rarely in
# receipt product names (or vice versa) -- stripped before token matching so
# "boneless skinless chicken breasts" can match a receipt's "chicken breast".
_INGREDIENT_STOPWORDS = {"boneless", "skinless", "bone", "skin", "in", "on", "fresh", "frozen"}


def _significant_tokens(text: str) -> set:
    tokens = set()
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in _INGREDIENT_STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]  # naive plural strip
        tokens.add(word)
    return tokens


def _match_price(name: str, price_history: dict):
    """Token-set match, either direction, so word order/plurals/brand prefixes
    don't block a match (e.g. "boneless skinless chicken breasts" <->
    "hi top chicken breast boneless"). Aggregates every matching key's price
    history together rather than stopping at the first hit."""
    canon = _canonical_ing(name).lower().strip()
    ing_tokens = _significant_tokens(canon)
    if not ing_tokens:
        return None, None
    matching_entries = []
    for key, entries in price_history.items():
        key_tokens = _significant_tokens(key)
        if key_tokens and (ing_tokens <= key_tokens or key_tokens <= ing_tokens):
            matching_entries.extend(entries)
    return _avg_price_per_unit(matching_entries)


def _match_override(name: str):
    """Checks _INGREDIENT_PORTION_OVERRIDES by the same token-subset rule as
    _match_price. Returns a flat per-meal cost, or None. Applies regardless
    of the recipe's own stated quantity/unit -- these overrides exist
    specifically because actual usage ("one package/portion per meal", "the
    whole bunch since you can't buy less") doesn't track what a recipe
    literally calls for.

    Two spec shapes in config.json:
      {"price_per_portion": X}                    -- flat total, e.g. a
        known per-item price derived from a receipt ("3 racks for $25.39"
        -> 8.46/rack, or a $1.29 bunch of cilantro). Use when the item is
        bought as discrete units.
      {"price_per_lb": X, "portion_lb": Y}         -- for bulk-bought meat
        repackaged into meal-sized portions (e.g. a Costco case split into
        6 packs), priced by weight but portioned by hand, not by the store.

    Never matches "dried X" ingredients -- those are genuinely used/priced
    per pinch from a spice jar, not bought fresh as a whole bunch, so they'd
    otherwise wrongly inherit a fresh-herb override (e.g. "dried parsley"
    matching the "parsley" bunch price).
    """
    if re.search(r"\bdried\b", name, re.IGNORECASE):
        return None
    ing_tokens = _significant_tokens(_canonical_ing(name))
    if not ing_tokens:
        return None
    for key, spec in _INGREDIENT_PORTION_OVERRIDES.items():
        key_tokens = _significant_tokens(key)
        if key_tokens and (ing_tokens <= key_tokens or key_tokens <= ing_tokens):
            if "price_per_portion" in spec:
                return spec["price_per_portion"]
            return spec["price_per_lb"] * spec["portion_lb"]
    return None


def estimate_recipe_cost(recipe: dict, price_history: dict) -> dict:
    ingredients = recipe.get("ingredients") or []
    total = len(ingredients)
    matched = 0
    cost_total = 0.0

    for ing in ingredients:
        override_cost = _match_override(ing.get("name", ""))
        if override_cost is not None:
            cost_total += override_cost
            matched += 1
            continue

        price_per_unit, purchased_unit = _match_price(ing.get("name", ""), price_history)
        if price_per_unit is None:
            continue
        qty = _parse_qty(ing.get("quantity", ""))
        if qty is None:
            continue
        recipe_family, recipe_factor = _unit_family(ing.get("unit", ""))
        purchased_family, purchased_factor = _unit_family(purchased_unit)
        if recipe_family is None or purchased_family != recipe_family:
            continue
        if recipe_family == "count":
            if _SUBDIVISION_WORDS_RE.search(ing.get("name", "")):
                continue
            if ing.get("category") not in _COUNT_ELIGIBLE_CATEGORIES:
                continue
            if qty != 1:
                # Receipts record "ea" as the price of one purchased line item
                # (a whole package), not a per-unit-inside-the-package price --
                # there's no package-content count to divide by. "1 onion" at
                # $1.78/ea is a safe match; "6 chicken breasts" at $12.55/ea
                # almost certainly means one $12.55 multi-breast package, and
                # multiplying by 6 would wildly overcount. Only trust it when
                # the recipe wants exactly one purchased unit.
                continue
        cost_total += (qty * recipe_factor) * (price_per_unit / purchased_factor)
        matched += 1

    coverage_pct = round(100 * matched / total, 1) if total else 0.0

    servings_match = re.search(r"\d+", str(recipe.get("servings", "")))
    servings = int(servings_match.group()) if servings_match else 4

    avg_cost = round(cost_total / servings, 2) if servings else None
    if avg_cost is not None and (
        coverage_pct < COVERAGE_THRESHOLD_PCT or avg_cost > _SANITY_CEILING_PER_SERVING
    ):
        avg_cost = None

    return {
        "avg_cost_per_serving": avg_cost,
        "cost_coverage_pct": coverage_pct,
        "cost_ingredients_matched": matched,
        "cost_ingredients_total": total,
        "cost_last_calculated": date.today().isoformat(),
    }
