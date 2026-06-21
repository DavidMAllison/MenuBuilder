#!/usr/bin/env python3
"""
meal_swap.py -- Swap a planned dinner mid-week.

All MenuBuilder file writes live here. SMS/Keanu calls execute_swap()
and never touches core MenuBuilder files directly.

Three scenarios:
  A) Existing active recipe  -- incoming_name matches active recipe
  B) Existing idea           -- incoming_name matches idea; incoming_url fetches content
  C) Brand new recipe        -- incoming_url fetches content; creates .md + metadata entry

Usage:
  python3 meal_swap.py --day "Thu 5/21" --out "Roasted Mushrooms" --in "Lamb Barbacoa"
  python3 meal_swap.py --day "Thu 5/21" --out "Roasted Mushrooms" --url "https://..."
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).parent / "config.json"


def _cfg() -> dict:
    return json.loads(_CONFIG_PATH.read_text())


def _metadata_path() -> Path:
    return Path(_cfg()["metadata_path"].replace("~", str(Path.home())))


def _weeklyplan_dir() -> Path:
    return _metadata_path().parent / "weeklyplan"


def _recipes_dir() -> Path:
    return _metadata_path().parent / "recipes"


# ---------------------------------------------------------------------------
# Date / file helpers
# ---------------------------------------------------------------------------

def _parse_day_to_date(day_str: str) -> Optional[date]:
    """Parse "Thu 5/21" → date using current year."""
    m = re.search(r"(\d{1,2})/(\d{1,2})", day_str)
    if not m:
        return None
    try:
        return date(date.today().year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _find_week_files(target: date) -> Tuple[Optional[Path], Optional[Path]]:
    """Return (mealplan_path, shopping_csv_path) for the week covering target."""
    plan_dir = _weeklyplan_dir()
    for f in sorted(plan_dir.glob("mealplan_*.json"), reverse=True):
        m = re.search(r"mealplan_(\d{4}-\d{2}-\d{2})\.json", f.name)
        if not m:
            continue
        week_monday = date.fromisoformat(m.group(1))
        week_sunday = week_monday - timedelta(days=1)
        week_saturday = week_monday + timedelta(days=5)
        if week_sunday <= target <= week_saturday:
            shopping = plan_dir / f"shopping_{m.group(1)}.csv"
            return f, shopping if shopping.exists() else None
    return None, None


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def _load_metadata() -> dict:
    return json.loads(_metadata_path().read_text())


def _save_metadata(data: dict) -> None:
    _metadata_path().write_text(json.dumps(data, indent=2))


def _find_recipe(name: str, recipes: dict) -> Optional[Tuple[str, dict]]:
    """Fuzzy name match. Returns (canonical_name, entry) or None."""
    name_lower = name.lower()
    if name in recipes and isinstance(recipes[name], dict):
        return name, recipes[name]
    for k, v in recipes.items():
        if isinstance(v, dict) and k.lower() == name_lower:
            return k, v
    for k, v in recipes.items():
        if isinstance(v, dict) and (name_lower in k.lower() or k.lower() in name_lower):
            return k, v
    return None


# ---------------------------------------------------------------------------
# Recipe fetch and parse
# ---------------------------------------------------------------------------

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def _fetch_recipe(url: str) -> Optional[dict]:
    """Fetch ld+json Recipe schema from URL. Returns None on failure."""
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=20, follow_redirects=True)
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


# ---------------------------------------------------------------------------
# Ingredient parsing
# ---------------------------------------------------------------------------

_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 0.333, "⅔": 0.667, "⅛": 0.125}
_UNITS = [
    "pounds", "pound", "lbs", "lb", "ounces", "ounce", "oz",
    "cups", "cup", "tablespoons", "tablespoon", "tbsp", "teaspoons", "teaspoon", "tsp",
    "quarts", "quart", "pints", "pint", "liters", "liter", "ml",
    "grams", "gram", "kg", "bunches", "bunch", "heads", "head",
    "stalks", "stalk", "sprigs", "sprig", "cans", "can", "cloves", "clove",
    "slices", "slice", "packages", "package",
]
_CATEGORY_KEYWORDS = {
    "Proteins": ["chicken", "beef", "pork", "lamb", "fish", "salmon", "shrimp", "turkey",
                 "tofu", "tempeh", "sausage", "bacon", "tuna", "cod", "scallop", "egg"],
    "Dairy": ["milk", "cream", "cheese", "butter", "yogurt", "hummus", "feta",
              "parmesan", "mozzarella", "ricotta", "cheddar", "sour cream"],
    "Dry Goods": ["pasta", "rice", "flour", "breadcrumb", "couscous", "quinoa",
                  "noodle", "orzo", "lentil", "chickpea", "panko", "oat"],
    "Pantry/Asian": ["oil", "vinegar", "soy sauce", "fish sauce", "sesame", "hoisin",
                     "broth", "stock", "canned", "tomato paste", "coconut milk", "honey",
                     "sugar", "mustard", "tahini", "miso", "wine", "beer", "cornstarch"],
    "Spices/Herbs": ["salt", "pepper", "cumin", "paprika", "cinnamon", "oregano", "thyme",
                     "rosemary", "basil", "mint", "cilantro", "parsley", "dill", "sage",
                     "turmeric", "curry", "chili", "cayenne", "baharat", "spice", "bay",
                     "allspice", "cardamom", "clove", "nutmeg", "ginger", "garlic powder"],
}


def _categorize(name: str) -> str:
    n = name.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(k in n for k in keywords):
            return category
    return "Produce"


def _parse_ingredient(s: str) -> dict:
    """Parse "1½ lbs chicken thighs, trimmed" → {name, quantity, unit, category}."""
    for frac, val in _FRACTIONS.items():
        s = s.replace(frac, f" {val} ")
    # Handle ASCII mixed numbers "1 1/2" → "1.5" before plain fractions
    s = re.sub(r"(\d+)\s+(\d+)/(\d+)",
               lambda m: str(round(int(m.group(1)) + int(m.group(2)) / int(m.group(3)), 4)), s)
    # Handle plain ASCII fractions "1/2" → "0.5"
    s = re.sub(r"(\d+)/(\d+)",
               lambda m: str(round(int(m.group(1)) / int(m.group(2)), 4)), s)
    s = re.sub(r"\s+", " ", s).strip()

    quantity = ""
    num_m = re.match(r"^([\d\.\s]+)", s)
    if num_m:
        try:
            parts = num_m.group(1).strip().split()
            quantity = str(round(sum(float(p) for p in parts), 4))
            s = s[num_m.end():].strip()
        except ValueError:
            pass

    unit = ""
    for u in _UNITS:
        if re.match(rf"^{re.escape(u)}\b", s, re.IGNORECASE):
            unit = u
            s = s[len(u):].lstrip(" ,.")
            break

    name = re.sub(r",.*$", "", s).strip()
    return {"name": name, "quantity": quantity, "unit": unit, "category": _categorize(name)}


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def _create_recipe_md(recipe: dict, source_url: str, source_name: str) -> Path:
    title = recipe["title"]
    path = _recipes_dir() / (title.replace(" ", "_") + ".md")
    lines = [
        f"# {title}", "",
        f"**Time**: {recipe.get('time', '')}  ",
        f"**Yield**: {recipe.get('servings', '')}  ",
        f"**Adapted from**: [{source_name}]({source_url})",
        "", "## Ingredients", "",
    ]
    for ing in recipe.get("ingredients", []):
        lines.append(f"- {ing}")
    lines += ["", "## Instructions", ""]
    for i, step in enumerate(recipe.get("instructions", []), 1):
        lines.append(f"{i}. {step}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _update_meal_plan_json(plan_path: Path, day_str: str, outgoing: str,
                            incoming: str, time_str: str, health: str, url: str) -> None:
    day_abbrev = day_str.split()[0]
    data = json.loads(plan_path.read_text())
    for meal in data.get("meals", []):
        if meal.get("day") == day_abbrev and meal.get("title", "").lower() == outgoing.lower():
            meal["title"] = incoming
            meal["time"] = time_str
            meal["health"] = health
            meal["url"] = url
            break
    plan_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.chmod(plan_path, 0o666)


def _update_shopping_csv(csv_path: Path, outgoing: str,
                          incoming: str, ingredients: List[dict],
                          target_date: date) -> None:
    rows = list(csv.DictReader(csv_path.read_text().splitlines()))
    kept = [r for r in rows if outgoing.lower() not in r.get("Notes", "").lower()]

    date_str = target_date.isoformat()
    for ing in ingredients:
        qty = ing.get("quantity", "")
        unit = ing.get("unit", "")
        qty_str = f"{qty} {unit}".strip() if qty or unit else ""
        notes = f"{qty_str} | {incoming}" if qty_str else incoming
        kept.append({"Item": ing["name"], "Notes": notes, "Date": date_str})

    out = StringIO()
    writer = csv.DictWriter(out, fieldnames=["Item", "Notes", "Date"])
    writer.writeheader()
    writer.writerows(kept)
    csv_path.write_text(out.getvalue())
    os.chmod(csv_path, 0o666)


def _log_swap_to_feedback(outgoing: str, incoming: str, target_date: date) -> None:
    feedback_path = _weeklyplan_dir() / "feedback_current.json"
    data = json.loads(feedback_path.read_text()) if feedback_path.exists() else {"entries": []}
    data["entries"].append({
        "date": target_date.isoformat(),
        "recipe": outgoing,
        "sentiment": "skipped",
        "notes": f"Swapped out for {incoming}.",
        "source": "swap",
    })
    feedback_path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Plan file updaters
# ---------------------------------------------------------------------------

def _recompute_balance(plan_path: Path) -> None:
    """Recount health labels across meals and update balance dict."""
    data = json.loads(plan_path.read_text())
    counts: Dict[str, int] = {}
    for meal in data.get("meals", []):
        h = meal.get("health", "")
        if h:
            counts[h] = counts.get(h, 0) + 1
    data["balance"] = counts
    plan_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.chmod(plan_path, 0o666)


def _suggest_start_time(time_str: str) -> str:
    """Subtract cook time from 6:30 PM dinner target to get a suggested start time."""
    total_min = 0
    h = re.search(r"(\d+)\s*h", time_str, re.IGNORECASE)
    m = re.search(r"(\d+)\s*m", time_str, re.IGNORECASE)
    if h:
        total_min += int(h.group(1)) * 60
    if m:
        total_min += int(m.group(1))
    if not total_min:
        return "6:00 PM"
    start_min = 18 * 60 + 30 - total_min  # 6:30 PM in minutes from midnight
    hour, minute = divmod(max(start_min, 0), 60)
    period = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {period}"


def _update_reminder_line(plan_path: Path, day_str: str, incoming: str, time_str: str) -> None:
    """Update the reminder field for the swapped day."""
    day_abbrev = day_str.split()[0]  # "Wed 5/27" → "Wed"
    start_time = _suggest_start_time(time_str)
    time_display = time_str if time_str else "?"
    new_reminder = f"{time_display} — {incoming}, start by {start_time}."
    data = json.loads(plan_path.read_text())
    for meal in data.get("meals", []):
        if meal.get("day") == day_abbrev:
            meal["reminder"] = new_reminder
            break
    plan_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.chmod(plan_path, 0o666)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute_swap(
    day: str,
    outgoing_recipe: str,
    incoming_name: str = None,
    incoming_url: str = None,
    incoming_content: dict = None,
) -> dict:
    """
    Swap a planned dinner. Returns {"success": bool, "message": str, ...}.

    Args:
        day:              "Thu 5/21"
        outgoing_recipe:  name of recipe being replaced
        incoming_name:    name of existing recipe (active or idea)
        incoming_url:     URL of recipe to fetch (required for idea or brand new)
        incoming_content: pre-parsed recipe dict if URL fetch failed —
                          {"title", "time", "servings", "ingredients": [str], "instructions": [str]}
    """
    cfg = _cfg()
    github_base = cfg.get("github_pages_base_url", "")

    target = _parse_day_to_date(day)
    if not target:
        return {"success": False, "message": f"Could not parse day: {day!r}"}

    plan_path, csv_path = _find_week_files(target)
    if not plan_path:
        return {"success": False, "message": f"No meal plan found covering {day}"}
    if not csv_path:
        return {"success": False, "message": f"No shopping CSV found for week of {day}"}

    data = _load_metadata()
    recipes = data.get("recipes", {})

    # --- Resolve incoming recipe ---
    recipe_data = None
    canonical_name = None
    source_name = "Unknown"
    health = "Heart-Healthy"
    needs_activation = False

    if incoming_name:
        match = _find_recipe(incoming_name, recipes)
        if not match:
            return {"success": False, "message": f"Recipe not found: {incoming_name!r}"}
        canonical_name, entry = match

        if entry.get("status") == "active":
            # Scenario A: already active — use existing metadata
            recipe_data = {
                "title": canonical_name,
                "time": entry.get("time", ""),
                "servings": entry.get("servings", ""),
                "ingredients": [
                    f"{i.get('quantity','')} {i.get('unit','')} {i['name']}".strip()
                    for i in entry.get("ingredients", [])
                ],
                "instructions": [],
                "cuisine": entry.get("cuisine", ""),
            }
            health = entry.get("health", "Heart-Healthy")
            source_name = entry.get("source", "")
        else:
            # Scenario B: idea — needs activation
            needs_activation = True
            health = entry.get("health", "Heart-Healthy")
            source_name = entry.get("source", "")
            if incoming_url:
                recipe_data = _fetch_recipe(incoming_url)
                if not recipe_data:
                    if incoming_content:
                        recipe_data = incoming_content
                    else:
                        return {"success": False, "message": "fetch_failed",
                                "detail": f"Could not fetch {incoming_url}. Provide incoming_content."}
            elif incoming_content:
                recipe_data = incoming_content
            else:
                return {"success": False, "message": f"Idea recipe requires incoming_url or incoming_content"}

    elif incoming_url or incoming_content:
        # Scenario C: brand new
        needs_activation = True
        if incoming_url and not incoming_content:
            recipe_data = _fetch_recipe(incoming_url)
            if not recipe_data:
                return {"success": False, "message": "fetch_failed",
                        "detail": f"Could not fetch {incoming_url}. Provide incoming_content."}
        else:
            recipe_data = incoming_content
    else:
        return {"success": False, "message": "Provide incoming_name, incoming_url, or incoming_content"}

    if not recipe_data:
        return {"success": False, "message": "Could not resolve incoming recipe"}

    title = recipe_data.get("title") or canonical_name or "Unknown Recipe"
    canonical_name = canonical_name or title
    time_str = recipe_data.get("time", "")

    # Parse ingredients to structured format
    raw = recipe_data.get("ingredients", [])
    if raw and isinstance(raw[0], str):
        parsed_ingredients = [_parse_ingredient(i) for i in raw]
    else:
        parsed_ingredients = list(raw)

    # Build GitHub Pages URL
    filename = title.replace(" ", "_")
    recipe_url = f"{github_base}/{filename}" if github_base else ""

    # --- Activate recipe if needed (Scenario B or C) ---
    if needs_activation:
        _create_recipe_md(recipe_data, incoming_url or "", source_name)
        clean_ingredients = [{k: v for k, v in i.items()} for i in parsed_ingredients]
        recipes[canonical_name] = {
            "title": canonical_name,
            "filename": f"{filename}.md",
            "source": source_name,
            "cuisine": recipe_data.get("cuisine", ""),
            "meal_type": "Weeknight",
            "health": health,
            "times_cooked": 0,
            "time": time_str,
            "servings": recipe_data.get("servings", ""),
            "status": "active",
            "cooking_method": "oven",
            "last_cooked_date": None,
            "ingredients": clean_ingredients,
            "feedback": [],
        }
        data["recipes"] = recipes
        _save_metadata(data)

    # --- Update files ---
    _update_shopping_csv(csv_path, outgoing_recipe, canonical_name, parsed_ingredients, target)
    _update_meal_plan_json(plan_path, day, outgoing_recipe, canonical_name, time_str, health, recipe_url)
    _recompute_balance(plan_path)
    _update_reminder_line(plan_path, day, canonical_name, time_str)
    _log_swap_to_feedback(outgoing_recipe, canonical_name, target)

    # --- Re-run apps ---
    subprocess.Popen(["open", "/Applications/WeeklyShoppingList.app"])
    subprocess.Popen(["open", "/Applications/WeeklyMealCalendar.app"])

    return {
        "success": True,
        "message": f"Swapped {outgoing_recipe} → {canonical_name} on {day}.",
        "incoming_recipe": canonical_name,
        "url": recipe_url,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Swap a planned dinner mid-week")
    parser.add_argument("--day", required=True, help='Day string, e.g. "Thu 5/21"')
    parser.add_argument("--out", required=True, metavar="OUTGOING", help="Recipe being replaced")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--in", dest="incoming_name", metavar="NAME",
                       help="Name of existing recipe to swap in")
    group.add_argument("--url", dest="incoming_url", metavar="URL",
                       help="URL of new recipe to fetch")
    args = parser.parse_args()

    result = execute_swap(
        day=args.day,
        outgoing_recipe=args.out,
        incoming_name=args.incoming_name,
        incoming_url=args.incoming_url,
    )
    print(result["message"])
    sys.exit(0 if result["success"] else 1)
