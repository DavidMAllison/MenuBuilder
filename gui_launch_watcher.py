#!/usr/bin/env python3
"""
gui_launch_watcher.py -- opens WeeklyShoppingList.app / WeeklyMealCalendar.app
on behalf of gui_launch.request_gui_launch(), always from inside
davidallison's own Aqua GUI session.

Runs exclusively as a LaunchAgent (com.menubuilder.guilaunch.plist) loaded
into davidallison's own login session -- never allisonbot's. Triggered by:
  - WatchPaths on the request directory (fires promptly on new requests)
  - RunAtLoad (sweeps up anything written while davidallison wasn't logged
    in / his session wasn't up yet, the moment his session starts)

Each run processes every pending *.json request file, then exits -- this
is intentionally a one-shot sweep, not a persistent daemon (WatchPaths and
a long-running process don't combine usefully). Requests are deduplicated
by app name within a single sweep so a burst of near-simultaneous requests
opens each app at most once.
"""

import json
import sys
import time
from pathlib import Path
from subprocess import Popen

sys.path.insert(0, str(Path(__file__).parent))
from gui_launch import gui_launch_requests_dir  # noqa: E402

MAX_AGE_SECONDS = 48 * 60 * 60  # discard, don't open, requests older than this


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
        app_path = Path("/Applications") / app
        if not app_path.exists():
            print(f"SKIP missing app: {app_path}")
            continue
        Popen(["open", str(app_path)])
        print(f"open {app_path}")


if __name__ == "__main__":
    main()
