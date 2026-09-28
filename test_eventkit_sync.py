#!/usr/bin/env python3
"""
test_eventkit_sync.py — Offline validation of shopping_list_sync.py /
meal_calendar_sync.py's pure logic (CSV/JSON parsing, cutoff-date math,
skip-list diffing, dinner/lunch spec building) using fake EventKit objects.
No real EventKit calls, no permissions needed, no writes to Reminders/
Calendar -- this is what can be validated before David grants the real
EventKit permissions.

Usage: .venv/bin/python3 test_eventkit_sync.py
"""
import json
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import shopping_list_sync as sls
import meal_calendar_sync as mcs

_COLOR = sys.stdout.isatty()
def _c(code, s): return f"\033[{code}m{s}\033[0m" if _COLOR else s
def OK(s): return _c("92", f"  OK  {s}")
def FAIL(s): return _c("91", f" FAIL {s}")

_failures = []


def check(label, cond, detail=""):
    if cond:
        print(OK(label))
    else:
        print(FAIL(f"{label}  {detail}"))
        _failures.append(label)


# ── Fakes ─────────────────────────────────────────────────────────────────

class FakeNSDate:
    """Minimal stand-in for NSDate -- only what our code calls."""
    def __init__(self, dt: datetime):
        self._dt = dt

    def timeIntervalSince1970(self):
        return self._dt.replace(tzinfo=timezone.utc).timestamp()


class FakeReminder:
    def __init__(self, title, notes="", completed=False, completion_date=None):
        self._title = title
        self._notes = notes
        self._completed = completed
        self._completion_date = FakeNSDate(completion_date) if completion_date else None

    def title(self): return self._title
    def notes(self): return self._notes
    def isCompleted(self): return self._completed
    def completionDate(self): return self._completion_date


# ── shopping_list_sync tests ────────────────────────────────────────────────

def test_plan_start_from_filename():
    check(
        "plan_start_from_filename: valid filename",
        sls.plan_start_from_filename(Path("shopping_2026-08-17.csv")) == date(2026, 8, 17),
    )
    fallback = sls.plan_start_from_filename(Path("garbage.csv"))
    check(
        "plan_start_from_filename: invalid filename falls back to ~24h ago",
        fallback == date.today() - timedelta(days=1),
        detail=str(fallback),
    )


def test_parse_csv():
    with tempfile.TemporaryDirectory() as d:
        csv_path = Path(d) / "shopping_2026-08-17.csv"
        csv_path.write_text(
            "Item,Notes,Date\n"
            "chicken breast,2 lbs | Recipe A,2026-08-18\n"
            "garlic,,2026-08-19\n"
            ",should be skipped,2026-08-20\n"
            "onion,1 whole,not-a-date\n"
        )
        rows = sls.parse_csv(csv_path)
        check("parse_csv: correct row count (blank Item skipped)", len(rows) == 3, detail=str(rows))
        check("parse_csv: item/notes/date parsed", rows[0].item == "chicken breast" and rows[0].notes == "2 lbs | Recipe A" and rows[0].due_date == date(2026, 8, 18))
        check("parse_csv: empty notes tolerated", rows[1].notes == "")
        check("parse_csv: unparseable date -> due_date None, row still kept", rows[2].item == "onion" and rows[2].due_date is None)

        csv_path2 = Path(d) / "shopping_2026-08-24.csv"
        csv_path2.write_text("Item,For,Date\nbasil,1 bunch,2026-08-25\n")
        rows2 = sls.parse_csv(csv_path2)
        check("parse_csv: falls back to 'For' column when 'Notes' absent", rows2[0].notes == "1 bunch")


def test_compute_completed_skip_names():
    plan_start = date(2026, 8, 17)
    reminders = [
        FakeReminder("chicken breast", completed=True, completion_date=datetime(2026, 8, 18, 10, 0)),  # after -> skip
        FakeReminder("garlic", completed=True, completion_date=datetime(2026, 8, 17, 0, 0)),            # exactly at cutoff -> skip
        FakeReminder("onion", completed=True, completion_date=datetime(2026, 8, 10, 0, 0)),             # before -> don't skip (prior week)
        FakeReminder("basil", completed=False),                                                          # incomplete -> ignored
    ]
    names = sls.compute_completed_skip_names(reminders, plan_start)
    check("compute_completed_skip_names: recently-completed included", "chicken breast" in names)
    check("compute_completed_skip_names: exactly-at-cutoff included (>=)", "garlic" in names)
    check("compute_completed_skip_names: prior-week completion excluded", "onion" not in names)
    check("compute_completed_skip_names: incomplete reminders excluded", "basil" not in names)
    check("compute_completed_skip_names: exact set size", len(names) == 2, detail=str(names))


def test_find_stale_menu_reminders():
    reminders = [
        FakeReminder("old item", notes="[menu] leftover", completed=False),
        FakeReminder("done item", notes="[menu] bought", completed=True),   # completed -> not stale
        FakeReminder("unrelated", notes="not menu tagged", completed=False),  # no tag -> not stale
    ]
    stale = sls.find_stale_menu_reminders(reminders)
    check("find_stale_menu_reminders: only incomplete + [menu]-tagged", [r.title() for r in stale] == ["old item"], detail=str([r.title() for r in stale]))


# ── meal_calendar_sync tests ─────────────────────────────────────────────────

def test_parse_minutes():
    check("parse_minutes: '1 hour'", mcs.parse_minutes("1 hour") == 60)
    check("parse_minutes: '45 min'", mcs.parse_minutes("45 min") == 45)
    check("parse_minutes: '1 hour 15 min'", mcs.parse_minutes("1 hour 15 min") == 75)
    check("parse_minutes: empty string defaults to 60", mcs.parse_minutes("") == 60)
    check("parse_minutes: unparseable defaults to 60", mcs.parse_minutes("quick") == 60)


def test_build_specs(monkeypatch_state_dir=True):
    today = date.today()
    future1 = today + timedelta(days=1)
    future2 = today + timedelta(days=2)
    past = today - timedelta(days=5)

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        plan = {
            "week_start": future1.isoformat(),
            "meals": [
                {"date": past.isoformat(), "title": "Old Meal (should be excluded)", "time": "30 min"},
                {"date": future1.isoformat(), "title": "Roast Chicken", "health": "Heart-Healthy", "time": "1 hour", "url": "http://example.com/chicken", "reminder": "brine overnight"},
                {"date": future2.isoformat(), "title": "Quick Tacos", "time": "20 min", "paired_recipes": [{"url": "http://example.com/tacos"}]},
            ],
        }
        plan_path = d / "mealplan_test.json"
        plan_path.write_text(json.dumps(plan))

        lunch_state = {
            "status": "selected",
            "current_pick": "Turkey Sandwich",
            "url": "http://example.com/sandwich",
            "set_date": future1.isoformat(),
        }
        lunch_path = d / "lunch_state.json"
        lunch_path.write_text(json.dumps(lunch_state))

        orig_lunch_path = mcs.LUNCH_STATE_PATH
        mcs.LUNCH_STATE_PATH = lunch_path
        try:
            dinners, lunches = mcs.build_specs(plan_path)
        finally:
            mcs.LUNCH_STATE_PATH = orig_lunch_path

        check("build_specs: past meal excluded", all(dn.title != "Old Meal (should be excluded)" for dn in dinners))
        check("build_specs: correct dinner count", len(dinners) == 2, detail=str(dinners))

        chicken = next((dn for dn in dinners if dn.title.startswith("Roast Chicken")), None)
        check("build_specs: dinner found", chicken is not None)
        if chicken:
            check("build_specs: duration parsed", chicken.duration_min == 60)
            check("build_specs: desc includes health/cook-time/note/url", all(
                part in chicken.desc for part in ["Heart-Healthy", "Cook time: 1 hour", "Note: brine overnight", "Recipe: http://example.com/chicken"]
            ), detail=chicken.desc)

        tacos = next((dn for dn in dinners if dn.title.startswith("Quick Tacos")), None)
        check("build_specs: url falls back to paired_recipes when meal has none", tacos is not None and tacos.url == "http://example.com/tacos")

        check("build_specs: lunch generated for the week", len(lunches) > 0, detail=str(lunches))
        if lunches:
            check("build_specs: lunch title has 'Ashley's Lunch' prefix", lunches[0].title.startswith("Ashley's Lunch:"))
            check("build_specs: Saturday excluded from lunch dates", all(lu.dt.weekday() != 5 for lu in lunches))


def main():
    test_plan_start_from_filename()
    test_parse_csv()
    test_compute_completed_skip_names()
    test_find_stale_menu_reminders()
    test_parse_minutes()
    test_build_specs()

    print()
    if _failures:
        print(_c("91", f"{len(_failures)} check(s) FAILED:"))
        for f in _failures:
            print(_c("91", f"  - {f}"))
        sys.exit(1)
    else:
        print(_c("92", "All checks passed."))


if __name__ == "__main__":
    main()
