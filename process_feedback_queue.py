#!/usr/bin/env python3
"""
process_feedback_queue.py -- Drain the SMS feedback queue into weekly feedback JSON files.

Usage:
  python3 process_feedback_queue.py
  python3 process_feedback_queue.py --dry-run

Run at the start of the weekly menu workflow, before suggest_meals.py.

Queue file: /Users/Shared/cooking/feedback_queue.json
Meal plans: /Users/Shared/cooking/weeklyplan/mealplan_YYYY-MM-DD.txt
Output:     /Users/Shared/cooking/weeklyplan/mealplan_YYYY-MM-DD_feedback.json
"""

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

QUEUE_PATH = Path("/Users/Shared/cooking/feedback_queue.json")
WEEKLYPLAN_DIR = Path("/Users/Shared/cooking/weeklyplan")

# Day abbreviation -> weekday index (Monday=0)
DAY_WEEKDAY = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


def load_queue():
    if not QUEUE_PATH.exists():
        QUEUE_PATH.write_text("[]")
        QUEUE_PATH.chmod(0o666)
        return []
    try:
        raw = QUEUE_PATH.read_text().strip()
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: could not read queue ({e}), treating as empty.")
        return []


def drain_queue(dry_run):
    if dry_run:
        print("  [DRY RUN] Would write [] to queue file.")
        return
    QUEUE_PATH.write_text("[]")
    try:
        QUEUE_PATH.chmod(0o666)
    except OSError:
        pass


# Cache parsed meal plans to avoid re-reading the same file multiple times.
_plan_cache = {}


def parse_meal_plan(txt_path):
    """
    Parse a meal plan .txt file.

    Returns:
        meals:      {recipe_name_lower: (day_abbrev, date_str, canonical_name)}
        week_start: "YYYY-MM-DD" string, or None
    """
    key = str(txt_path)
    if key in _plan_cache:
        return _plan_cache[key]

    m = re.search(r"mealplan_(\d{4}-\d{2}-\d{2})\.txt$", txt_path.name)
    if not m:
        _plan_cache[key] = ({}, None)
        return {}, None

    week_start = m.group(1)
    week_start_date = datetime.strptime(week_start, "%Y-%m-%d").date()

    meals = {}
    try:
        content = txt_path.read_text()
    except OSError:
        _plan_cache[key] = ({}, week_start)
        return {}, week_start

    for line in content.splitlines():
        # Match: "Mon 4/27  Recipe Name [Health] | N min"
        m2 = re.match(
            r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d+/\d+\s+(.+?)(?:\s+\[(?:Heart-Healthy|Moderate|Indulgent)\].*)?$",
            line.strip(),
        )
        if not m2:
            continue

        day_abbrev = m2.group(1)
        recipe_raw = m2.group(2).strip()
        # Strip trailing [Health] tag if still present
        canonical = re.sub(r"\s*\[(?:Heart-Healthy|Moderate|Indulgent)\].*$", "", recipe_raw).strip()

        # Compute the actual calendar date for this day within the week
        target_dow = DAY_WEEKDAY[day_abbrev]
        date_str = None
        for i in range(7):
            candidate = week_start_date + timedelta(days=i)
            if candidate.weekday() == target_dow:
                date_str = candidate.strftime("%Y-%m-%d")
                break

        meals[canonical.lower()] = (day_abbrev, date_str or week_start, canonical)

    result = (meals, week_start)
    _plan_cache[key] = result
    return result


def find_recipe_in_plans(recipe_lower, plan_files):
    """
    Search plan files (most-recent first) for a recipe.

    Returns (week_start, day_abbrev, date_str, canonical_name) or None.
    """
    for txt_path in sorted(plan_files, reverse=True):
        meals, week_start = parse_meal_plan(txt_path)
        if recipe_lower in meals:
            day_abbrev, date_str, canonical = meals[recipe_lower]
            return week_start, day_abbrev, date_str, canonical
    return None


def load_feedback_json(feedback_path):
    if feedback_path.exists():
        try:
            return json.loads(feedback_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return None


def save_feedback_json(feedback_path, data, dry_run):
    if dry_run:
        print(f"  [DRY RUN] Would write {feedback_path.name}:")
        print(json.dumps(data, indent=2))
        return
    feedback_path.write_text(json.dumps(data, indent=2) + "\n")
    try:
        feedback_path.chmod(0o666)
    except OSError:
        pass


def make_feedback_entry(queue_entry):
    """Convert a queue entry dict into a feedback JSON entry."""
    timestamp = queue_entry.get("timestamp", "")
    date_str = timestamp.split("T")[0] if "T" in timestamp else timestamp[:10] or None
    return {
        "date": date_str if date_str else None,
        "person": queue_entry.get("person", ""),
        "sentiment": queue_entry.get("sentiment", ""),
        "note": queue_entry.get("feedback", ""),
        "source": "sms",
    }


def ensure_day_in_feedback(feedback_data, day_abbrev, recipe_name):
    """Add the day key to feedback_data['meals'] if missing."""
    if day_abbrev not in feedback_data["meals"]:
        feedback_data["meals"][day_abbrev] = {"recipe": recipe_name, "feedback": []}


def main():
    dry_run = "--dry-run" in sys.argv

    queue = load_queue()
    if not queue:
        print("Feedback queue is empty -- nothing to process.")
        return

    print(f"Processing {len(queue)} feedback queue {'entry' if len(queue) == 1 else 'entries'}...")

    plan_files = sorted(WEEKLYPLAN_DIR.glob("mealplan_????-??-??.txt"))
    if not plan_files:
        print("Warning: no meal plan files found in", WEEKLYPLAN_DIR)

    # Classify each queue entry as matched or unplanned
    # matched: {week_start: [(day_abbrev, date_str, canonical_name, queue_entry)]}
    matched_by_week = defaultdict(list)
    unplanned = []

    for entry in queue:
        recipe = entry.get("recipe", "").strip()
        if not recipe:
            continue
        result = find_recipe_in_plans(recipe.lower(), plan_files)
        if result:
            week_start, day_abbrev, date_str, canonical = result
            matched_by_week[week_start].append((day_abbrev, date_str, canonical, entry))
            print(f"  Matched: {recipe!r} -> {canonical!r} ({day_abbrev}, week of {week_start})")
        else:
            unplanned.append(entry)
            print(f"  Unplanned (no meal plan match): {recipe!r}")

    # Write matched entries into their feedback JSON files
    for week_start, items in sorted(matched_by_week.items()):
        feedback_path = WEEKLYPLAN_DIR / f"mealplan_{week_start}_feedback.json"
        feedback_data = load_feedback_json(feedback_path)
        if feedback_data is None:
            feedback_data = {"week_start": week_start, "meals": {}}

        for day_abbrev, date_str, canonical, entry in items:
            ensure_day_in_feedback(feedback_data, day_abbrev, canonical)
            feedback_data["meals"][day_abbrev]["feedback"].append(make_feedback_entry(entry))

        save_feedback_json(feedback_path, feedback_data, dry_run)
        print(f"  Wrote {feedback_path.name} ({len(items)} new {'entry' if len(items) == 1 else 'entries'})")

    # Store unplanned entries in the most-recent feedback JSON under "_unplanned"
    if unplanned:
        if plan_files:
            recent_txt = sorted(plan_files)[-1]
            m = re.search(r"mealplan_(\d{4}-\d{2}-\d{2})\.txt$", recent_txt.name)
            recent_week = m.group(1) if m else "unknown"
        else:
            recent_week = datetime.today().strftime("%Y-%m-%d")

        feedback_path = WEEKLYPLAN_DIR / f"mealplan_{recent_week}_feedback.json"
        feedback_data = load_feedback_json(feedback_path)
        if feedback_data is None:
            feedback_data = {"week_start": recent_week, "meals": {}}

        if "_unplanned" not in feedback_data:
            feedback_data["_unplanned"] = []

        for entry in unplanned:
            feedback_data["_unplanned"].append({
                "timestamp": entry.get("timestamp"),
                "person": entry.get("person"),
                "recipe": entry.get("recipe"),
                "sentiment": entry.get("sentiment"),
                "note": entry.get("feedback"),
                "source": "sms",
            })

        save_feedback_json(feedback_path, feedback_data, dry_run)
        print(f"  Stored {len(unplanned)} unplanned {'entry' if len(unplanned) == 1 else 'entries'} in {feedback_path.name}")

    matched_count = sum(len(v) for v in matched_by_week.values())
    drain_queue(dry_run)
    print(
        f"\nDone. {matched_count} matched, {len(unplanned)} unplanned. "
        f"Queue {'would be ' if dry_run else ''}drained."
    )


if __name__ == "__main__":
    main()
