"""
Send this week's proposed menu to Partner for approval via Keanu.
Accepts a meal list directly so it can be called before the plan file is written.

Usage:
    python3 send_menu_partner.py --meals '[{"day": "Mon 5/19", "recipe": "Korean Chicken Bulgogi"}, ...]'

Writes to Keanu's outbox (triggers the iMessage) and drops a pending flag file.
Keanu captures Partner's reply and writes it to menu_feedback_response.json.
MenuBuilder deletes the pending file once the menu is confirmed.
"""

import json
import argparse
import os
import time
from datetime import datetime
from pathlib import Path

OUTBOX_DIR = Path("/Users/Shared/cooking-state/outbox")
PENDING_FILE = Path("/Users/Shared/sms-assistant/menu_feedback_pending.json")

_config = json.loads((Path(__file__).parent / "config.json").read_text())
PARTNER_HANDLE = _config["partner_handle"]


def format_message(meals: list[dict]) -> str:
    lines = ["This week's menu:"]
    for m in meals:
        day = m['day'].split()[0]
        lines.append(f"{day}: {m['recipe']}")
    lines.append("\nAll good? Any changes?")
    return "\n".join(lines)


def send_to_partner(meals: list[dict]) -> None:
    if PENDING_FILE.exists():
        print("A menu approval is already pending. Check if Partner has replied before sending again.")
        return

    if not meals:
        print("No meals provided.")
        return

    message = format_message(meals)
    print("Sending to Partner:\n")
    print(message)
    print()

    # One create-exclusive spool file — keep in sync with sms-assistant tools.queue_outbox
    OUTBOX_DIR.mkdir(exist_ok=True)
    spool_path = OUTBOX_DIR / f"{time.time_ns()}_{os.getpid()}.json"
    fd = os.open(str(spool_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"handle": PARTNER_HANDLE, "text": message}))

    PENDING_FILE.write_text(json.dumps({
        "sent_at": datetime.now().isoformat(),
        "partner_handle": PARTNER_HANDLE,
    }))

    print("Menu sent to Partner via Keanu. Waiting for her reply.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--meals", required=True, help='JSON array of {"day", "recipe"} objects')
    args = parser.parse_args()

    meals = json.loads(args.meals)
    send_to_partner(meals)


if __name__ == "__main__":
    main()
