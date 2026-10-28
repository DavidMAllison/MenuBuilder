"""
plan_utils.py -- Plan helpers shared by the MCP server and meal_swap.py.

meal_swap.execute_swap runs inside Keanu's process, which can't import the MCP
server, so anything both need to agree on (recipe-name resolution, the shopping
CSV, day-before reminders) lives here instead of being re-implemented in each.
"""

import csv
import difflib
import io
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

from intake_checks import buy_ahead_days, day_before_note

DAYS_ORDER = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def day_meals(val) -> list:
    """Normalize a selected_meals day value to a list of recipe name strings."""
    if isinstance(val, list):
        return [str(v) for v in val if v]
    return [str(val)] if val else []


def day_date_map(week_start: date) -> dict:
    """week_start is the plan's Monday; Sunday is the day before."""
    m = {"Sun": week_start - timedelta(days=1)}
    for i, day in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]):
        m[day] = week_start + timedelta(days=i)
    return m


# ---------------------------------------------------------------------------
# Recipe-name resolution
# ---------------------------------------------------------------------------

# Words too generic to tie a name to one recipe.
_NAME_STOPWORDS = {"with", "and", "the", "style", "easy", "quick", "simple", "recipe"}


def _norm_name(s: str) -> str:
    # Drop plan annotations ("[Heart-Healthy] *NEW*"), then punctuation, so
    # "Honey Tahini" == "Honey-Tahini" and "&" == "and" differences don't matter.
    s = re.sub(r"\[[^\]]*\]|\*[^*]*\*", " ", s.lower()).replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", s))


def _name_words(s: str) -> set:
    return {w for w in _norm_name(s).split() if len(w) >= 4 and w not in _NAME_STOPWORDS}


def find_recipe_key(name: str, recipes: dict) -> Optional[str]:
    """Resolve a name to its recipe_metadata key: exact, or exact apart from case
    and punctuation ("Honey Tahini" == "Honey-Tahini"). Anything looser is a
    guess, and guesses sent "Chicken and Radicchio Salad..." to Cumin-Cardamom
    Spiced Chicken ... Salad (Oct 2026) -- a name a person typed goes through
    name_candidates() and gets confirmed instead.
    """
    name_lower = name.lower().strip()
    for key in recipes:
        if key.lower() == name_lower:
            return key
    norm = _norm_name(name)
    if not norm:
        return None
    for key in recipes:
        if _norm_name(key) == norm:
            return key
    return None


def name_candidates(name: str, recipes: dict, limit: int = 3) -> list:
    """Recipes a typed name could mean, best first -- to put in front of a person
    ("Is this the right lemon chicken?"), never to pick from automatically. Two
    recipes can share every word of "lemon chicken"."""
    pool = {k: v for k, v in recipes.items()
            if isinstance(v, dict) and v.get("status") not in ("disliked", "ignored")}
    exact = find_recipe_key(name, pool)
    if exact:
        return [exact]
    norm, words = _norm_name(name), _name_words(name)
    if words:
        hits = [k for k in pool if words <= _name_words(k)]
    else:
        hits = [k for k in pool if norm and norm in _norm_name(k)]
    hits.sort(key=lambda k: -difflib.SequenceMatcher(None, norm, _norm_name(k)).ratio())
    return hits[:limit]


# ---------------------------------------------------------------------------
# Shopping CSV
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
    "chicken thighs boneless skinless":             "boneless skinless chicken thighs",
    "skinless boneless chicken thighs":             "boneless skinless chicken thighs",
    # Either cut (intake_checks.apply_cut_swap) -- one Grocery item, buyer picks
    "boneless, skinless chicken breasts or thighs":  "boneless skinless chicken breasts or thighs",
    "boneless skinless chicken breast or thighs":    "boneless skinless chicken breasts or thighs",
    "boneless skinless chicken thighs or breast cutlets": "boneless skinless chicken breasts or thighs",
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


def cuisine_name(value) -> str:
    """One cuisine string for recipe_metadata.json. ld+json recipeCuisine can be a
    list (butterjoykitchen: ["Chinese", "Taiwanese"]); keep the first -- variety
    rules map one cuisine to a family, and readers (Keanu's similarity check)
    join it as a string."""
    if isinstance(value, (list, tuple)):
        value = next((v for v in value if isinstance(v, str) and v.strip()), "")
    return value.strip() if isinstance(value, str) else ""


def canonical_ing(name: str) -> str:
    lower = name.lower().strip()
    return _ING_ALIASES.get(lower, lower)


def display_ing(name: str) -> str:
    """Return canonical alias if one exists, else the original name (preserves case)."""
    lower = name.lower().strip()
    return _ING_ALIASES.get(lower, name)


def build_shopping_csv(selected: dict, week_start: date, recipes: dict,
                       condiment_ingredients: Callable[[str], list] = lambda name: [],
                       lunch_state_file: Optional[Path] = None,
                       garden_herbs: frozenset = frozenset()) -> str:
    """
    Generate shopping CSV content, aggregating duplicate ingredients across recipes.
    Format: Item, Notes (qty + meal context), Date (earliest date needed as YYYY-MM-DD)

    Ingredient fallback order per CLAUDE.md:
      1. structured `ingredients` array
      2. `ingredients_raw` strings
      3. no ingredients → skip recipe
    """
    day_to_date = day_date_map(week_start)

    # Collect all occurrences: (canonical_key, display_name, qty_unit, meal_label, date_str)
    occurrences: list[tuple] = []

    def _add_structured(ing_list, meal_label, date_str):
        for ing in ing_list:
            ing_name = str(ing.get("name", "")).strip()
            if not ing_name:
                continue
            if garden_herbs and any(herb in ing_name.lower() for herb in garden_herbs):
                continue
            qty  = str(ing.get("quantity", "")).strip()
            unit = str(ing.get("unit", "")).strip()
            qty_unit = f"{qty} {unit}".strip()
            occurrences.append((canonical_ing(ing_name), display_ing(ing_name), qty_unit, meal_label, date_str))

    def _add_raw(raw_list, meal_label, date_str):
        for raw in raw_list:
            raw = str(raw).strip()
            if raw and not (garden_herbs and any(herb in raw.lower() for herb in garden_herbs)):
                occurrences.append((canonical_ing(raw), display_ing(raw), "", meal_label, date_str))

    for day in DAYS_ORDER:
        if day not in selected:
            continue
        cook_date = day_to_date.get(day)
        if not cook_date:
            continue
        for name in day_meals(selected[day]):
            key = find_recipe_key(name, recipes)
            if not key:
                continue
            # A 24h marinade means the ingredients are needed the day before.
            date_str = (cook_date - timedelta(days=buy_ahead_days(recipes[key]))).isoformat()
            structured = recipes[key].get("ingredients", [])
            if structured:
                _add_structured(structured, name, date_str)
            else:
                _add_raw(recipes[key].get("ingredients_raw", []), name, date_str)
            for cdep in recipes[key].get("condiment_deps", []):
                cing = condiment_ingredients(cdep)
                if cing:
                    _add_structured(cing, f"{cdep} (for {name})", date_str)

    # Append lunch ingredients if Partner has picked THIS week (set within 7 days of week_start)
    if lunch_state_file and lunch_state_file.exists():
        try:
            lunch = json.loads(lunch_state_file.read_text())
            pick = lunch.get("current_pick")
            set_date_str = lunch.get("set_date", "")
            cutoff = (week_start - timedelta(days=7)).isoformat()
            if pick and lunch.get("status") == "selected" and set_date_str >= cutoff:
                key = find_recipe_key(pick, recipes)
                if key:
                    sat_date = (week_start - timedelta(days=2)).isoformat()
                    structured = recipes[key].get("ingredients", [])
                    if structured:
                        _add_structured(structured, "Partner's lunch", sat_date)
                    else:
                        _add_raw(recipes[key].get("ingredients_raw", []), "Partner's lunch", sat_date)
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


def load_condiment_ingredients(condiments_path: Path) -> Callable[[str], list]:
    """condiment_ingredients callable for build_shopping_csv, reading condiments.json."""
    def _get(name: str) -> list:
        try:
            return json.loads(condiments_path.read_text()).get(name, {}).get("ingredients", [])
        except Exception:
            return []
    return _get


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

def add_day_before_reminders(meals_info: list, recipes: dict) -> None:
    """Put "Start X tonight (needs 24 hours marinating)" on the day *before* any
    meal with a long required wait -- the LLM reminder for X's own day is too late
    (Miso Chicken, Oct 2026). Deterministic, so it can't be dropped or misplaced."""
    by_day = {m["day"]: m for m in meals_info}
    for m in meals_info:
        for title in m["title"].split(" + "):
            key = find_recipe_key(title, recipes)
            note = day_before_note(title, recipes[key]) if key else ""
            if not note:
                continue
            idx = DAYS_ORDER.index(m["day"])
            prev = by_day.get(DAYS_ORDER[idx - 1]) if idx > 0 else None
            if prev is not None:
                prev["reminder"] = f"{note}. {prev['reminder']}".strip()
            else:  # first night of the plan -- nothing earlier to hang it on
                m["reminder"] = f"Needs a head start: {note[len('Start '):]}. {m['reminder']}".strip()
