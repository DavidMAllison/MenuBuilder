# MenuBuilder - Release Notes

## Mar 2026

### WeeklyShoppingList.app - Notes, Dates, and Categorization Fix
- Reminder name is now just the ingredient (e.g. "Ground pork") -- no qty or meal name in title
- Qty and meal name moved to the reminder **Notes** field (e.g. "1.5 lbs | Ground Pork with Ginger and Miso")
- Each reminder now has a **due date** set to the day the ingredient is needed
- Fixed a categorization bug where items were placed in the wrong Reminders group when the meal name contained "chicken" -- resolved by keeping meal names out of the reminder title
- CSV `For` column replaced with `Notes` (qty + meal name) and `Date` (YYYY-MM-DD) columns
- Script rewritten to write Python parser to a temp file to avoid shell quoting/escape issues
- Date set via integer year/month/day properties rather than string coercion (locale-safe)

### WeeklyMealCalendar.app - Timeout Fix
- Fixed AppleEvent timeout (-1712) errors when Calendar wasn't open before the app ran
- Script now explicitly activates Calendar and waits 2 seconds before adding events
- Switched from one bulk AppleScript call to one call per event for better reliability

## Feb 2026

### WeeklyMealCalendar.app
- `/Applications/WeeklyMealCalendar.app` reads the most recent meal plan and adds dinner events to the iCloud "Calendar" (the default empty calendar)
- Events start at 6 PM, end time based on actual cook time parsed from the meal plan
- Event notes include: health tag, cook time, reminder notes, Dropbox recipe URL
- Only adds events from today forward (skips past days)
- Run via: `open /Applications/WeeklyMealCalendar.app`
- Permission required: System Preferences > Security & Privacy > Privacy > Automation > Calendar

### WeeklyShoppingList.app
- `/Applications/WeeklyShoppingList.app` reads the most recent shopping CSV and populates the "Grocery" Reminders list
- Each reminder shows item name + which day it's needed (e.g. "Roma tomatoes - 2 lbs  [Wed]")
- Clears unchecked items before repopulating
- Partner's phone is shared on the "Grocery" list — updates sync automatically
- Auto-run at end of every meal plan generation: `open /Applications/WeeklyShoppingList.app`
- **Feb 2026**: Switched from "Weekly Shopping" list to built-in "Grocery" list

### Recipe PDF Standard
- All recipes stored as PDFs using Python `fpdf` library
- Standardized format: Title, Time, Ingredients, Instructions, Notes, Metadata section
- Metadata section includes: Source, Cuisine, Meal Type, Health, Times Cooked
