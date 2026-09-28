-- WeeklyMealCalendar: create dinner (6 PM) and Ashley's Lunch (noon) events from meal plan JSON
-- Rebuilt as proper AppleScript applet so macOS can grant Calendar Apple Events permission

set helperScript to "/Users/davidallison/projects/personal/MenuBuilder/parse_meal_calendar.py"
set rawOutput to do shell script "/usr/bin/python3 " & quoted form of helperScript

if rawOutput is "" then
	display notification "No upcoming meals found in plan" with title "Weekly Meal Calendar"
	return
end if

-- Collect dinner and lunch records from tab-separated output
set dinnerList to {}
set lunchList to {}

set outputLines to paragraphs of rawOutput
repeat with aLine in outputLines
	set aLine to aLine as string
	if aLine is not "" then
		set AppleScript's text item delimiters to tab
		set parts to text items of aLine
		set AppleScript's text item delimiters to ""

		set lineType to item 1 of parts

		if lineType is "DINNER" and (count of parts) ≥ 8 then
			set end of dinnerList to {evYear:(item 2 of parts) as integer, evMonth:(item 3 of parts) as integer, evDay:(item 4 of parts) as integer, evTitle:item 5 of parts, evDuration:(item 6 of parts) as integer, evURL:item 7 of parts, evDesc:item 8 of parts}

		else if lineType is "LUNCH" and (count of parts) ≥ 6 then
			set end of lunchList to {lvYear:(item 2 of parts) as integer, lvMonth:(item 3 of parts) as integer, lvDay:(item 4 of parts) as integer, lvTitle:item 5 of parts, lvURL:item 6 of parts}
		end if
	end if
end repeat

-- Activate Calendar once, then do all operations in one scripting session
tell application "Calendar" to activate
delay 2

-- Aug 22 2026: same fixes as WeeklyShoppingList.app --
--   - a `sample` of a live stuck run showed the process blocked in
--     `-[OSAAppletDelegate presentScriptError:]` / `-[NSAlert runModal]`,
--     i.e. waiting forever for a human to dismiss the default error alert.
--     Fatal for any unattended trigger. The try/on error below guarantees an
--     error can never reach that path -- always caught, logged, reported via
--     a non-blocking notification instead.
--   - Reminders.app was separately observed to go fully unresponsive to any
--     Apple Event after sustained scripting, recoverable only by a full
--     quit+relaunch, and could degrade again mid-run so a single preflight
--     probe isn't enough. Apply the same retry-once pattern here in case
--     Calendar.app has the same failure mode.
set succeeded to false
set dinnerCount to 0
set lunchCount to 0
repeat with attemptNum from 1 to 3
	if attemptNum > 1 then
		do shell script "echo " & quoted form of "Retrying after attempt 1 failure -- restarting Calendar" & " >> /tmp/meal_calendar.log"
		try
			tell application "Calendar" to quit
		end try
		delay 2
		do shell script "killall Calendar 2>/dev/null; true"
		delay 2
		tell application "Calendar" to activate
		delay 30
	else
		set calendarHealthy to false
		try
			with timeout of 10 seconds
				tell application "Calendar" to get count of calendars
			end timeout
			set calendarHealthy to true
		end try
		if not calendarHealthy then
			do shell script "echo " & quoted form of "Calendar unresponsive at preflight -- restarting" & " >> /tmp/meal_calendar.log"
			try
				tell application "Calendar" to quit
			end try
			delay 2
			do shell script "killall Calendar 2>/dev/null; true"
			delay 2
			tell application "Calendar" to activate
			delay 30
		end if
	end if

	with timeout of 90 seconds
		try
			tell application "Calendar"
				set targetCalendar to first calendar whose name is "Calendar"

				-- Dinner events at 6 PM
				set dinnerCount to 0
				repeat with ev in dinnerList
					set startDate to current date
					set day of startDate to 1
					set year of startDate to evYear of ev
					set month of startDate to evMonth of ev
					set day of startDate to evDay of ev
					set hours of startDate to 18
					set minutes of startDate to 0
					set seconds of startDate to 0

					-- Delete existing 6 PM events on this date (safe re-run)
					try
						delete (every event of targetCalendar whose start date is startDate)
					end try

					set endDate to startDate + ((evDuration of ev) * 60)
					set newEv to make new event at end of events of targetCalendar with properties {summary:(evTitle of ev), start date:startDate, end date:endDate, description:(evDesc of ev)}
					try
						if (evURL of ev) is not "" then set url of newEv to evURL of ev
					end try
					set dinnerCount to dinnerCount + 1
				end repeat

				-- Lunch events at noon (Sunday–Friday)
				set lunchCount to 0
				repeat with lv in lunchList
					set noonDate to current date
					set day of noonDate to 1
					set year of noonDate to lvYear of lv
					set month of noonDate to lvMonth of lv
					set day of noonDate to lvDay of lv
					set hours of noonDate to 12
					set minutes of noonDate to 0
					set seconds of noonDate to 0

					-- Delete any existing Ashley's Lunch events at noon on this date
					try
						set noonEvs to (every event of targetCalendar whose start date is noonDate)
						repeat with e in noonEvs
							if summary of e starts with "Ashley's Lunch" then delete e
						end repeat
					end try

					set lunchEnd to noonDate + 3600
					set newLv to make new event at end of events of targetCalendar with properties {summary:(lvTitle of lv), start date:noonDate, end date:lunchEnd}
					try
						if (lvURL of lv) is not "" then set url of newLv to lvURL of lv
					end try
					set lunchCount to lunchCount + 1
				end repeat
			end tell
			set succeeded to true
		on error errMsg number errNum
			do shell script "echo " & quoted form of ("FATAL (attempt " & (attemptNum as string) & "): " & errMsg & " (" & (errNum as string) & ")") & " >> /tmp/meal_calendar.log"
		end try
	end timeout

	if succeeded then exit repeat
end repeat

if succeeded then
	set msg to (dinnerCount as string) & " dinners"
	if lunchCount > 0 then set msg to msg & ", " & (lunchCount as string) & " lunches"
	display notification (msg & " added to Calendar") with title "Weekly Meal Calendar"
else
	display notification "Failed after 2 attempts -- check /tmp/meal_calendar.log" with title "Weekly Meal Calendar"
end if
