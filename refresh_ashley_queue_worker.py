#!/usr/bin/env python3
"""
refresh_ashley_queue_worker.py -- background worker spawned by
refresh_ashley_recipe_queue() (mcp/menu_server.py).

Runs fill_menu_ideas.py to completion (all 6 agents -- this takes minutes,
which is exactly why it's detached rather than run inline in the MCP tool
call), then generates a fresh Ashley batch and drops it directly into
Keanu's outbox spool so she gets notified without any callback into
Keanu's own process -- this worker is spawned detached and outlives the
short-lived bridge subprocess that started it.

Always releases the lock file, even on failure, so a crashed run doesn't
permanently block future refreshes.

fill_menu_ideas.py resolves its paths via Path.home() -- explicitly forcing
HOME here regardless of what this worker inherited, since it may have been
spawned from a process running as a different macOS account (e.g. the SMS
bridge, which runs as allisonbot).

Not meant to be run manually -- spawned internally with a notify_handle arg:
    python3 refresh_ashley_queue_worker.py <notify_handle>
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

MENUBUILDER_DIR = Path(__file__).parent
LOCK_PATH = Path("/Users/Shared/cooking-state/ashley_refresh.lock")
OUTBOX_DIR = Path("/Users/Shared/cooking-state/outbox")
_DAVID_HOME = "/Users/davidallison"


def main():
    notify_handle = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        env = {**os.environ, "HOME": _DAVID_HOME}
        subprocess.run(
            [sys.executable, str(MENUBUILDER_DIR / "fill_menu_ideas.py")],
            cwd=str(MENUBUILDER_DIR),
            env=env,
        )

        sys.path.insert(0, str(MENUBUILDER_DIR))
        import pick_ashley_batch
        batch = pick_ashley_batch.generate_batch()

        if batch and notify_handle:
            OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
            path = OUTBOX_DIR / f"{time.time_ns()}_{os.getpid()}.json"
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps({"handle": notify_handle, "text": batch["message"]}))
    finally:
        LOCK_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
