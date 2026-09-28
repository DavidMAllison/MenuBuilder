#!/usr/bin/env python3
"""
gui_launch_watcher.py -- syncs the shopping list to Reminders and the meal
plan to Calendar, on behalf of gui_launch.request_gui_launch(), always from
inside davidallison's own Aqua GUI session.

Runs exclusively as a LaunchAgent (com.menubuilder.guilaunch.plist) loaded
into davidallison's own login session -- never allisonbot's. Triggered by:
  - WatchPaths on the request directory (fires promptly on new requests)
  - RunAtLoad (sweeps up anything written while davidallison wasn't logged
    in / his session wasn't up yet, the moment his session starts)

Each run processes every pending *.json request file, then exits -- this
is intentionally a one-shot sweep, not a persistent daemon (WatchPaths and
a long-running process don't combine usefully). Requests are deduplicated
by app name within a single sweep so a burst of near-simultaneous requests
syncs each target at most once.

Aug 22 2026: switched from `open`-ing WeeklyShoppingList.app/
WeeklyMealCalendar.app (AppleScript automation of Reminders.app/Calendar.app)
to calling shopping_list_sync.py/meal_calendar_sync.py directly via EventKit
-- AppleScript automation of those GUI apps proved fundamentally unreliable
on this Mac (Apple Events intermittently timed out regardless of script
quality; see bug_weeklyshoppinglist_hang memory). EventKit talks to the
underlying data store directly, no GUI app involved. This LaunchAgent is
already running as the correct venv python inside the correct Aqua session
-- exactly the identity that was granted EventKit access -- so the sync
functions are called in-process, not via `open`/subprocess, and each is
wrapped in its own try/except so one failing sync can't block the other in
the same sweep. The app names in DISPATCH are historical (that's still what
callers pass to request_gui_launch) but no longer refer to launching an app.
"""

import importlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gui_launch import gui_launch_requests_dir  # noqa: E402

MAX_AGE_SECONDS = 48 * 60 * 60  # discard, don't sync, requests older than this

DISPATCH = {
    "WeeklyShoppingList.app": ("shopping_list_sync", "sync_shopping_list"),
    "WeeklyMealCalendar.app": ("meal_calendar_sync", "sync_calendar"),
}


def main() -> None:
    reqs_dir = gui_launch_requests_dir()
    if not reqs_dir.exists():
        return
    files = sorted(reqs_dir.glob("*.json"))
    if not files:
        return

    apps_to_open = set()
    for f in files:
        try:
            body = json.loads(f.read_text())
            age = time.time() - body.get("requested_at", 0)
            if age > MAX_AGE_SECONDS:
                print(f"SKIP stale ({age / 3600:.1f}h): {f.name} apps={body.get('apps')}")
            else:
                apps_to_open.update(body.get("apps", []))
                print(f"OK {f.name} apps={body.get('apps')}")
        except Exception as e:
            print(f"ERROR reading {f.name}: {type(e).__name__}: {e}")
        finally:
            f.unlink(missing_ok=True)

    for app in sorted(apps_to_open):
        if app not in DISPATCH:
            print(f"SKIP unknown app: {app}")
            continue
        module_name, func_name = DISPATCH[app]
        try:
            module = importlib.import_module(module_name)
            result = getattr(module, func_name)()
            print(f"OK {app}: {result}")
        except Exception as e:
            print(f"ERROR {app}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
