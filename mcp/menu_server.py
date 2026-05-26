#!/usr/bin/env python3
"""
MenuBuilder MCP Server — weekly menu workflow tools.

Transport: stdio
Run via:  /Users/davidallison/projects/personal/MenuBuilder/.venv/bin/python3.12 mcp/menu_server.py

Tools:
  get_workflow_state       — current activity state
  start_menu_workflow      — initialize workflow, drain feedback, load last week
  log_meal_feedback        — record last-week ratings; "done" advances state
  get_meal_suggestions     — run candidate filter, auto-select 7 meals
  swap_meal                — replace one day's meal (pre-signoff)
  approve_menu             — send selected meals to Ashley via Keanu
  handle_ashley_reply      — process Ashley's approval or swap request
  activate_idea_recipe     — activate a pending idea from provided markdown content
  finalize_plan            — generate plan + shopping CSV, launch apps, send prep guide
"""

import csv
import io
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup

MENUBUILDER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MENUBUILDER_DIR))

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config and paths
# ---------------------------------------------------------------------------

_CONFIG = json.loads((MENUBUILDER_DIR / "config.json").read_text())
METADATA_PATH = Path(_CONFIG["metadata_path"])
WEEKLYPLAN_DIR = METADATA_PATH.parent / "weeklyplan"
RECIPES_DIR = METADATA_PATH.parent / "recipes"
FEEDBACK_CURRENT_FILE = WEEKLYPLAN_DIR / "feedback_current.json"

ACTIVITY_FILE = MENUBUILDER_DIR / "menu_activity.json"
OUTBOX_FILE = Path("/Users/Shared/sms-assistant/.outbox.json")
PENDING_FILE = Path("/Users/Shared/sms-assistant/menu_feedback_pending.json")

PARTNER_HANDLE = _CONFIG.get("partner_handle", "")          # Ashley
ADMIN_HANDLE = _CONFIG.get("admin_handle", "")              # David (optional in config)
GITHUB_BASE_URL = _CONFIG.get("github_pages_base_url", "")
DROPBOX_BASE_URL = _CONFIG.get("dropbox_recipe_base_url", "")

DAYS_ORDER = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
ADULT_NAMES = set(n.lower() for n in _CONFIG.get("adult_names", []))

DAY_NAME_MAP = {
    "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu",
    "friday": "Fri", "saturday": "Sat", "sunday": "Sun",
    "mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu",
    "fri": "Fri", "sat": "Sat", "sun": "Sun",
}

APPROVAL_PHRASES = (
    "looks good", "good", "ok", "okay", "approved", "go ahead", "perfect",
    "great", "sounds good", "yes", "yep", "yeah", "love it", "fine", "sure", "\U0001f44d"
)

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
    return json.loads(METADATA_PATH.read_text()).get("recipes", {})


def _save_metadata(recipes: dict) -> None:
    raw = json.loads(METADATA_PATH.read_text()) if METADATA_PATH.exists() else {}
    raw["recipes"] = recipes
    raw["last_updated"] = date.today().isoformat()
    METADATA_PATH.write_text(json.dumps(raw, indent=2))


def _find_recipe_key(name: str, recipes: dict) -> Optional[str]:
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
# Candidate loading and scoring  (mirrors suggest_meals.py)
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
    recipes = _load_metadata()
    today = date.today()
    is_grill_season = 4 <= today.month <= 9

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
            "cuisine": r.get("cuisine", "Unknown"),
            "health": r.get("health", "Moderate"),
            "protein": protein,
            "minutes": minutes,
            "time_str": r.get("time", ""),
            "is_quick": is_quick,
            "is_grill": is_grill,
            "is_grill_season": is_grill_season,
            "times_cooked": r.get("times_cooked", 0),
            "last_cooked": r.get("last_cooked_date") or "never",
            "age_weeks": age_weeks,
            "meal_type": r.get("meal_type", "Weeknight"),
            "method": r.get("cooking_method", ""),
            "adult_score": adult_score,
            "kid_friendly": kid_friendly,
        }
        c["score"] = _candidate_score(c)
        candidates.append(c)

    return sorted(candidates, key=lambda c: c["score"])


def _select_meals(candidates: list, quick_days: list, cuisine_direction: Optional[str]) -> dict:
    pool = list(candidates)

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
    leftovers = [c for c in pool if c["name"] not in selected.values()]
    for day in DAYS_ORDER:
        if day not in selected and leftovers:
            selected[day] = leftovers.pop(0)["name"]

    return selected


# ---------------------------------------------------------------------------
# Swap helpers  (structured parse + Claude fallback, mirrors menu_workflow.py)
# ---------------------------------------------------------------------------

def _parse_swap(text: str, selected: dict, week_start: date) -> Optional[dict]:
    """Parse structured swap: 'swap 3 to pasta', 'change tuesday to tacos'."""
    lowered = text.lower()
    ordered = [(day, selected[day]) for day in DAYS_ORDER if day in selected]

    # Numbered swap: "swap 3 to X"
    num_m = re.search(r'(?:swap|change|replace)\s+(\d+)\s+(?:to|with|for)\s+(.+)', lowered)
    if num_m:
        idx = int(num_m.group(1)) - 1
        new_name = num_m.group(2).strip().rstrip(".").title()
        if 0 <= idx < len(ordered):
            day = ordered[idx][0]
            new_selected = dict(selected)
            new_selected[day] = new_name
            return new_selected

    # Day-named swap: "change tuesday to X"
    for day_name, abbrev in DAY_NAME_MAP.items():
        if day_name in lowered and abbrev in selected:
            day_m = re.search(rf'{re.escape(day_name)}\s+(?:to|with|for)\s+(.+)', lowered)
            if day_m:
                new_name = day_m.group(1).strip().rstrip(".").title()
                new_selected = dict(selected)
                new_selected[abbrev] = new_name
                return new_selected

    return None


def _claude_swap(text: str, selected: dict, cuisine_direction: Optional[str]) -> Optional[dict]:
    """Use Claude Haiku to interpret natural language swap requests."""
    import anthropic as _anthropic

    ordered = [(day, selected[day]) for day in DAYS_ORDER if day in selected]
    meal_list = "\n".join(f"{i + 1}. {day}: {name}" for i, (day, name) in enumerate(ordered))
    already_selected = set(selected.values())
    direction_lower = (cuisine_direction or "").lower()

    recipes = _load_metadata()
    candidates = [
        (name, meta)
        for name, meta in recipes.items()
        if meta.get("status") == "active" and name not in already_selected
    ]

    def _score(item):
        name, meta = item
        times = meta.get("times_cooked", 0)
        cuisine_bonus = -1 if direction_lower and direction_lower in meta.get("cuisine", "").lower() else 0
        return (times, cuisine_bonus)

    candidates.sort(key=_score)
    candidate_names = [name for name, _ in candidates[:60]]

    try:
        client = _anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    "A user is reviewing a weekly dinner plan and giving natural language feedback.\n\n"
                    f"Current plan:\n{meal_list}\n\n"
                    f"User feedback: \"{text}\"\n\n"
                    f"Available replacements (in preference order): {json.dumps(candidate_names)}\n\n"
                    "Identify which meals to replace (by day abbreviation Mon/Tue/Wed/Thu/Fri/Sat/Sun) "
                    "and choose the best replacement from the available list. "
                    "Return JSON array only, no explanation:\n"
                    '[{"day": "Mon", "to": "Replacement Meal Name"}, ...]\n\n'
                    "If the feedback doesn't clearly request any changes, return []."
                ),
            }],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        swaps = json.loads(raw)
        if not isinstance(swaps, list) or not swaps:
            return None
        new_selected = dict(selected)
        changed = False
        for swap in swaps:
            day = (swap.get("day") or "").strip()
            to_meal = (swap.get("to") or "").strip()
            if day and to_meal and day in new_selected:
                new_selected[day] = to_meal
                changed = True
        return new_selected if changed else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Recipe URL helpers
# ---------------------------------------------------------------------------

def _recipe_url(recipe_name: str, metadata_entry: dict) -> str:
    """Return the best URL for a recipe — GitHub Pages if .md exists, else Dropbox."""
    filename = metadata_entry.get("filename", "")
    if filename and filename.endswith(".md"):
        stem = Path(filename).stem
        if GITHUB_BASE_URL:
            return f"{GITHUB_BASE_URL}/{stem}"
    if DROPBOX_BASE_URL and filename:
        return f"{DROPBOX_BASE_URL}&preview={filename}"
    return ""


# ---------------------------------------------------------------------------
# Idea recipe: fetch + activate
# ---------------------------------------------------------------------------

_FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


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


def _fetch_recipe_data(url: str) -> Optional[dict]:
    """Fetch ld+json Recipe schema from URL. Returns structured dict or None."""
    try:
        resp = httpx.get(url, headers=_FETCH_HEADERS, timeout=20, follow_redirects=True)
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


def _write_recipe_md(title: str, recipe_data: dict, source_url: str, source_name: str) -> Path:
    """Write a recipe .md file to RECIPES_DIR. Returns the path."""
    filename = title.replace(" ", "_") + ".md"
    path = RECIPES_DIR / filename
    lines = [
        f"# {title}", "",
        f"**Time**: {recipe_data.get('time', '')}  ",
        f"**Yield**: {recipe_data.get('servings', '')}  ",
        f"**Adapted from**: [{source_name or source_url}]({source_url})" if source_url else "",
        "", "## Ingredients", "",
    ]
    for ing in recipe_data.get("ingredients", []):
        lines.append(f"- {ing}")
    lines += ["", "## Instructions", ""]
    for i, step in enumerate(recipe_data.get("instructions", []), 1):
        lines.append(f"{i}. {step}")
    lines.append("")
    path.write_text("\n".join(l for l in lines if l is not None), encoding="utf-8")
    return path


def _activate_idea_in_metadata(name: str, filename: str, recipe_data: dict,
                                source_url: str) -> bool:
    """Set recipe status to 'active' in metadata. Returns True on success."""
    recipes = _load_metadata()
    key = _find_recipe_key(name, recipes)
    if not key:
        return False
    recipes[key].update({
        "status": "active",
        "filename": filename,
        "time": recipe_data.get("time", recipes[key].get("time", "")),
        "cuisine": recipe_data.get("cuisine", recipes[key].get("cuisine", "")),
    })
    _save_metadata(recipes)
    return True


def _try_auto_activate(name: str, recipes: dict) -> bool:
    """
    Attempt to fetch and activate an idea recipe from its source_url.
    Returns True if successful, False if fetch failed or no URL.
    """
    key = _find_recipe_key(name, recipes)
    if not key:
        return False
    entry = recipes[key]
    source_url = entry.get("source_url", "")
    if not source_url:
        return False

    recipe_data = _fetch_recipe_data(source_url)
    if not recipe_data:
        return False

    title = recipe_data.get("title") or name
    source_name = entry.get("source", source_url)
    md_path = _write_recipe_md(title, recipe_data, source_url, source_name)
    return _activate_idea_in_metadata(name, md_path.name, recipe_data, source_url)


# ---------------------------------------------------------------------------
# Plan text generation
# ---------------------------------------------------------------------------

def _build_plan_text(selected: dict, week_start: date, schedule_notes: list) -> str:
    """Generate mealplan_YYYY-MM-DD.txt content. Calls Claude for REMINDERS."""
    import anthropic as _anthropic

    recipes = _load_metadata()
    day_to_date = _day_date_map(week_start)
    ordered = [(day, selected[day]) for day in DAYS_ORDER if day in selected]

    meals_info = []
    for day, name in ordered:
        key = _find_recipe_key(name, recipes)
        meta = recipes.get(key, {}) if key else {}
        health = meta.get("health", "Moderate")
        time_str = meta.get("time", "?")
        filename = meta.get("filename", "")
        url = _recipe_url(name, meta)
        dt = day_to_date.get(day)
        date_str = dt.strftime("%-m/%-d") if dt else ""
        meals_info.append({
            "day": day, "date": date_str, "name": name,
            "health": health, "time": time_str, "url": url,
        })

    week_end = week_start + timedelta(days=5)
    week_start_display = (week_start - timedelta(days=1)).strftime("%B %d")
    week_end_display = week_end.strftime("%B %d, %Y")

    # DINNERS block
    dinners_lines = []
    for m in meals_info:
        dinners_lines.append(f"{m['day']} {m['date']}  {m['name']} [{m['health']}] | {m['time']}")
        if m["url"]:
            dinners_lines.append(f"          {m['url']}")

    # BALANCE line
    health_counts: dict = {}
    for m in meals_info:
        h = m["health"]
        health_counts[h] = health_counts.get(h, 0) + 1
    balance_line = "BALANCE: " + ", ".join(f"{v} {k}" for k, v in sorted(health_counts.items()))

    # REMINDERS via Claude
    schedule_context = "\n".join(schedule_notes) if schedule_notes else "No special schedule notes."
    meal_lines = "\n".join(
        f"{m['day']} {m['date']}: {m['name']} ({m['health']}, {m['time']})"
        for m in meals_info
    )

    reminder_prompt = (
        f"Generate the REMINDERS section for this weekly meal plan.\n\n"
        f"Week: {week_start_display} - {week_end_display}\n"
        f"Schedule notes: {schedule_context}\n\n"
        f"Meals:\n{meal_lines}\n\n"
        "Format: One line per day that has a meal, like:\n"
        "- MON: one-line timing/prep note\n\n"
        "Rules:\n"
        "- Only include days that have meals\n"
        "- Include suggested start times for weeknight meals over 30 min\n"
        "- Note schedule constraints from the schedule notes\n"
        "- Note special prep (marinating, chilling, slow cooker setup)\n"
        "- One line per day — these populate calendar events\n\n"
        "Return ONLY the reminder lines, no header."
    )

    try:
        client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": reminder_prompt}],
        )
        reminders = response.content[0].text.strip()
    except Exception as e:
        # Fallback: simple reminder per day
        reminders = "\n".join(f"- {m['day'].upper()}: {m['name']}" for m in meals_info)

    return (
        f"WEEKLY MEAL PLAN: {week_start_display} - {week_end_display}\n\n"
        f"========================================\n"
        f"DINNERS\n"
        f"========================================\n\n"
        f"{chr(10).join(dinners_lines)}\n\n"
        f"{balance_line}\n\n"
        f"========================================\n"
        f"REMINDERS\n"
        f"========================================\n"
        f"{reminders}"
    )


# ---------------------------------------------------------------------------
# Shopping CSV generation
# ---------------------------------------------------------------------------

def _build_shopping_csv(selected: dict, week_start: date) -> str:
    """
    Generate shopping CSV content.
    Format: Item, Notes (qty + unit), Date (cook date as YYYY-MM-DD)
    """
    recipes = _load_metadata()
    day_to_date = _day_date_map(week_start)
    rows = []

    for day in DAYS_ORDER:
        if day not in selected:
            continue
        name = selected[day]
        key = _find_recipe_key(name, recipes)
        if not key:
            continue
        cook_date = day_to_date.get(day)
        if not cook_date:
            continue
        date_str = cook_date.isoformat()

        for ing in recipes[key].get("ingredients", []):
            qty = str(ing.get("quantity", "")).strip()
            unit = str(ing.get("unit", "")).strip()
            ing_name = str(ing.get("name", "")).strip()
            if not ing_name:
                continue
            notes = f"{qty} {unit}".strip() if (qty or unit) else ""
            rows.append({"Item": ing_name, "Notes": notes, "Date": date_str})

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["Item", "Notes", "Date"])
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Prep guide
# ---------------------------------------------------------------------------

def _build_prep_guide(selected: dict) -> str:
    recipes = _load_metadata()
    lines = ["PREP GUIDE:"]
    for day in DAYS_ORDER:
        if day not in selected:
            continue
        name = selected[day]
        key = _find_recipe_key(name, recipes)
        if not key:
            continue
        prep = recipes[key].get("prep_components", [])
        notes = recipes[key].get("prep_notes", "")
        if prep:
            lines.append(f"\n{name}:")
            for p in prep:
                lines.append(f"  - {p}")
            if notes:
                lines.append(f"  Note: {notes}")
    return "\n".join(lines) if len(lines) > 1 else ""


# ---------------------------------------------------------------------------
# Keanu outbox
# ---------------------------------------------------------------------------

def _send_outbox(handle: str, text: str) -> None:
    if not handle:
        return
    outbox = json.loads(OUTBOX_FILE.read_text()) if OUTBOX_FILE.exists() else []
    outbox.append({"handle": handle, "text": text})
    OUTBOX_FILE.write_text(json.dumps(outbox))


# ---------------------------------------------------------------------------
# Core finalization logic  (shared by handle_ashley_reply and finalize_plan)
# ---------------------------------------------------------------------------

def _do_finalize(activity: dict) -> dict:
    """
    Generate plan text, shopping CSV, launch apps, send prep guide.
    Mutates and saves activity state to 'complete'.
    Returns the completed activity dict.
    """
    selected = activity.get("selected_meals", {})
    week_start = date.fromisoformat(activity["week_start"])
    schedule_notes = activity.get("schedule_notes", [])

    # Generate plan text
    plan_text = _build_plan_text(selected, week_start, schedule_notes)

    # Write plan file
    plan_path = WEEKLYPLAN_DIR / f"mealplan_{week_start.isoformat()}.txt"
    shopping_path = WEEKLYPLAN_DIR / f"shopping_{week_start.isoformat()}.csv"
    WEEKLYPLAN_DIR.mkdir(exist_ok=True)
    plan_path.write_text(plan_text)
    shopping_path.write_text(_build_shopping_csv(selected, week_start))

    # Launch apps
    subprocess.Popen(["open", "/Applications/WeeklyShoppingList.app"])
    subprocess.Popen(["open", "/Applications/WeeklyMealCalendar.app"])

    # Build and send prep guide
    prep_guide = _build_prep_guide(selected)

    # Summary for notification
    summary_lines = plan_text.split("\n")[:15]
    summary = "\n".join(summary_lines)

    if ADMIN_HANDLE:
        _send_outbox(ADMIN_HANDLE, f"Plan ready!\n\n{summary}")
        if prep_guide:
            _send_outbox(ADMIN_HANDLE, prep_guide)

    if PARTNER_HANDLE:
        if prep_guide:
            _send_outbox(PARTNER_HANDLE, prep_guide)

    # Clear the pending approval file
    if PENDING_FILE.exists():
        PENDING_FILE.unlink()

    activity["state"] = "complete"
    activity["plan_path"] = str(plan_path)
    activity["shopping_path"] = str(shopping_path)
    activity["prep_guide"] = prep_guide
    _save_activity(activity)
    return activity


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("menubuilder")


@mcp.tool()
def get_workflow_state() -> dict:
    """
    Return the current menu workflow activity state.

    Returns the full activity dict, or {"state": "idle"} if no active workflow.

    State values:
      idle                    — no active workflow
      awaiting_meal_logging   — last week meals loaded; record ratings or call done
      awaiting_suggestions    — feedback logged; call get_meal_suggestions
      awaiting_meal_approval  — meals proposed; review + swap or approve_menu
      awaiting_ashley_signoff — sent to Ashley; waiting for her reply
      awaiting_idea_activation — ideas on menu need content before finalizing
      complete                — plan written, apps launched, prep guide sent
    """
    return _load_activity()


@mcp.tool()
def start_menu_workflow() -> dict:
    """
    Initialize a new weekly menu build workflow.

    1. Drain the SMS feedback queue (process_feedback_queue.py)
    2. Parse the most recent mealplan_*.txt for last week's meals
    3. Merge any feedback from feedback_current.json onto those meals
    4. Write fresh activity state (menu_activity.json)

    Returns the new activity state. last_week_meals will show each meal
    with any pre-existing SMS feedback merged in.

    State after call: awaiting_meal_logging
    """
    cmd = [sys.executable, str(MENUBUILDER_DIR / "process_feedback_queue.py")]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception:
        pass

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

    Call with natural language feedback for each meal, then call with "done" to
    finalize. Feedback text is matched to the closest meal by keyword.

    When feedback == "done":
    - Updates recipe_metadata.json: increments times_cooked, sets last_cooked_date
    - Clears feedback_current.json
    - Advances state to awaiting_suggestions

    Args:
        feedback: Natural language feedback (e.g. "Monday was great, Thursday needed
                  more seasoning") or "done" to finalize.

    Returns the updated activity state.
    """
    activity = _load_activity()
    if activity.get("state") == "idle":
        return {"error": "No active workflow. Call start_menu_workflow first."}

    meals = activity.get("last_week_meals", [])

    if feedback.strip().lower() == "done":
        recipes = _load_metadata()
        today_str = date.today().isoformat()
        for meal in meals:
            key = _find_recipe_key(meal["name"], recipes)
            if key:
                recipes[key]["times_cooked"] = recipes[key].get("times_cooked", 0) + 1
                recipes[key]["last_cooked_date"] = today_str
        _save_metadata(recipes)

        if FEEDBACK_CURRENT_FILE.exists():
            FEEDBACK_CURRENT_FILE.write_text(json.dumps({"entries": []}, indent=2))
            try:
                FEEDBACK_CURRENT_FILE.chmod(0o666)
            except Exception:
                pass

        activity["state"] = "awaiting_suggestions"
        _save_activity(activity)
        return activity

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
                           "Mexican", "Indian"). Empty = no preference.
        constraints: Schedule notes (e.g. "practice Monday and Wednesday, game Friday").
                     Days mentioned with activity keywords become quick-meal slots.

    Returns:
        {
          "candidates": top-20 eligible recipes (name/cuisine/health/minutes/meal_type),
          "selected_meals": {day: recipe_name} auto-proposed for the week,
          "quick_days": days flagged for quick meals,
          "week_start": "YYYY-MM-DD"
        }

    selected_meals is stored in the activity state. Use swap_meal() to adjust
    individual days, then call approve_menu() to send to Ashley.

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

    quick_days = list(activity.get("quick_days", []))
    if constraints:
        c_lower = constraints.lower()
        quick_signals = ("game", "practice", "busy", "quick", "early", "tournament")
        if any(s in c_lower for s in quick_signals):
            for day_name, abbrev in DAY_NAME_MAP.items():
                if day_name in c_lower and abbrev not in quick_days:
                    quick_days.append(abbrev)
    activity["quick_days"] = quick_days

    candidates = _load_candidates(quick_days)
    selected = _select_meals(candidates, quick_days, cuisine_direction or activity.get("cuisine_direction"))
    activity["selected_meals"] = selected
    activity["state"] = "awaiting_meal_approval"
    _save_activity(activity)

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
    Replace the planned meal for a given day (pre-signoff).

    Args:
        day: Day abbreviation (Mon, Tue, Wed, Thu, Fri, Sat, Sun).
        reason: Natural language reason (e.g. "we've had too much chicken").
        replacement: Optional explicit recipe name. If empty, auto-picks from candidates.

    Auto-pick logic: excludes already-selected recipes, prefers cuisine_direction
    match, prefers same meal_type (weekend/weeknight) as the displaced day.

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
        # Exclude every recipe already in the plan (including outgoing — we want something different)
        currently_selected = set(selected.values())
        candidates = _load_candidates(activity.get("quick_days", []))
        eligible = [c for c in candidates if c["name"] not in currently_selected]

        if not eligible:
            return {"error": f"No eligible replacement candidates found for {day}."}

        cuisine_dir = activity.get("cuisine_direction", "")
        if cuisine_dir and cuisine_dir.lower() not in ("what we've got", ""):
            c_lower = cuisine_dir.lower()
            cuisine_match = [c for c in eligible if c_lower in c.get("cuisine", "").lower()]
            if cuisine_match:
                eligible = cuisine_match + [c for c in eligible if c not in cuisine_match]

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


@mcp.tool()
def handle_ashley_reply(reply: str) -> dict:
    """
    Process Ashley's reply to the proposed menu.

    Args:
        reply: Ashley's text message (e.g. "looks good", "swap Tuesday to salmon",
               "can we do something lighter on Wednesday?")

    Approval:
      - Detects standard approval phrases ("looks good", "ok", "perfect", etc.)
      - Checks if any selected_meals have status "idea" in metadata
      - Auto-activates ideas by fetching from their source_url
      - If all ideas activate: generates plan files, launches apps, sends prep guide
        → state: complete
      - If any idea fetch fails: returns pending_ideas list for caller to resolve
        → state: awaiting_idea_activation
        Caller should: fetch URL content, call activate_idea_recipe(), then finalize_plan()

    Swap request:
      - Parses structured commands ("swap 3 to X", "change Tuesday to tacos")
      - Falls back to Claude Haiku for natural language ("we've had too much chicken")
      - Re-sends updated menu to Ashley, stays in awaiting_ashley_signoff

    Unparseable:
      - Returns {parsed: false, reply: ...} for manual handling

    Returns the updated activity state plus any action-specific fields.
    """
    activity = _load_activity()
    if activity.get("state") != "awaiting_ashley_signoff":
        return {"error": f"Not in awaiting_ashley_signoff state (current: {activity.get('state')})"}

    lowered = reply.lower().strip()
    selected = activity.get("selected_meals", {})
    week_start = date.fromisoformat(activity["week_start"])

    # ── Approval ──
    if any(lowered == p or lowered.startswith(p + " ") for p in APPROVAL_PHRASES):
        # Check for idea recipes on the menu
        recipes = _load_metadata()
        ideas_on_menu = [
            name for name in selected.values()
            if (key := _find_recipe_key(name, recipes)) and recipes[key].get("status") == "idea"
        ]

        # Try to auto-activate each idea
        failed_ideas = []
        for name in ideas_on_menu:
            if not _try_auto_activate(name, recipes):
                key = _find_recipe_key(name, recipes)
                source_url = recipes[key].get("source_url", "") if key else ""
                failed_ideas.append({"name": name, "source_url": source_url})

        activity["ideas_on_menu"] = ideas_on_menu

        if failed_ideas:
            activity["state"] = "awaiting_idea_activation"
            activity["pending_ideas"] = failed_ideas
            _save_activity(activity)
            return {
                **activity,
                "message": (
                    f"Ashley approved! But {len(failed_ideas)} idea recipe(s) couldn't be "
                    f"auto-fetched. Activate them manually then call finalize_plan()."
                ),
                "pending_ideas": failed_ideas,
            }

        # All clear — finalize
        return _do_finalize(activity)

    # ── Swap ──
    new_selected = _parse_swap(reply, selected, week_start)
    if not new_selected:
        new_selected = _claude_swap(reply, selected, activity.get("cuisine_direction"))

    if new_selected:
        activity["selected_meals"] = new_selected
        day_to_date = _day_date_map(week_start)
        meals_json = [
            {"day": f"{day} {day_to_date[day].strftime('%-m/%-d')}", "recipe": new_selected[day]}
            for day in DAYS_ORDER if day in new_selected
        ]
        cmd = [
            sys.executable, str(MENUBUILDER_DIR / "send_menu_partner.py"),
            "--meals", json.dumps(meals_json),
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception:
            pass
        _save_activity(activity)
        return {
            **activity,
            "message": "Ashley's swap applied. Updated menu re-sent to her.",
            "meals_sent": meals_json,
        }

    # ── Unparseable ──
    return {
        **activity,
        "parsed": False,
        "message": f"Could not parse Ashley's reply: {reply!r}. Handle manually.",
    }


@mcp.tool()
def activate_idea_recipe(name: str, content: str, source_url: str = "") -> dict:
    """
    Activate a pending idea recipe from provided markdown content.

    Called when handle_ashley_reply returns pending_ideas that couldn't be auto-fetched.
    After activating all pending ideas, call finalize_plan().

    Args:
        name: Recipe name (fuzzy-matched against metadata).
        content: Full markdown content for the recipe file (title, ingredients,
                 instructions). Provide the complete recipe text.
        source_url: Source URL for attribution in the .md file (optional).

    Returns:
        {
          "success": bool,
          "canonical_name": str,
          "filename": str,
          "remaining_pending": int  — how many pending ideas are still unactivated
        }
    """
    activity = _load_activity()
    recipes = _load_metadata()
    key = _find_recipe_key(name, recipes)

    if not key:
        return {"success": False, "error": f"Recipe not found in metadata: {name!r}"}

    # Write .md file
    filename = re.sub(r"[^\w\s-]", "", key).strip().replace(" ", "_") + ".md"
    md_path = RECIPES_DIR / filename
    RECIPES_DIR.mkdir(exist_ok=True)

    # Prepend attribution if source_url provided and not already in content
    if source_url and "Adapted from" not in content:
        source_label = recipes[key].get("source", source_url)
        header = f"# {key}\n\n**Adapted from**: [{source_label}]({source_url})\n\n"
        if not content.startswith("#"):
            content = header + content
        else:
            content = content  # assume caller included attribution

    md_path.write_text(content, encoding="utf-8")

    # Update metadata
    recipes[key]["status"] = "active"
    recipes[key]["filename"] = filename
    _save_metadata(recipes)

    # Update pending_ideas list in activity
    pending = [p for p in activity.get("pending_ideas", []) if p.get("name") != key]
    activity["pending_ideas"] = pending
    if activity.get("state") == "awaiting_idea_activation" and not pending:
        # All pending ideas resolved; signal that finalize_plan can proceed
        activity["state"] = "awaiting_finalization"
    _save_activity(activity)

    return {
        "success": True,
        "canonical_name": key,
        "filename": filename,
        "remaining_pending": len(pending),
    }


@mcp.tool()
def finalize_plan() -> dict:
    """
    Generate plan files, launch apps, and send prep guide.

    Can be called:
    - After handle_ashley_reply returns state=awaiting_finalization (all ideas activated)
    - Directly if all selected recipes are already active (no ideas to resolve)

    Steps:
    1. Verify all selected_meals ideas are activated (fails if any still have status="idea")
    2. Generate mealplan_YYYY-MM-DD.txt (DINNERS block + REMINDERS via Claude Sonnet)
    3. Generate shopping_YYYY-MM-DD.csv (Item / Notes(qty) / Date(cook date) format)
    4. Launch WeeklyShoppingList.app and WeeklyMealCalendar.app
    5. Send plan summary to admin (David) via Keanu outbox
    6. Send prep guide to both David and Ashley via Keanu outbox

    Requires ANTHROPIC_API_KEY in environment for REMINDERS generation (falls back
    to simple day-name reminders if not available).

    Returns:
        {
          "state": "complete",
          "plan_path": "/Users/Shared/cooking/weeklyplan/mealplan_YYYY-MM-DD.txt",
          "shopping_path": "/Users/Shared/cooking/weeklyplan/shopping_YYYY-MM-DD.csv",
          "prep_guide": "PREP GUIDE: ..."  (empty string if no prep components)
        }
    """
    activity = _load_activity()
    if activity.get("state") == "idle":
        return {"error": "No active workflow. Call start_menu_workflow first."}

    selected = activity.get("selected_meals", {})
    if not selected:
        return {"error": "No meals selected. Call get_meal_suggestions first."}

    # Verify no ideas remain un-activated
    recipes = _load_metadata()
    still_ideas = [
        name for name in selected.values()
        if (key := _find_recipe_key(name, recipes)) and recipes[key].get("status") == "idea"
    ]
    if still_ideas:
        return {
            "error": "Some selected recipes are still ideas and need activation first.",
            "pending_ideas": still_ideas,
        }

    return _do_finalize(activity)


@mcp.tool()
def get_prep_guide() -> dict:
    """
    Return the prep guide for the current week's meal plan.

    Reads the most recent mealplan_*.txt, looks up prep_components and
    prep_notes for each recipe in metadata, and returns a formatted guide.

    Works at any time — does not require an active workflow. Reflects the
    actual current plan including any mid-week swaps.

    Returns:
        {
          "prep_guide": "PREP GUIDE:\\n\\nRecipe Name:\\n  - component\\n  ...",
          "week_start": "YYYY-MM-DD",
          "recipes_with_prep": ["Recipe A", "Recipe B"],
          "recipes_without_prep": ["Recipe C", ...]
        }

    prep_guide is an empty string if no recipes have prep_components populated.
    """
    # Find the current week's meal plan file
    if not WEEKLYPLAN_DIR.exists():
        return {"error": "No meal plans found.", "prep_guide": ""}

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
        return {"error": "No current meal plan found.", "prep_guide": ""}

    week_start_str = plan_file.stem.replace("mealplan_", "")

    # Parse recipe names from the plan
    selected = {}
    for line in plan_file.read_text().splitlines():
        m = _PLAN_LINE_RE.match(line.strip())
        if m:
            selected[m.group(1)] = m.group(2).strip()

    if not selected:
        return {"error": "Could not parse any meals from plan.", "prep_guide": "", "week_start": week_start_str}

    # Build prep guide
    recipes = _load_metadata()
    with_prep = []
    without_prep = []
    lines = ["PREP GUIDE:"]

    for day in DAYS_ORDER:
        if day not in selected:
            continue
        name = selected[day]
        key = _find_recipe_key(name, recipes)
        if not key:
            without_prep.append(name)
            continue
        prep = recipes[key].get("prep_components", [])
        notes = recipes[key].get("prep_notes", "")
        if prep:
            with_prep.append(name)
            lines.append(f"\n{name}:")
            for p in prep:
                lines.append(f"  - {p}")
            if notes:
                lines.append(f"  Note: {notes}")
        else:
            without_prep.append(name)

    # Build flat Sunday prep task list — one bullet per action across all recipes
    task_lines = []
    for day in DAYS_ORDER:
        if day not in selected:
            continue
        name = selected[day]
        key = _find_recipe_key(name, recipes)
        if not key:
            continue
        prep = recipes[key].get("prep_components", [])
        for p in prep:
            # Strip after em-dash, semicolon, or opening parenthetical; lowercase
            phrase = re.split(r'\s+[—–]\s+|;\s*|\s+\(', p)[0].strip().rstrip(",;").lower()
            task_lines.append(f"- {phrase}")

    prep_guide = "Sunday prep:\n" + "\n".join(task_lines) if task_lines else "No advance prep needed this week."

    return {
        "prep_guide": prep_guide,
        "week_start": week_start_str,
        "recipes_with_prep": with_prep,
        "recipes_without_prep": without_prep,
    }


if __name__ == "__main__":
    mcp.run()
