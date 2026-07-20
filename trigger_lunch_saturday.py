#!/usr/bin/env python3
"""
Saturday 10 AM: Send Ashley 3 lunch suggestions for the week.

Reads last week's pick from lunch_state.json to exclude it.
Formats as plain text with each recipe name on its own line (iMessage
wraps URLs into rich preview cards).

Sends via Keanu HTTP API (POST http://localhost:5050/send).
"""
import json
import subprocess
import sys
from pathlib import Path

import httpx

CONFIG_PATH      = Path(__file__).parent / "config.json"
LUNCH_STATE_FILE = Path("/Users/Shared/cooking-state/lunch_state.json")
KEANU_URL        = "http://localhost:5050/send"

config          = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
PARTNER_HANDLE  = config.get("partner_handle", "")


def _last_pick() -> str:
    if LUNCH_STATE_FILE.exists():
        try:
            state = json.loads(LUNCH_STATE_FILE.read_text())
            return state.get("last_pick") or state.get("current_pick") or ""
        except Exception:
            pass
    return ""


def _get_suggestions(exclude: str) -> list[dict]:
    script = Path(__file__).parent / "suggest_lunch.py"
    cmd    = [sys.executable, str(script), "--json"]
    if exclude:
        cmd += ["--exclude", exclude]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return json.loads(result.stdout)


def _send(handle: str, text: str) -> None:
    try:
        httpx.post(KEANU_URL, json={"handle": handle, "text": text}, timeout=10)
    except Exception as e:
        print(f"Send failed: {e}", file=sys.stderr)


def main() -> None:
    if not PARTNER_HANDLE:
        print("No partner_handle in config.json", file=sys.stderr)
        sys.exit(1)

    exclude     = _last_pick()
    suggestions = _get_suggestions(exclude)

    if not suggestions:
        print("No lunch candidates found; skipping Saturday text.", file=sys.stderr)
        sys.exit(0)

    lines = ["Hey! What do you want for lunch this week? Here are some ideas:\n"]
    for i, pick in enumerate(suggestions, 1):
        lines.append(f"{i}. {pick['name']}")
        if pick.get("url"):
            lines.append(pick["url"])
        lines.append("")

    lines.append("Reply with the number, a name, or a URL for something else.")
    msg = "\n".join(lines).rstrip()

    print(f"Sending lunch suggestions to {PARTNER_HANDLE}:\n{msg}\n")
    _send(PARTNER_HANDLE, msg)
    print("Done.")


if __name__ == "__main__":
    main()
