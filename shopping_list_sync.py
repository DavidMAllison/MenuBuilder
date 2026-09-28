"""
shopping_list_sync.py -- EventKit replacement for WeeklyShoppingList.app /
WeeklyShoppingList_backup.applescript. See eventkit_helpers.py for why this
exists instead of AppleScript.

Behavior preserved exactly from the AppleScript version:
  - Reminders list "Grocery" (created if missing).
  - Incomplete reminders whose notes start with "[menu]" are deleted and
    replaced every run (stale-tag cleanup).
  - Reminders completed on/after the plan's start date (parsed from the CSV
    filename, shopping_YYYY-MM-DD.csv) are skipped, not re-added -- they were
    already bought this week.
  - New reminders: title=Item, notes="[menu] " + Notes, due date 4:00 PM on
    the row's date if parseable.

Usage:
    .venv/bin/python3 shopping_list_sync.py [--csv PATH] [--dry-run]

Library:
    from shopping_list_sync import sync_shopping_list
    sync_shopping_list(csv_path=None, dry_run=False) -> dict
"""
import argparse
import csv as csv_module
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from EventKit import EKEntityTypeReminder, EKReminder
from Foundation import NSDateComponents

import eventkit_helpers as ek

STATE_DIR = Path("/Users/Shared/cooking-state/weeklyplan")
LOG_PATH = Path("/tmp/shopping_list.log")
LIST_NAME = "Grocery"
MENU_TAG = "[menu]"
DUE_HOUR = 16  # 4:00 PM, matches the AppleScript's 57600 seconds


@dataclass
class ShoppingRow:
    item: str
    notes: str
    due_date: Optional[date]


def find_latest_csv(state_dir: Path = STATE_DIR) -> Path:
    candidates = sorted(state_dir.glob("shopping_*.csv"), reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No shopping_*.csv found in {state_dir}")
    return candidates[0]


def plan_start_from_filename(csv_path: Path) -> date:
    """cutoffDate equivalent. Falls back to 24h-ago on parse failure,
    matching the AppleScript's fallback behavior exactly."""
    m = re.match(r"shopping_(\d{4}-\d{2}-\d{2})\.csv$", csv_path.name)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return date.today() - timedelta(days=1)


def parse_csv(csv_path: Path) -> list:
    rows = []
    with csv_path.open(newline="") as f:
        for row in csv_module.DictReader(f):
            item = (row.get("Item") or "").strip()
            if not item:
                continue
            notes = (row.get("Notes") or row.get("For") or "").strip()
            date_str = (row.get("Date") or "").strip()
            due = None
            if date_str:
                try:
                    due = date.fromisoformat(date_str)
                except ValueError:
                    pass
            rows.append(ShoppingRow(item=item, notes=notes, due_date=due))
    return rows


def _nsdate_to_date(nsdate) -> Optional[date]:
    if nsdate is None:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(nsdate.timeIntervalSince1970(), tz=timezone.utc).date()


def compute_completed_skip_names(all_reminders, plan_start: date) -> set:
    """Names of reminders completed on/after plan_start -- these were
    already bought this week and should not be re-added."""
    names = set()
    for r in all_reminders:
        if not r.isCompleted():
            continue
        comp_date = _nsdate_to_date(r.completionDate())
        if comp_date is not None and comp_date >= plan_start:
            names.add(str(r.title()))
    return names


def find_stale_menu_reminders(all_reminders) -> list:
    return [
        r for r in all_reminders
        if not r.isCompleted() and str(r.notes() or "").startswith(MENU_TAG)
    ]


def sync_shopping_list(csv_path: Optional[Path] = None, dry_run: bool = False) -> dict:
    logger = ek.configure_logging(LOG_PATH)
    csv_path = Path(csv_path) if csv_path else find_latest_csv()
    rows = parse_csv(csv_path)
    plan_start = plan_start_from_filename(csv_path)
    logger.info("sync_shopping_list starting: csv=%s plan_start=%s dry_run=%s", csv_path, plan_start, dry_run)

    store = ek.get_store()
    ek.request_access(store, EKEntityTypeReminder)
    grocery_list = ek.find_or_create_reminder_list(store, LIST_NAME)

    predicate = store.predicateForRemindersInCalendars_([grocery_list])
    all_reminders = ek.fetch_reminders(store, predicate)

    stale = find_stale_menu_reminders(all_reminders)
    completed_names = compute_completed_skip_names(all_reminders, plan_start)

    deleted = 0
    for r in stale:
        if dry_run:
            logger.info("[dry-run] would delete stale reminder: %r", str(r.title()))
            deleted += 1
            continue
        ok, err = store.removeReminder_commit_error_(r, False, None)
        if ok:
            deleted += 1
        else:
            logger.error("remove failed for %r: %s", str(r.title()), err)

    added = skipped = 0
    for row in rows:
        if row.item in completed_names:
            skipped += 1
            logger.info("skip already-completed: %r", row.item)
            continue
        if dry_run:
            logger.info("[dry-run] would add: %r due=%s notes=%r", row.item, row.due_date, row.notes)
            added += 1
            continue
        rem = EKReminder.reminderWithEventStore_(store)
        rem.setTitle_(row.item)
        rem.setNotes_(f"{MENU_TAG} {row.notes}")
        rem.setCalendar_(grocery_list)
        if row.due_date:
            comps = NSDateComponents.alloc().init()
            comps.setYear_(row.due_date.year)
            comps.setMonth_(row.due_date.month)
            comps.setDay_(row.due_date.day)
            comps.setHour_(DUE_HOUR)
            comps.setMinute_(0)
            rem.setDueDateComponents_(comps)
        ok, err = store.saveReminder_commit_error_(rem, False, None)
        if ok:
            added += 1
        else:
            logger.error("save failed for %r: %s", row.item, err)

    if not dry_run:
        ok, err = store.commit_(None)
        if not ok:
            logger.error("final commit failed: %s", err)
            raise RuntimeError(f"final commit failed: {err}")

    result = {
        "added": added, "skipped": skipped, "deleted": deleted,
        "list": LIST_NAME, "dry_run": dry_run, "csv": str(csv_path),
    }
    logger.info("sync_shopping_list result: %s", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(sync_shopping_list(csv_path=args.csv, dry_run=args.dry_run))
