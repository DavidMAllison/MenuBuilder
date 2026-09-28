#!/bin/bash
# Direct-write fallback for WeeklyMealCalendar.app -- see run_shopping_list.sh
# for the full rationale (same root cause, same fix).
exec /usr/bin/osascript "/Users/davidallison/projects/personal/MenuBuilder/WeeklyMealCalendar_backup.applescript"
