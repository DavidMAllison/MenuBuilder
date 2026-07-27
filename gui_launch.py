#!/usr/bin/env python3
"""
gui_launch.py -- cross-account GUI app launch handoff.

Both menu_server.py (_do_finalize, which may run as davidallison or as
allisonbot depending on caller) and meal_swap.py (execute_swap, always
triggered by sms-assistant running as allisonbot) need to open
WeeklyShoppingList.app / WeeklyMealCalendar.app so their writes land in
davidallison's own iCloud account. A direct subprocess.Popen(["open", ...])
opens the app inside whichever macOS account the *calling* process happens
to be running as -- wrong when that's allisonbot, a separate Mac user
account signed into a different iCloud account, running concurrently via
Fast User Switching.

Callers write a one-shot request file instead of launching the app
directly; gui_launch_watcher.py -- running only inside davidallison's own
Aqua GUI session via a LaunchAgent -- picks it up and performs the actual
`open` call from there, regardless of who wrote the request.

Follows the exact spool-file convention as _send_outbox() in
mcp/menu_server.py: one create-exclusive file per event, mode 0o666, so
both davidallison and allisonbot (no shared Unix group) can write/read it.
"""

import json
import os
import time
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "config.json"


def _state_dir() -> Path:
    cfg = json.loads(_CONFIG_PATH.read_text())
    return Path(cfg.get("state_dir", "/Users/Shared/cooking-state"))


def gui_launch_requests_dir() -> Path:
    return _state_dir() / "gui_launch_requests"


def request_gui_launch(apps: list) -> None:
    """
    Request that the given .app names (e.g. "WeeklyShoppingList.app") be
    opened inside davidallison's own GUI session, regardless of which
    macOS account this process is currently running as.

    Writes a one-shot spool file; gui_launch_watcher.py (a LaunchAgent
    running in davidallison's Aqua session) picks it up, opens the apps,
    and deletes the file. If davidallison isn't logged in when this is
    called, the request just waits until his next login/session resume --
    see gui_launch_watcher.py.

    No-ops if apps is empty.
    """
    if not apps:
        return
    reqs_dir = gui_launch_requests_dir()
    existed = reqs_dir.exists()
    reqs_dir.mkdir(parents=True, exist_ok=True)
    if not existed:
        os.chmod(reqs_dir, 0o777)  # must be world-writable: no shared group between accounts
    path = reqs_dir / f"{time.time_ns()}_{os.getpid()}.json"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"apps": apps, "requested_at": time.time()}))
    # os.open()'s mode is masked by the caller's umask, so 0o666 above is
    # not reliable across accounts with different umasks -- chmod explicitly
    # (same pattern menu_server.py uses for plan/shopping files).
    os.chmod(path, 0o666)
