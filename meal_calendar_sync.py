"""
meal_calendar_sync.py -- EventKit replacement for WeeklyMealCalendar.app /
WeeklyMealCalendar_backup.applescript / parse_meal_calendar.py.

The tab-separated stdout protocol between parse_meal_calendar.py and the
AppleScript only existed because AppleScript couldn't call Python directly --
with everything in one process there's no serialization boundary to
preserve, so the data-prep logic (find_latest/load_metadata/parse_minutes)
is ported here nearly verbatim but builds DinnerSpec/LunchSpec objects
consumed directly, instead of being printed as tab-joined strings for a
second process to re-parse.

Behavior preserved exactly from the AppleScript version:
  - Calendar named "Dinners" (NOT auto-created if missing -- matches the
    AppleScript, which assumed it already exists). Dedicated calendar so it
    can be iCloud-shared with Ashley without exposing David's default
    calendar or risking the exact-time delete-and-recreate logic below
    colliding with an unrelated personal event (see Sep 14 2026 discussion).
  - Dinners at 6:00 PM: any existing event at that exact start time is
    deleted before creating the new one (safe re-run).
  - Lunches at noon, titled "Ashley's Lunch: <name>": matched for deletion
    by title prefix among events at that noon timestamp, not just by time,
    since other things could be scheduled at noon.
  - Event description built from health | "Cook time: X" | "Note: X" |
    "Recipe: X", joined by " | ".

Usage:
    .venv/bin/python3 meal_calendar_sync.py [--plan PATH] [--dry-run]

Library:
    from meal_calendar_sync import sync_calendar
    sync_calendar(plan_path=None, dry_run=False) -> dict
"""
import argparse
import glob
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from EventKit import EKEntityTypeEvent, EKEvent, EKSpanThisEvent
from Foundation import NSURL

import eventkit_helpers as ek

STATE_DIR = Path("/Users/Shared/cooking-state/weeklyplan")
LUNCH_STATE_PATH = Path("/Users/Shared/cooking-state/lunch_state.json")
METADATA_PATH = Path(os.path.expanduser("~/Dropbox/LLMContext/cooking/recipe_metadata.json"))
LOG_PATH = Path("/tmp/meal_calendar.log")
CALENDAR_NAME = "Dinners"
DINNER_HOUR = 18
LUNCH_HOUR = 12
LUNCH_TITLE_PREFIX = "Ashley's Lunch"

EFFORT = {"low": "(low)", "medium": "(med)", "high": "(high)"}


@dataclass
class DinnerSpec:
    dt: date
    title: str
    duration_min: int
    url: str
    desc: str


@dataclass
class LunchSpec:
    dt: date
    title: str
    url: str


def find_latest_plan(state_dir: Path = STATE_DIR) -> Optional[Path]:
    plans = sorted(glob.glob(str(state_dir / "mealplan_*.json")), reverse=True)
    return Path(plans[0]) if plans else None


def load_metadata() -> dict:
    try:
        with METADATA_PATH.open() as f:
            return json.load(f)["recipes"]
    except Exception:
        return {}


def parse_minutes(s: str) -> int:
    if not s:
        return 60
    mins = 0
    m = re.search(r"(\d+)\s*hour", s, re.I)
    if m:
        mins += int(m.group(1)) * 60
    m = re.search(r"(\d+)\s*min", s, re.I)
    if m:
        mins += int(m.group(1))
    return mins if mins > 0 else 60


def build_specs(plan_path: Path) -> tuple:
    """Ported from parse_meal_calendar.py's main() -- same logic, returns
    (dinners, lunches) as lists of DinnerSpec/LunchSpec instead of printing
    tab-separated lines."""
    with plan_path.open() as f:
        data = json.load(f)

    metadata = load_metadata()
    today = date.today()
    week_start_str = data.get("week_start", "")
    week_start = date.fromisoformat(week_start_str) if week_start_str else None

    dinners = []
    for m in data.get("meals", []):
        date_str = m.get("date", "")
        if not date_str:
            continue
        try:
            meal_date = date.fromisoformat(date_str)
        except ValueError:
            continue
        if meal_date < today:
            continue

        title = m.get("title", "")
        paired = m.get("paired_recipes", [])

        effort = ""
        if not paired and title in metadata:
            effort = EFFORT.get(metadata[title].get("weeknight_effort", ""), "")

        title_out = (title + " " + effort).strip() if effort else title
        health = m.get("health", "")
        cook_time = m.get("time", "")
        url = m.get("url", "")
        if not url and paired:
            url = paired[0].get("url", "") if paired else ""
        reminder = m.get("reminder", "")
        duration = parse_minutes(cook_time)

        desc_parts = []
        if health:
            desc_parts.append(health)
        if cook_time:
            desc_parts.append("Cook time: " + cook_time)
        if reminder:
            desc_parts.append("Note: " + reminder)
        if url:
            desc_parts.append("Recipe: " + url)
        desc = " | ".join(desc_parts)

        dinners.append(DinnerSpec(dt=meal_date, title=title_out, duration_min=duration, url=url, desc=desc))

    lunches = []
    try:
        with LUNCH_STATE_PATH.open() as f:
            ls = json.load(f)
        if ls.get("status") == "selected" and ls.get("current_pick"):
            lunch_name = ls["current_pick"]
            lunch_url = ls.get("url", "")

            set_date_str = ls.get("set_date", "")
            skip = False
            if week_start and set_date_str:
                try:
                    set_date = date.fromisoformat(set_date_str)
                    if (week_start - set_date).days > 7:
                        skip = True
                except ValueError:
                    pass

            if not skip:
                if week_start:
                    lunch_dates = [week_start + timedelta(days=i) for i in range(6)]
                else:
                    all_dates = sorted(set(
                        date.fromisoformat(mm["date"])
                        for mm in data.get("meals", [])
                        if mm.get("date")
                    ))
                    lunch_dates = [d for d in all_dates if d.weekday() != 5]

                lunch_dates = [d for d in lunch_dates if d >= today]
                for d in lunch_dates:
                    lunches.append(LunchSpec(dt=d, title=f"{LUNCH_TITLE_PREFIX}: {lunch_name}", url=lunch_url))
    except Exception:
        pass

    return dinners, lunches


def _at_time(d: date, hour: int) -> datetime:
    return datetime(d.year, d.month, d.day, hour, 0, 0)


def sync_calendar(plan_path: Optional[Path] = None, dry_run: bool = False) -> dict:
    logger = ek.configure_logging(LOG_PATH)
    plan_path = Path(plan_path) if plan_path else find_latest_plan()
    if plan_path is None:
        logger.info("no mealplan_*.json found, nothing to do")
        return {"dinners": 0, "lunches": 0, "dry_run": dry_run}

    dinners, lunches = build_specs(plan_path)
    logger.info("sync_calendar starting: plan=%s dinners=%d lunches=%d dry_run=%s", plan_path, len(dinners), len(lunches), dry_run)

    store = ek.get_store()
    ek.request_access(store, EKEntityTypeEvent)
    cal = ek.find_calendar_by_title(store, EKEntityTypeEvent, CALENDAR_NAME)
    if cal is None:
        raise LookupError(f'No calendar named "{CALENDAR_NAME}" found -- unlike Reminders, this is not auto-created')

    dinner_count = 0
    for d in dinners:
        start = _at_time(d.dt, DINNER_HOUR)
        end = start + timedelta(minutes=d.duration_min)
        _delete_matching_events(store, cal, start, title_prefix=None, dry_run=dry_run, logger=logger)

        if dry_run:
            logger.info("[dry-run] would create dinner: %r at %s (%dmin) url=%r", d.title, start, d.duration_min, d.url)
            dinner_count += 1
            continue

        ev = EKEvent.eventWithEventStore_(store)
        ev.setTitle_(d.title)
        ev.setStartDate_(start)
        ev.setEndDate_(end)
        ev.setNotes_(d.desc)
        ev.setCalendar_(cal)
        if d.url:
            try:
                ev.setURL_(NSURL.URLWithString_(d.url))
            except Exception:
                logger.warning("could not set URL for %r: %s", d.title, d.url)
        ok, err = store.saveEvent_span_commit_error_(ev, EKSpanThisEvent, False, None)
        if ok:
            dinner_count += 1
        else:
            logger.error("save failed for dinner %r: %s", d.title, err)

    lunch_count = 0
    for lu in lunches:
        noon = _at_time(lu.dt, LUNCH_HOUR)
        _delete_matching_events(store, cal, noon, title_prefix=LUNCH_TITLE_PREFIX, dry_run=dry_run, logger=logger)

        if dry_run:
            logger.info("[dry-run] would create lunch: %r at %s url=%r", lu.title, noon, lu.url)
            lunch_count += 1
            continue

        ev = EKEvent.eventWithEventStore_(store)
        ev.setTitle_(lu.title)
        ev.setStartDate_(noon)
        ev.setEndDate_(noon + timedelta(hours=1))
        ev.setCalendar_(cal)
        if lu.url:
            try:
                ev.setURL_(NSURL.URLWithString_(lu.url))
            except Exception:
                logger.warning("could not set URL for %r: %s", lu.title, lu.url)
        ok, err = store.saveEvent_span_commit_error_(ev, EKSpanThisEvent, False, None)
        if ok:
            lunch_count += 1
        else:
            logger.error("save failed for lunch %r: %s", lu.title, err)

    if not dry_run:
        ok, err = store.commit_(None)
        if not ok:
            logger.error("final commit failed: %s", err)
            raise RuntimeError(f"final commit failed: {err}")

    result = {"dinners": dinner_count, "lunches": lunch_count, "dry_run": dry_run, "plan": str(plan_path)}
    logger.info("sync_calendar result: %s", result)
    return result


def _delete_matching_events(store, cal, at_time: datetime, title_prefix, dry_run: bool, logger):
    """Delete events on `cal` starting exactly at `at_time`, optionally
    restricted to a title prefix (the noon/"Ashley's Lunch" case, since
    other things can be scheduled at noon -- matches the AppleScript's
    per-purpose delete-match logic exactly)."""
    day_start = datetime(at_time.year, at_time.month, at_time.day)
    day_end = day_start + timedelta(days=1)
    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(day_start, day_end, [cal])
    candidates = store.eventsMatchingPredicate_(predicate)
    for ev in candidates:
        if ev.startDate() != at_time:
            continue
        if title_prefix is not None and not str(ev.title() or "").startswith(title_prefix):
            continue
        if dry_run:
            logger.info("[dry-run] would delete existing event: %r at %s", str(ev.title()), at_time)
            continue
        ok, err = store.removeEvent_span_commit_error_(ev, EKSpanThisEvent, False, None)
        if not ok:
            logger.error("remove failed for %r: %s", str(ev.title()), err)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(sync_calendar(plan_path=args.plan, dry_run=args.dry_run))
