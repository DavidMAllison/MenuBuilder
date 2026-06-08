-- Weekly Shopping List -> Apple Reminders
-- Reads the most recent shopping_*.csv from Dropbox

set dropboxPath to (POSIX path of (path to home folder)) & "Dropbox/LLMContext/cooking/weeklyplan/"

set latestCSV to do shell script "ls -t " & quoted form of dropboxPath & "shopping_*.csv 2>/dev/null | head -1"

if latestCSV is "" then
	display dialog "No shopping CSV found." buttons {"OK"} default button "OK"
	return
end if

-- Write Python parser to a temp file
set pyPath to "/tmp/parse_shopping.py"
do shell script "cat > " & pyPath & " << 'PYEOF'
import csv, sys
from datetime import datetime
with open(sys.argv[1]) as f:
    for row in csv.DictReader(f):
        item = row.get('Item', '').strip()
        notes = row.get('Notes', row.get('For', '')).strip()
        date_str = row.get('Date', '').strip()
        y, m, d = '', '', ''
        if date_str:
            try:
                dt = datetime.strptime(date_str, '%Y-%m-%d')
                y, m, d = str(dt.year), str(dt.month), str(dt.day)
            except:
                pass
        sys.stdout.write(item + chr(9) + notes + chr(9) + y + chr(9) + m + chr(9) + d + chr(10))
PYEOF"

set rawLines to do shell script "python3 " & pyPath & " " & quoted form of latestCSV
set itemLines to paragraphs of rawLines

set listName to "Grocery"

-- Ensure Reminders is running before scripting it
tell application "Reminders" to activate
delay 2

tell application "Reminders"
	if not (exists list listName) then
		make new list with properties {name:listName}
	end if
	set targetList to list listName
	delete (every reminder of targetList whose completed is false and body starts with "[menu]")
	
	-- Build a set of recently-completed reminder names (last 24 hours) to avoid re-adding them
	-- 24h window handles same-day re-runs without dropping items from consecutive weekly lists
	set completedNames to {}
	set cutoffDate to (current date) - (24 * hours)
	set completedReminders to (every reminder of targetList whose completed is true)
	repeat with r in completedReminders
		if (completion date of r) > cutoffDate then
			set end of completedNames to (name of r) as string
		end if
	end repeat
	
	-- Keep tell targetList outside the loop — opening the connection once is far faster
	-- and prevents crashes on large lists
	tell targetList
		repeat with aLine in itemLines
			set aLine to aLine as string
			if aLine is not "" then
				set AppleScript's text item delimiters to tab
				set parts to text items of aLine
				set AppleScript's text item delimiters to ""
				
				if (count of parts) ≥ 1 then
					set reminderName to item 1 of parts
					set reminderNotes to ""
					if (count of parts) ≥ 2 then set reminderNotes to item 2 of parts
					set yearStr to ""
					set monthStr to ""
					set dayStr to ""
					if (count of parts) ≥ 5 then
						set yearStr to item 3 of parts
						set monthStr to item 4 of parts
						set dayStr to item 5 of parts
					end if
					
					if reminderName is not "" and reminderName is not in completedNames then
						try
							if yearStr is not "" and monthStr is not "" and dayStr is not "" then
								set dueDate to current date
								set day of dueDate to 1
								set year of dueDate to (yearStr as integer)
								set month of dueDate to (monthStr as integer)
								set day of dueDate to (dayStr as integer)
								set time of dueDate to 57600 -- 4:00 PM
								make new reminder with properties {name:reminderName, body:"[menu] " & reminderNotes, due date:dueDate}
							else
								make new reminder with properties {name:reminderName, body:"[menu] " & reminderNotes}
							end if
						end try
					end if
				end if
			end if
		end repeat
	end tell
end tell

display notification "Shopping list ready in Reminders" with title "Weekly Shopping"

