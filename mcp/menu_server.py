#!/usr/bin/env python3
"""
MenuBuilder MCP Server — weekly menu workflow tools.

Transport: stdio
Run via:  /Users/davidallison/projects/personal/MenuBuilder/.venv/bin/python3.12 mcp/menu_server.py

Tools:
  get_workflow_state      — current activity state
  start_menu_workflow     — initialize workflow, drain feedback, load last week
  log_meal_feedback       — record last-week ratings; "done" advances state
  get_meal_suggestions    — run candidate filter, auto-select 7 meals
  swap_meal               — replace one day's meal
  approve_menu            — send selected meals to Ashley via Keanu
"""

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# Put MenuBuilder root on the path so local modules are importable
MENUBUILDER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MENUBUILDER_DIR))

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CONFIG = json.loads((MENUBUILDER_DIR / "config.json").read_text())
METADATA_PATH = Path(_CONFIG["metadata_path"])
WEEKLYPLAN_DIR = METADATA_PATH.parent / "weeklyplan"
FEEDBACK_CURRENT_FILE = WEEKLYPLAN_DIR / "feedback_current.json"
FEEDBACK_QUEUE_FILE = Path("/Users/Shared/cooking/feedback_queue.json")

ACTIVITY_FILE = MENUBUILDER_DIR / "menu_activity.json"

DAYS_ORDER = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
ADULT_NAMES = set(n.lower() for n in _CONFIG.get("adult_names", []))

DAY_NAME_MAP = {
    "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu",
    "friday": "Fri", "saturday": "Sat", "sunday": "Sun",
    "mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu",
    "fri": "Fri", "sat": "Sat", "sun": "Sun",
}

# ---------------------------------------------------------------------------
# Activity state I/O
# ---------------------------------------------------------------------------

def _load_activity() -> dict:
    if ACTIVITY_FILE.exists():
        try:
            return json.loads(ACTIVITY_FILE.read_text())
        except Exception:
            pass
    return {"state": "idle"}


def _save_activity(activity: dict) -> None:
    ACTIVITY_FILE.write_text(json.dumps(activity, indent=2))


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def _load_metadata() -> dict:
    """Return the 'recipes' dict from recipe_metadata.json."""
    return json.loads(METADATA_PATH.read_text()).get("recipes", {})


def _save_metadata(recipes: dict) -> None:
    raw = json.loads(METADATA_PATH.read_text()) if METADATA_PATH.exists() else {}
    raw["recipes"] = recipes
    raw["last_updated"] = date.today().isoformat()
    METADATA_PATH.write_text(json.dumps(raw, indent=2))


def _find_recipe_key(name: str, recipes: dict) -> Optional[str]:
    """Fuzzy match recipe name → canonical key."""
    name_lower = name.lower()
    for key in recipes:
        if key.lower() == name_lower:
            return key
    for key in recipes:
        words = [w for w in key.lower().split() if len(w) > 3]
        if len(words) >= 2 and sum(1 for w in words if w in name_lower) >= 2:
            return key
    return None


# ---------------------------------------------------------------------------
# Meal plan parsing
# ---------------------------------------------------------------------------

_PLAN_LINE_RE = re.compile(
    r"^(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+\d+/\d+\s+(.+?)\s*(?:\[|$)"
)


def _parse_last_plan() -> list:
    """Return [{name, day, sms_feedback: null}] from the most recent mealplan file."""
    if not WEEKLYPLAN_DIR.exists():
        return []
    today = date.today()
    dated = []
    for f in WEEKLYPLAN_DIR.glob("mealplan_*.txt"):
        try:
            d = date.fromisoformat(f.stem.replace("mealplan_", ""))
            dated.append((d, f))
        except ValueError:
            continue
    dated.sort(key=lambda x: x[0], reverse=True)
    plan_file = next((f for d, f in dated if d <= today), None)
    if not plan_file:
        return []
    meals = []
    for line in plan_file.read_text().splitlines():
        m = _PLAN_LINE_RE.match(line.strip())
        if m:
            meals.append({"name": m.group(2).strip(), "day": m.group(1), "sms_feedback": None})
    return meals


def _merge_feedback(meals: list) -> list:
    """Overlay feedback_current.json entries onto matching meals."""
    if not FEEDBACK_CURRENT_FILE.exists():
        return meals
    try:
        data = json.loads(FEEDBACK_CURRENT_FILE.read_text())
        for entry in data.get("entries", []):
            recipe = entry.get("recipe", "")
            note = entry.get("note", "")
            sentiment = entry.get("sentiment", "")
            if not recipe:
                continue
            for meal in meals:
                if recipe.lower() in meal["name"].lower() or meal["name"].lower() in recipe.lower():
                    if not meal["sms_feedback"]:
                        meal["sms_feedback"] = f"{sentiment}: {note}" if sentiment else note
                    break
    except Exception:
        pass
    return meals


# ---------------------------------------------------------------------------
# Week date helpers
# ---------------------------------------------------------------------------

def _get_week_start() -> date:
    """Return next Monday (or this Monday if today is Monday)."""
    today = date.today()
    days_ahead = (0 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def _day_date_map(week_start: date) -> dict:
    m = {"Sun": week_start - timedelta(days=1)}
    for i, day in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]):
        m[day] = week_start + timedelta(days=i)
    return m


# ---------------------------------------------------------------------------
# Candidate loading and scoring (mirrors suggest_meals.py logic)
# ---------------------------------------------------------------------------

RECENCY_WEEKS = 3
QUICK_THRESHOLD = 35
HIATUS_PROTEINS = ["salmon"]

_PROTEIN_KEYWORDS = [
    ("salmon", "Fish"), ("fish", "Fish"), ("shrimp", "Shrimp"), ("cod", "Fish"),
    ("tilapia", "Fish"), ("pork", "Pork"), ("lamb", "Lamb"), ("beef", "Beef"),
    ("chicken", "Chicken"), ("turkey", "Turkey"), ("tofu", "Vegetarian"),
    ("chickpea", "Vegetarian"), ("mushroom", "Vegetarian"), ("lentil", "Vegetarian"),
    ("bean", "Vegetarian"), ("vegetarian", "Vegetarian"),
    ("pasta", "Pasta"), ("spaghetti", "Pasta"), ("noodle", "Pasta"),
]


def _get_protein(name: str) -> str:
    lower = name.lower()
    for keyword, label in _PROTEIN_KEYWORDS:
        if keyword in lower:
            return label
    return "Other"


def _weeks_since(date_str: Optional[str]) -> float:
    if not date_str:
        return 999.0
    try:
        cooked = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (date.today() - cooked).days / 7
    except Exception:
        return 999.0


def _parse_minutes(time_str: str) -> int:
    if not time_str:
        return 999
    mins = 0
    h = re.search(r"(\d+)\s*hour", time_str, re.IGNORECASE)
    if h:
        mins += int(h.group(1)) * 60
    m = re.search(r"(\d+)\s*min", time_str, re.IGNORECASE)
    if m:
        mins += int(m.group(1))
    return mins if mins > 0 else 999


def _candidate_score(c: dict) -> float:
    s = 0.0
    age_weeks = c.get("age_weeks", 0)
    s -= min(age_weeks, 52) * 2
    health = c.get("health", "Moderate")
    if health == "Heart-Healthy":
        s -= 5
    elif health == "Indulgent":
        s += 10
    s += c.get("times_cooked", 0) * 2
    if c.get("is_grill") and c.get("is_grill_season"):
        s -= 4
    adult_score = c.get("adult_score")
    if adult_score is not None:
        if adult_score < 0.5:
            s += 15
        elif adult_score >= 0.9:
            s -= 6
    if c.get("kid_friendly"):
        s -= 3
    return s


def _load_candidates(quick_days: list) -> list:
    """Return sorted candidate list from recipe_metadata.json."""
    recipes = _load_metadata()
    today = date.today()
    month = today.month
    is_grill_season = 4 <= month <= 9

    candidates = []
    for name, r in recipes.items():
        if r.get("status") != "active":
            continue
        if any(h in name.lower() for h in HIATUS_PROTEINS):
            continue

        age_weeks = _weeks_since(r.get("last_cooked_date"))
        if age_weeks < RECENCY_WEEKS:
            continue

        minutes = _parse_minutes(r.get("time", ""))
        is_slow = r.get("cooking_method") == "slow_cooker"
        is_quick = minutes <= QUICK_THRESHOLD or is_slow
        is_grill = r.get("cooking_method") == "grill"
        protein = _get_protein(name)
        health = r.get("health", "Moderate")
        cuisine = r.get("cuisine", "Unknown")
        times_cooked = r.get("times_cooked", 0)
        meal_type = r.get("meal_type", "Weeknight")
        feedback = r.get("feedback", [])

        adults = [f for f in feedback if f.get("person", "").lower() in ADULT_NAMES]
        adult_score = None
        if adults:
            liked = sum(1 for f in adults if f.get("sentiment") == "liked")
            adult_score = liked / len(adults)

        kids = [f for f in feedback if f.get("person", "").lower() not in ADULT_NAMES]
        kid_friendly = None
        if kids:
            liked = sum(1 for f in kids if f.get("sentiment") == "liked")
            kid_friendly = liked > len(kids) / 2

        c = {
            "name": name,
            "cuisine": cuisine,
            "health": health,
            "protein": protein,
            "minutes": minutes,
            "time_str": r.get("time", ""),
            "is_quick": is_quick,
            "is_grill": is_grill,
            "is_grill_season": is_grill_season,
            "times_cooked": times_cooked,
            "last_cooked": r.get("last_cooked_date") or "never",
            "age_weeks": age_weeks,
            "meal_type": meal_type,
            "method": r.get("cooking_method", ""),
            "adult_score": adult_score,
            "kid_friendly": kid_friendly,
        }
        c["score"] = _candidate_score(c)
        candidates.append(c)

    return sorted(candidates, key=lambda c: c["score"])


def _select_meals(candidates: list, quick_days: list, cuisine_direction: Optional[str]) -> dict:
    """Select up to 7 meals for Sun–Sat, maintaining protein diversity and health balance."""
    pool = list(candidates)

    # Bump cuisine-matching recipes to front
    if cuisine_direction and cuisine_direction.lower() not in ("what we've got", ""):
        c_lower = cuisine_direction.lower()
        pool.sort(key=lambda c: (0 if c_lower in c.get("cuisine", "").lower() else 1, c["score"]))

    # Include idea recipes matching cuisine direction
    recipes = _load_metadata()
    if cuisine_direction and cuisine_direction.lower() not in ("what we've got", ""):
        c_lower = cuisine_direction.lower()
        for name, meta in recipes.items():
            if (
                meta.get("status") == "idea"
                and c_lower in meta.get("cuisine", "").lower()
                and not any(c["name"] == name for c in pool)
            ):
                pool.insert(0, {
                    "name": name,
                    "cuisine": meta.get("cuisine", ""),
                    "health": meta.get("health", "Moderate"),
                    "protein": _get_protein(name),
                    "minutes": 30,
                    "time_str": meta.get("time", ""),
                    "is_quick": True,
                    "meal_type": "Weeknight",
                    "score": -100,
                })

    quick_set = {d.lower() for d in quick_days}
    selected: dict = {}
    used_proteins: set = set()

    def _pick(days_subset, require_quick=False, require_weekend=False):
        for day in days_subset:
            if day in selected:
                continue
            for c in pool:
                if c["name"] in selected.values():
                    continue
                if require_quick and not c["is_quick"]:
                    continue
                if require_weekend and c.get("meal_type") != "Weekend":
                    continue
                protein = c["protein"]
                if protein in used_proteins and len(pool) > len(days_subset) * 2:
                    continue
                selected[day] = c["name"]
                used_proteins.add(protein)
                break

    _pick(["Sat", "Sun"], require_weekend=True)
    quick_abbrevs = [a for a in DAYS_ORDER if a.lower() in quick_set or a[:3].lower() in quick_set]
    _pick(quick_abbrevs, require_quick=True)
    _pick(DAYS_ORDER)

    # Fill any remaining gaps without constraints
    leftovers = [c for c in pool if c["name"] not in selected.values()]
    for day in DAYS_ORDER:
        if day not in selected and leftovers:
            selected[day] = leftovers.pop(0)["name"]

    return selected


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("menubuilder")


@mcp.tool()
def get_workflow_state() -> dict:
    """
    Return the current menu workflow activity state.

    Returns the full activity dict, or {"state": "idle"} if no active workflow.

    Fields:
      state: idle | awaiting_meal_logging | awaiting_suggestions |
             awaiting_meal_approval | awaiting_ashley_signoff | complete
      week_start: YYYY-MM-DD of the upcoming Monday
      last_week_meals: [{name, day, sms_feedback}]
      selected_meals: {day_abbrev: recipe_name} (populated after get_meal_suggestions)
      cuisine_direction: cuisine preference set during get_meal_suggestions
      quick_days: days flagged as needing quick meals
    """
    return _load_activity()


@mcp.tool()
def start_menu_workflow() -> dict:
    """
    Initialize a new weekly menu build workflow.

    Steps performed:
    1. Drain the SMS feedback queue (process_feedback_queue.py)
    2. Parse the most recent mealplan_*.txt
    3. Merge any feedback from feedback_current.json onto last week's meals
    4. Write a fresh activity state (menu_activity.json)

    Returns the new activity state. last_week_meals will show each meal
    with any pre-existing SMS feedback merged in.

    State after call: awaiting_meal_logging
    """
    # Drain SMS feedback queue
    cmd = [sys.executable, str(MENUBUILDER_DIR / "process_feedback_queue.py")]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception:
        pass  # Non-fatal — queue drain failure shouldn't block workflow

    meals = _merge_feedback(_parse_last_plan())
    week_start = _get_week_start()

    activity = {
        "state": "awaiting_meal_logging",
        "initiated_at": datetime.now().isoformat(),
        "week_start": week_start.isoformat(),
        "last_week_meals": meals,
        "schedule_notes": [],
        "cuisine_direction": None,
        "selected_meals": {},
        "ideas_on_menu": [],
        "quick_days": [],
    }
    _save_activity(activity)
    return activity


@mcp.tool()
def log_meal_feedback(feedback: str) -> dict:
    """
    Record feedback for last week's meals.

    Call repeatedly with feedback text per meal, then call once with "done"
    to finalize. Feedback is matched to the closest meal by keyword.

    When feedback == "done":
    - Updates recipe_metadata.json: increments times_cooked, sets last_cooked_date
    - Clears feedback_current.json
    - Advances state to awaiting_suggestions

    Args:
        feedback: Natural language feedback string (e.g. "Monday pasta was great,
                  Thursday chicken needed more seasoning") or "done" to finalize.

    Returns the updated activity state.
    """
    activity = _load_activity()
    if activity.get("state") == "idle":
        return {"error": "No active workflow. Call start_menu_workflow first."}

    meals = activity.get("last_week_meals", [])

    if feedback.strip().lower() == "done":
        # Persist cooked-meal updates to metadata
        recipes = _load_metadata()
        today_str = date.today().isoformat()
        for meal in meals:
            key = _find_recipe_key(meal["name"], recipes)
            if key:
                recipes[key]["times_cooked"] = recipes[key].get("times_cooked", 0) + 1
                recipes[key]["last_cooked_date"] = today_str
        _save_metadata(recipes)

        # Clear feedback_current.json
        if FEEDBACK_CURRENT_FILE.exists():
            FEEDBACK_CURRENT_FILE.write_text(json.dumps({"entries": []}, indent=2))
            try:
                FEEDBACK_CURRENT_FILE.chmod(0o666)
            except Exception:
                pass

        activity["state"] = "awaiting_suggestions"
        _save_activity(activity)
        return activity

    # Match feedback text to the most relevant meal
    lowered = feedback.lower()
    matched = False
    for meal in meals:
        words = [w for w in meal["name"].lower().split() if len(w) > 3]
        if words and any(w in lowered for w in words):
            existing = meal.get("sms_feedback") or ""
            meal["sms_feedback"] = (existing + " " + feedback).strip()
            matched = True
            break

    if not matched and meals:
        # Append to last meal as fallback
        existing = meals[-1].get("sms_feedback") or ""
        meals[-1]["sms_feedback"] = (existing + " " + feedback).strip()

    activity["last_week_meals"] = meals
    _save_activity(activity)
    return activity


@mcp.tool()
def get_meal_suggestions(cuisine_direction: str = "", constraints: str = "") -> dict:
    """
    Run the candidate filter and auto-select a proposed weekly meal plan.

    Args:
        cuisine_direction: Cuisine preference (e.g. "Mediterranean", "Asian",
                           "Mexican", "Indian"). Empty string = no preference.
        constraints: Free-text schedule notes (e.g. "soccer practice Monday and
                     Wednesday, birthday dinner Friday"). Days mentioned alongside
                     activity keywords become quick-meal slots.

    Returns:
        {
          "candidates": top-20 eligible recipes with name/cuisine/health/minutes,
          "selected_meals": {day_abbrev: recipe_name} auto-selected for the week,
          "quick_days": days identified as needing quick meals,
          "week_start": "YYYY-MM-DD"
        }

    The selected_meals are stored in the activity state. Use swap_meal() to
    replace individual days before calling approve_menu().

    State after call: awaiting_meal_approval
    """
    activity = _load_activity()
    if activity.get("state") == "idle":
        return {"error": "No active workflow. Call start_menu_workflow first."}

    if cuisine_direction:
        activity["cuisine_direction"] = cuisine_direction
    if constraints:
        notes = activity.get("schedule_notes", [])
        notes.append(constraints)
        activity["schedule_notes"] = notes

    # Derive quick days from constraints
    quick_days = list(activity.get("quick_days", []))
    if constraints:
        c_lower = constraints.lower()
        quick_signals = ("game", "practice", "busy", "quick", "early", "tournament")
        if any(s in c_lower for s in quick_signals):
            for day_name, abbrev in DAY_NAME_MAP.items():
                if day_name in c_lower and abbrev not in quick_days:
                    quick_days.append(abbrev)
    activity["quick_days"] = quick_days

    # Load and score candidates
    candidates = _load_candidates(quick_days)

    # Auto-select 7 meals
    selected = _select_meals(candidates, quick_days, cuisine_direction or activity.get("cuisine_direction"))
    activity["selected_meals"] = selected
    activity["state"] = "awaiting_meal_approval"
    _save_activity(activity)

    # Return a clean summary (top 20 candidates)
    clean_candidates = [
        {
            "name": c["name"],
            "cuisine": c["cuisine"],
            "health": c["health"],
            "minutes": c["minutes"],
            "time_str": c["time_str"],
            "meal_type": c["meal_type"],
            "is_quick": c["is_quick"],
            "last_cooked": c["last_cooked"],
        }
        for c in candidates[:20]
    ]

    return {
        "candidates": clean_candidates,
        "selected_meals": selected,
        "quick_days": quick_days,
        "week_start": activity["week_start"],
    }


@mcp.tool()
def swap_meal(day: str, reason: str, replacement: str = "") -> dict:
    """
    Replace the planned meal for a given day.

    Args:
        day: Day abbreviation (Mon, Tue, Wed, Thu, Fri, Sat, Sun).
        reason: Natural language reason (e.g. "we've had too much chicken").
                Used when picking an auto-replacement.
        replacement: Optional explicit recipe name. If provided, used directly.
                     If empty, the tool picks the best eligible candidate.

    Auto-pick logic (when replacement is empty):
    - Excludes recipes already in selected_meals
    - Applies recency filter (not cooked in last 3 weeks)
    - Prefers cuisine_direction match if set
    - Prefers same meal_type (weekend vs weeknight) as the displaced day
    - Picks the top scorer from remaining eligible candidates

    Returns:
        {
          "selected_meals": updated {day: recipe} dict,
          "swapped_day": "Thu",
          "new_recipe": "New Recipe Name",
          "outgoing_recipe": "Old Recipe Name"
        }

    State: stays awaiting_meal_approval
    """
    activity = _load_activity()
    if activity.get("state") == "idle":
        return {"error": "No active workflow. Call start_menu_workflow first."}

    selected = dict(activity.get("selected_meals", {}))
    outgoing = selected.get(day)

    if not replacement:
        currently_selected = set(selected.values()) - ({outgoing} if outgoing else set())
        candidates = _load_candidates(activity.get("quick_days", []))
        eligible = [c for c in candidates if c["name"] not in currently_selected]

        if not eligible:
            return {"error": f"No eligible replacement candidates found for {day}."}

        # Prefer cuisine direction match
        cuisine_dir = activity.get("cuisine_direction", "")
        if cuisine_dir and cuisine_dir.lower() not in ("what we've got", ""):
            c_lower = cuisine_dir.lower()
            cuisine_match = [c for c in eligible if c_lower in c.get("cuisine", "").lower()]
            if cuisine_match:
                eligible = cuisine_match + [c for c in eligible if c not in cuisine_match]

        # Prefer same meal_type as the outgoing recipe
        if outgoing:
            recipes = _load_metadata()
            key = _find_recipe_key(outgoing, recipes)
            if key:
                target_type = recipes[key].get("meal_type", "Weeknight")
                same_type = [c for c in eligible if c.get("meal_type") == target_type]
                if same_type:
                    eligible = same_type

        replacement = eligible[0]["name"]

    selected[day] = replacement
    activity["selected_meals"] = selected
    _save_activity(activity)

    return {
        "selected_meals": selected,
        "swapped_day": day,
        "new_recipe": replacement,
        "outgoing_recipe": outgoing,
    }


@mcp.tool()
def approve_menu() -> dict:
    """
    Send the currently selected meals to Ashley for approval via Keanu.

    Calls send_menu_partner.py with the selected_meals from the active workflow.
    Writes to Keanu's outbox (.outbox.json) and creates menu_feedback_pending.json.

    Returns:
        {
          "state": "awaiting_ashley_signoff",
          "message": "Menu sent to Ashley via Keanu.",
          "meals_sent": [{"day": "Mon 5/26", "recipe": "..."}]
        }

    State after call: awaiting_ashley_signoff
    """
    activity = _load_activity()
    if activity.get("state") == "idle":
        return {"error": "No active workflow. Call start_menu_workflow first."}

    selected = activity.get("selected_meals", {})
    if not selected:
        return {"error": "No meals selected. Call get_meal_suggestions first."}

    week_start = date.fromisoformat(activity["week_start"])
    day_to_date = _day_date_map(week_start)

    meals_json = []
    for day in DAYS_ORDER:
        if day in selected:
            dt = day_to_date.get(day)
            date_str = dt.strftime("%-m/%-d") if dt else ""
            meals_json.append({"day": f"{day} {date_str}", "recipe": selected[day]})

    cmd = [
        sys.executable, str(MENUBUILDER_DIR / "send_menu_partner.py"),
        "--meals", json.dumps(meals_json),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return {"error": f"send_menu_partner.py failed: {result.stderr.strip()}"}
    except Exception as e:
        return {"error": f"Could not run send_menu_partner.py: {e}"}

    activity["state"] = "awaiting_ashley_signoff"
    _save_activity(activity)

    return {
        "state": "awaiting_ashley_signoff",
        "message": "Menu sent to Ashley via Keanu. Waiting for her reply.",
        "meals_sent": meals_json,
    }


if __name__ == "__main__":
    mcp.run()
