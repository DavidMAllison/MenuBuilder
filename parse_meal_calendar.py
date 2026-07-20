#!/usr/bin/env python3
"""Helper for WeeklyMealCalendar.app — outputs event data as tab-separated lines."""
import json
import os
import glob
import re
from datetime import date, timedelta


def find_latest():
    d = "/Users/Shared/cooking-state/weeklyplan/"
    plans = sorted(glob.glob(os.path.join(d, "mealplan_*.json")), reverse=True)
    return plans[0] if plans else None


def load_metadata():
    path = os.path.expanduser("~/Dropbox/LLMContext/cooking/recipe_metadata.json")
    try:
        with open(path) as f:
            return json.load(f)["recipes"]
    except Exception:
        return {}


EFFORT = {"low": "(low)", "medium": "(med)", "high": "(high)"}


def parse_minutes(s):
    if not s:
        return 60
    mins = 0
    m = re.search(r"(\d+)\s*hour", s, re.I)
    if m:
        mins += int(m.group(1)) * 60
    m = re.search(r"(\d+)\s*min", s, re.I)
    if m:
        mins += int(m.group(1))
    return mins if mins > 0 else 60


def main():
    plan_path = find_latest()
    if not plan_path:
        return

    with open(plan_path) as f:
        data = json.load(f)

    metadata = load_metadata()
    today = date.today()
    week_start_str = data.get("week_start", "")
    week_start = date.fromisoformat(week_start_str) if week_start_str else None

    for m in data.get("meals", []):
        date_str = m.get("date", "")
        if not date_str:
            continue
        try:
            meal_date = date.fromisoformat(date_str)
        except ValueError:
            continue
        if meal_date < today:
            continue

        title = m.get("title", "")
        paired = m.get("paired_recipes", [])

        effort = ""
        if not paired and title in metadata:
            effort = EFFORT.get(metadata[title].get("weeknight_effort", ""), "")

        title_out = (title + " " + effort).strip() if effort else title
        health = m.get("health", "")
        cook_time = m.get("time", "")
        url = m.get("url", "")
        if not url and paired:
            url = paired[0].get("url", "") if paired else ""
        reminder = m.get("reminder", "")
        duration = parse_minutes(cook_time)

        desc_parts = []
        if health:
            desc_parts.append(health)
        if cook_time:
            desc_parts.append("Cook time: " + cook_time)
        if reminder:
            desc_parts.append("Note: " + reminder)
        if url:
            desc_parts.append("Recipe: " + url)
        desc = " | ".join(desc_parts)

        print("\t".join([
            "DINNER",
            str(meal_date.year),
            str(meal_date.month),
            str(meal_date.day),
            title_out,
            str(duration),
            url,
            desc,
        ]))

    try:
        with open("/Users/Shared/cooking-state/lunch_state.json") as f:
            ls = json.load(f)
        if ls.get("status") == "selected" and ls.get("current_pick"):
            lunch_name = ls["current_pick"]
            lunch_url = ls.get("url", "")

            set_date_str = ls.get("set_date", "")
            skip = False
            if week_start and set_date_str:
                try:
                    set_date = date.fromisoformat(set_date_str)
                    if (week_start - set_date).days > 7:
                        skip = True
                except ValueError:
                    pass

            if not skip:
                if week_start:
                    lunch_dates = [week_start + timedelta(days=i) for i in range(6)]
                else:
                    all_dates = sorted(set(
                        date.fromisoformat(mm["date"])
                        for mm in data.get("meals", [])
                        if mm.get("date")
                    ))
                    lunch_dates = [d for d in all_dates if d.weekday() != 5]

                lunch_dates = [d for d in lunch_dates if d >= today]
                for d in lunch_dates:
                    print("\t".join([
                        "LUNCH",
                        str(d.year),
                        str(d.month),
                        str(d.day),
                        "Ashley's Lunch: " + lunch_name,
                        lunch_url,
                    ]))
    except Exception:
        pass


if __name__ == "__main__":
    main()
