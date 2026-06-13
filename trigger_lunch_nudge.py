#!/usr/bin/env python3
"""
Saturday 6 PM: Nudge Ashley if she hasn't picked a lunch yet.

Checks lunch_state.json — if status is not "selected" or set_date is
not today, sends a short reminder. If she already replied, exits silently.

Sends via Keanu HTTP API (POST http://localhost:5050/send).
"""
import json
import sys
from datetime import date
from pathlib import Path

import httpx

CONFIG_PATH      = Path(__file__).parent / "config.json"
LUNCH_STATE_FILE = Path("/Users/Shared/cooking/lunch_state.json")
KEANU_URL        = "http://localhost:5050/send"

config         = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
PARTNER_HANDLE = config.get("partner_handle", "")


def _already_picked() -> bool:
    if not LUNCH_STATE_FILE.exists():
        return False
    try:
        state    = json.loads(LUNCH_STATE_FILE.read_text())
        picked   = state.get("status") == "selected"
        set_date = state.get("set_date", "")
        return picked and set_date == date.today().isoformat()
    except Exception:
        return False


def _send(handle: str, text: str) -> None:
    try:
        httpx.post(KEANU_URL, json={"handle": handle, "text": text}, timeout=10)
    except Exception as e:
        print(f"Send failed: {e}", file=sys.stderr)


def main() -> None:
    if not PARTNER_HANDLE:
        print("No partner_handle in config.json", file=sys.stderr)
        sys.exit(1)

    if _already_picked():
        print("Ashley already picked lunch — no nudge needed.")
        sys.exit(0)

    msg = "Hey — just a nudge! Did you want to pick a lunch for this week? Reply anytime or I'll leave it up to you."
    print(f"Sending nudge to {PARTNER_HANDLE}")
    _send(PARTNER_HANDLE, msg)
    print("Done.")


if __name__ == "__main__":
    main()
