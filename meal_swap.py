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
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from github_sync import request_publish
from gui_launch import request_gui_launch
from intake_checks import condiment_links, run_intake_checks
from plan_utils import add_day_before_reminders, build_shopping_csv, load_condiment_ingredients


def _chmod_shared(path) -> None:
    """Best-effort 666 on a shared plan/shopping file. Access between
    davidallison and allisonbot actually comes from the inherited group:staff
    ACL on cooking-state/ -- chmod is only a belt-and-braces extra, and it
    raises PermissionError for whichever account doesn't own the file (only
    the owner may chmod), which used to abort finalize/swap mid-write."""
    try:
        os.chmod(path, 0o666)
    except PermissionError:
        pass


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).parent / "config.json"


def _cfg() -> dict:
    return json.loads(_CONFIG_PATH.read_text())


def _metadata_path() -> Path:
    return Path(_cfg()["metadata_path"].replace("~", str(Path.home())))


def _weeklyplan_dir() -> Path:
    return Path(_cfg()["state_dir"]) / "weeklyplan"


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
            recipe_yield = item.get("recipeYield", "")
            if isinstance(recipe_yield, list):
                recipe_yield = recipe_yield[-1] if recipe_yield else ""

            return {
                "title": item.get("name", "").strip(),
                "time": _parse_duration(item.get("totalTime") or item.get("cookTime", "")),
                "servings": str(recipe_yield).strip(),
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

def _create_recipe_md(recipe: dict, source_url: str, source_name: str,
                      condiments: Optional[dict] = None) -> Path:
    """source_url/source_name aren't written into the file — the caller
    persists source_url to recipe_metadata.json, and the GitHub Pages
    template renders attribution from there. See recipe_md.py for the
    canonical builder every intake path shares."""
    from recipe_md import build_recipe_md
    title = recipe["title"]
    path = _recipes_dir() / (title.replace(" ", "_") + ".md")
    content = build_recipe_md(
        title=title,
        ingredients=recipe.get("ingredients", []),
        instructions=recipe.get("instructions", []),
        condiments=condiments,
    )
    path.write_text(content, encoding="utf-8")
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
            meal["urls"] = [url]
            break
    plan_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    _chmod_shared(plan_path)


def _rebuild_shopping_csv(plan_path: Path, csv_path: Path, recipes: dict) -> None:
    """Regenerate the week's shopping CSV from the plan file with the same builder
    finalize uses. Editing rows in place dropped whole aggregated rows that named
    the outgoing recipe ("garlic, 4 (Salmon) + 3 (Miso Chicken)") and couldn't
    redate a day-before marinade (Oct 27 2026)."""
    plan = json.loads(plan_path.read_text())
    selected = {m["day"]: m["title"].split(" + ") for m in plan.get("meals", []) if m.get("title")}
    week_monday = date.fromisoformat(plan_path.stem.split("_", 1)[1])
    cfg = _cfg()
    content = build_shopping_csv(
        selected, week_monday, recipes,
        condiment_ingredients=load_condiment_ingredients(_metadata_path().parent / "condiments.json"),
        lunch_state_file=Path(cfg["state_dir"]) / "lunch_state.json",
        garden_herbs=frozenset(h.lower() for h in cfg.get("garden_herbs", [])),
    )
    csv_path.write_text(content)
    _chmod_shared(csv_path)


def _refresh_day_before_notes(plan_path: Path, recipes: dict) -> None:
    """Re-place "Start X tonight (needs ...)" notes after meals move between days."""
    data = json.loads(plan_path.read_text())
    meals = data.get("meals", [])
    for m in meals:
        r = m.get("reminder", "")
        r = re.sub(r"Start [^.]*? tonight \(needs [^)]*\)\.\s*", "", r)
        m["reminder"] = re.sub(r"Needs a head start: [^.]*?\(needs [^)]*\)\.\s*", "", r).strip()
    add_day_before_reminders(meals, recipes)
    plan_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    _chmod_shared(plan_path)


def _log_swap_to_feedback(outgoing: str, incoming: str, target_date: date) -> None:
    feedback_path = _weeklyplan_dir() / "feedback_current.json"
    data = json.loads(feedback_path.read_text()) if feedback_path.exists() else {"entries": []}
    data["entries"].append({
        "date": target_date.isoformat(),
        "recipe": outgoing,
        "sentiment": "skipped",
        "note": f"Swapped out for {incoming}.",
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
    _chmod_shared(plan_path)


def _suggest_start_time(time_str: str) -> str:
    """Subtract active cook time from 6:30 PM dinner target to get a suggested start
    time. Active only -- "25 minutes plus 24 hours marinating" used to come out
    as "start by 12:00 AM"."""
    from candidate_scoring import parse_minutes
    total_min = parse_minutes(time_str)
    if total_min == 999:
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
    _chmod_shared(plan_path)


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
                "ingredients": list(entry.get("ingredients_raw", [])),
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
            elif entry.get("ingredients_raw") and entry.get("instructions"):
                recipe_data = {
                    "title": canonical_name,
                    "time": entry.get("time", ""),
                    "servings": entry.get("servings", ""),
                    "ingredients": list(entry["ingredients_raw"]),
                    "instructions": list(entry["instructions"]),
                    "cuisine": entry.get("cuisine", ""),
                }
                incoming_url = incoming_url or entry.get("source_url", "")
            else:
                return {"success": False, "message": "Idea recipe requires incoming_url or incoming_content"}

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

    day_abbrev = day.split()[0]
    for meal in json.loads(plan_path.read_text()).get("meals", []):
        if meal.get("day") != day_abbrev and (recipe_data.get("title") or canonical_name or "").lower() in (
                t.lower() for t in meal.get("title", "").split(" + ")):
            return {"success": False, "message": "already_planned",
                    "detail": f"{meal['title']} is already on {meal['day']}. To switch two "
                              f"nights, use reorder_days('{day_abbrev}', '{meal['day']}')."}

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
        # JSON invariant: no entry without ingredients_raw + instructions.
        ingredients_raw = [i for i in raw if isinstance(i, str) and i.strip()]
        instructions = [s for s in recipe_data.get("instructions", []) if isinstance(s, str) and s.strip()]
        if not ingredients_raw or not instructions:
            return {"success": False, "message": "fetch_failed",
                    "detail": "Recipe is missing ingredients or instructions. Provide incoming_content."}

        # Required waits (24h marinade) and linked sub-recipes -- same intake
        # checks as the Review UI and SMS URL paths. Before the .md write so
        # the file can link its sub-recipes.
        checks = {"time": time_str, "instructions": instructions, "ingredients_raw": ingredients_raw}
        new_condiment_mds = run_intake_checks(title, checks, incoming_url or "", source_name)
        time_str = checks["time"]

        _create_recipe_md(recipe_data, incoming_url or "", source_name,
                          condiments=condiment_links(checks))

        from candidate_scoring import parse_minutes
        from fill_menu_ideas import _infer_cooking_method

        # Scenario B updates the idea's entry in place so fields set at its
        # intake (source_url, title_en, notes, ...) survive activation.
        entry = recipes.get(canonical_name) if isinstance(recipes.get(canonical_name), dict) else {}
        entry.update({
            "title": canonical_name,
            "filename": f"{filename}.md",
            "source": source_name,
            "cuisine": recipe_data.get("cuisine", "") or entry.get("cuisine", ""),
            "meal_type": "Weekend" if parse_minutes(time_str) > 60 and parse_minutes(time_str) != 999 else "Weeknight",
            "health": health,
            "time": time_str,
            "servings": recipe_data.get("servings", "") or entry.get("servings", ""),
            "status": "active",
            "cooking_method": entry.get("cooking_method") or _infer_cooking_method(title, instructions),
            "ingredients_raw": ingredients_raw,
            "instructions": instructions,
            "ingredients": [dict(i) for i in parsed_ingredients],
        })
        entry.setdefault("times_cooked", 0)
        entry.setdefault("last_cooked_date", None)
        entry.setdefault("feedback", [])
        if incoming_url:
            entry.setdefault("source_url", incoming_url)
        for k in ("advance_prep_hours", "condiment_deps", "intake_warnings"):
            if checks.get(k):
                entry[k] = checks[k]
        recipes[canonical_name] = entry
        data["recipes"] = recipes
        _save_metadata(data)
        # github_sync LaunchAgent pushes as davidallison
        request_publish([f"{filename}.md"], new_condiment_mds)

    # --- Record the decline so the outgoing recipe gets a cooldown before it's
    # suggested again (see DECLINE_COOLDOWN_WEEKS in candidate_scoring.py) ---
    outgoing_match = _find_recipe(outgoing_recipe, recipes)
    if outgoing_match:
        _, outgoing_entry = outgoing_match
        outgoing_entry["last_declined_date"] = target.isoformat()
        data["recipes"] = recipes
        _save_metadata(data)

    # --- Update files ---
    _update_meal_plan_json(plan_path, day, outgoing_recipe, canonical_name, time_str, health, recipe_url)
    _recompute_balance(plan_path)
    _update_reminder_line(plan_path, day, canonical_name, time_str)
    _refresh_day_before_notes(plan_path, recipes)
    _rebuild_shopping_csv(plan_path, csv_path, recipes)
    _log_swap_to_feedback(outgoing_recipe, canonical_name, target)

    # --- Re-run apps (opened from davidallison's own GUI session via
    # gui_launch_watcher.py, regardless of which Mac account this process
    # is running as — see gui_launch.py) ---
    request_gui_launch(["WeeklyShoppingList.app", "WeeklyMealCalendar.app"])

    return {
        "success": True,
        "message": f"Swapped {outgoing_recipe} → {canonical_name} on {day}.",
        "incoming_recipe": canonical_name,
        "url": recipe_url,
    }


def reorder_days(day_a: str, day_b: str) -> dict:
    """Switch the dinners on two planned days, e.g. "Tue 10/27", "Wed 10/28" when a marinade
    didn't get started. Both meals are still cooked, so unlike execute_swap there's
    no decline cooldown and no "skipped" feedback. Two execute_swap calls did all
    of that, and lost shopping rows besides (Oct 27 2026)."""
    target_a, target_b = _parse_day_to_date(day_a), _parse_day_to_date(day_b)
    if not target_a or not target_b:
        return {"success": False, "message": f"Could not parse days: {day_a!r}, {day_b!r}"}
    plan_path, csv_path = _find_week_files(target_a)
    if not plan_path or _find_week_files(target_b)[0] != plan_path:
        return {"success": False, "message": f"{day_a} and {day_b} aren't in the same planned week"}

    data = json.loads(plan_path.read_text())
    by_day = {m.get("day"): m for m in data.get("meals", [])}
    a, b = by_day.get(day_a.split()[0]), by_day.get(day_b.split()[0])
    if not a or not b or a is b:
        return {"success": False, "message": f"Need two different planned days, got {day_a!r}, {day_b!r}"}
    for k in ("title", "health", "time", "url", "urls"):
        a[k], b[k] = b.get(k), a.get(k)
    for m in (a, b):
        m["reminder"] = (f"Start {m['title']} around {_suggest_start_time(m.get('time', ''))} "
                         f"for a 6:30 PM dinner.")
    plan_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    _chmod_shared(plan_path)

    recipes = _load_metadata().get("recipes", {})
    _refresh_day_before_notes(plan_path, recipes)
    if csv_path:
        _rebuild_shopping_csv(plan_path, csv_path, recipes)  # day-before dates move with the meals
    # Calendar only -- the Grocery list holds items David is deliberately
    # leaving for later in the week, and nothing on it changes but dates.
    request_gui_launch(["WeeklyMealCalendar.app"])
    return {
        "success": True,
        "message": f"Switched: {a['day']} is now {a['title']}, {b['day']} is now {b['title']}.",
        "days": {a["day"]: a["title"], b["day"]: b["title"]},
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
