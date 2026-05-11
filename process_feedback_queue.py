#!/usr/bin/env python3
"""
process_feedback_queue.py -- Drain the SMS feedback queue into feedback_current.json.

Usage:
  python3 process_feedback_queue.py
  python3 process_feedback_queue.py --dry-run

Run at the start of the weekly menu workflow, before suggest_meals.py.

Queue file:   /Users/Shared/cooking/feedback_queue.json
Output:       /Users/Shared/cooking/weeklyplan/feedback_current.json
"""

import json
import sys
from pathlib import Path

QUEUE_PATH = Path("/Users/Shared/cooking/feedback_queue.json")
FEEDBACK_PATH = Path("/Users/Shared/cooking/weeklyplan/feedback_current.json")


def load_queue():
    if not QUEUE_PATH.exists():
        QUEUE_PATH.write_text("[]")
        QUEUE_PATH.chmod(0o666)
        return []
    try:
        raw = QUEUE_PATH.read_text().strip()
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: could not read queue ({e}), treating as empty.")
        return []


def drain_queue(dry_run):
    if dry_run:
        print("  [DRY RUN] Would write [] to queue file.")
        return
    QUEUE_PATH.write_text("[]")
    try:
        QUEUE_PATH.chmod(0o666)
    except OSError:
        pass


def load_feedback():
    if FEEDBACK_PATH.exists():
        try:
            return json.loads(FEEDBACK_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"entries": []}


def save_feedback(data, dry_run):
    if dry_run:
        print("  [DRY RUN] Would write feedback_current.json:")
        print(json.dumps(data, indent=2))
        return
    FEEDBACK_PATH.write_text(json.dumps(data, indent=2) + "\n")
    try:
        FEEDBACK_PATH.chmod(0o666)
    except OSError:
        pass


def make_entry(queue_entry):
    timestamp = queue_entry.get("timestamp", "")
    date_str = timestamp.split("T")[0] if "T" in timestamp else timestamp[:10] or None
    return {
        "recipe": queue_entry.get("recipe", "").strip(),
        "date": date_str,
        "person": queue_entry.get("person", ""),
        "sentiment": queue_entry.get("sentiment", ""),
        "note": queue_entry.get("feedback", ""),
        "source": "sms",
    }


def main():
    dry_run = "--dry-run" in sys.argv

    queue = load_queue()
    if not queue:
        print("Feedback queue is empty -- nothing to process.")
        return

    print(f"Processing {len(queue)} feedback queue {'entry' if len(queue) == 1 else 'entries'}...")

    feedback = load_feedback()
    count = 0

    for entry in queue:
        recipe = entry.get("recipe", "").strip()
        if not recipe:
            continue
        feedback["entries"].append(make_entry(entry))
        print(f"  Added: {recipe!r}")
        count += 1

    save_feedback(feedback, dry_run)
    drain_queue(dry_run)
    print(
        f"\nDone. {count} {'entry' if count == 1 else 'entries'} added to feedback_current.json. "
        f"Queue {'would be ' if dry_run else ''}drained."
    )


if __name__ == "__main__":
    main()
