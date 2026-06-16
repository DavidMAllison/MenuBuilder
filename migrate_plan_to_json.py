#!/usr/bin/env python3
"""
One-time migration: convert mealplan_*.txt files to mealplan_*.json.

Reads each .txt plan, parses it, writes the equivalent .json.
The .txt files are left in place for reference.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

WEEKLYPLAN_DIR = Path.home() / "Dropbox/LLMContext/cooking/weeklyplan"

_DAY_RE = re.compile(
    r"^(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+(\d+)/(\d+)\s+(.+)"
)
_HEALTH_TIME_RE = re.compile(r"^(.+?)\s+\[([^\]]+)\]\s+\|\s+(.+)$")
_REMINDER_RE = re.compile(
    r"^-\s+(Sun|Mon|Tue|Wed|Thu|Fri|Sat)[^:]*:\s*(.+)", re.IGNORECASE
)


def parse_txt(path: Path) -> dict:
    basename = path.name  # mealplan_YYYY-MM-DD.txt
    week_monday = date.fromisoformat(basename[9:19])
    week_sunday = week_monday - __import__("datetime").timedelta(days=1)
    week_saturday = week_monday + __import__("datetime").timedelta(days=5)
    year = week_monday.year

    lines = path.read_text().splitlines()

    dinners_start = dinners_end = reminders_start = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s == "DINNERS":
            dinners_start = i + 1
        if dinners_start and dinners_end is None and s.startswith("BALANCE:"):
            dinners_end = i
        if s == "REMINDERS":
            reminders_start = i + 1

    dinner_lines = lines[dinners_start:dinners_end] if dinners_start else []
    reminder_lines = lines[reminders_start:] if reminders_start else []

    meals = []
    i = 0
    while i < len(dinner_lines):
        line = dinner_lines[i].strip()
        m = _DAY_RE.match(line)
        if m:
            day_name = m.group(1)
            month, day_num = int(m.group(2)), int(m.group(3))
            rest = m.group(4)

            m2 = _HEALTH_TIME_RE.match(rest)
            if m2:
                title, health, time_str = m2.group(1).strip(), m2.group(2), m2.group(3).strip()
            else:
                parts = rest.split("|", 1)
                title = parts[0].strip()
                health = ""
                time_str = parts[1].strip() if len(parts) > 1 else ""

            url = ""
            if i + 1 < len(dinner_lines):
                next_s = dinner_lines[i + 1].strip()
                if next_s.startswith("http"):
                    url = next_s
                    i += 1

            try:
                meal_date = date(year, month, day_num).isoformat()
            except ValueError:
                meal_date = ""

            meals.append({
                "day": day_name,
                "date": meal_date,
                "title": title,
                "health": health,
                "time": time_str,
                "url": url,
                "reminder": "",
            })
        i += 1

    reminders: dict = {}
    for line in reminder_lines:
        rm = _REMINDER_RE.match(line.strip())
        if rm:
            reminders[rm.group(1)[:3].title()] = rm.group(2).strip()
    for meal in meals:
        meal["reminder"] = reminders.get(meal["day"], "")

    health_counts: dict = {}
    for meal in meals:
        h = meal["health"]
        if h:
            health_counts[h] = health_counts.get(h, 0) + 1

    return {
        "week_start": week_sunday.isoformat(),
        "week_end": week_saturday.isoformat(),
        "generated_date": week_monday.isoformat(),
        "balance": health_counts,
        "meals": meals,
    }


def main():
    txt_files = sorted(WEEKLYPLAN_DIR.glob("mealplan_*.txt"), reverse=True)
    if not txt_files:
        print("No .txt plan files found.")
        return

    for txt_path in txt_files:
        json_path = txt_path.with_suffix(".json")
        if json_path.exists() and "--force" not in sys.argv:
            print(f"SKIP  {json_path.name} (already exists; use --force to overwrite)")
            continue
        try:
            data = parse_txt(txt_path)
            json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            print(f"OK    {txt_path.name} → {json_path.name}  ({len(data['meals'])} meals)")
        except Exception as e:
            print(f"ERROR {txt_path.name}: {e}")


if __name__ == "__main__":
    main()
