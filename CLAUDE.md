# MenuBuilder Project - Family Cooking Preferences

## Cooking Time Preferences by Day

### Weekdays (Monday-Friday)
- Target meal prep/cook time: ~1 hour or less
- Quick, efficient meals that fit busy weeknight schedules

### Weekends (Saturday & Sunday)
- Can accommodate longer cooking times
- Good days for more elaborate meals, slow-cooked dishes, or recipes with longer prep
- Can try new complex recipes

## Recipe Collection Location
- All recipes stored as Markdown files (`.md`) in: `~/Dropbox/LLMContext/cooking/recipes/` (local source of truth)
- Recipes also published to GitHub Pages: `https://davidmallison.github.io/menubuilder-recipes/` (web access, mobile-friendly)
- Recipes follow standardized format: Title, Time, Ingredients, Instructions, Notes
- **New recipes**: Always create as `.md` files in Dropbox AND push to the `menubuilder-recipes` GitHub repo. Do NOT create PDFs.
- **Attribution**: Always include "Adapted from [Source]" with a link to the original. Recipes are reworded/reformatted — not verbatim copies.

### Recipe Metadata System
- **Metadata file**: `~/Dropbox/LLMContext/cooking/recipe_metadata.json`
- **Tracked for each recipe**:
  - **Source**: Where recipe is from (e.g., "America's Test Kitchen", "Kenji Lopez-Alt")
  - **Cuisine Type**: Mexican, Chinese, Japanese, Italian, American, Indian, Thai, etc.
  - **Meal Timing**: Weeknight (<60 min) or Weekend (>60 min)
  - **Health Classification**:
    - Heart-Healthy: Low sat fat, low sodium, good for health goals
    - Moderate: Enjoy occasionally, can be modified
    - Indulgent: Special occasions, fun meals (max 1/week)
  - **Times Cooked**: Counter increments when meal is cooked
  - **Last Cooked Date**: `last_cooked_date` field (YYYY-MM-DD) — updated every time a meal is logged as cooked. Used by candidate filter to avoid repeating recent meals.
  - **Cooking Method**: `stovetop`, `oven`, `grill`, `slow_cooker`, `multi` -- all recipes populated as of Mar 2026
  - **Nutritional Info** (when available): Calories, fat, saturated fat, sodium, protein, fiber per serving
- **Usage**: JSON is the single source of truth for all metadata and ingredients. Recipe files contain only recipe content (title, ingredients, instructions, notes) -- no metadata footer.
- **Ingredients**: Each recipe entry has an `ingredients` array: `[{"name", "quantity", "unit", "category"}]`. Categories: Proteins, Produce, Dairy, Pantry/Asian, Dry Goods, Spices/Herbs. Populate when a recipe is first used in a meal plan.
- **Status**: `"active"` (in collection, .md file exists) | `"idea"` (in `recipe_metadata.json`, sourced but not yet tried) | `"disliked"` (tried, didn't like — .md file deleted, entry kept as tombstone) | `"ignored"` (decided to skip without trying)
- **Updates**: Times cooked increments automatically when user reports cooking a meal

## Weekly Meal Plans
- Weekly meal plans saved to: `~/Dropbox/LLMContext/cooking/weeklyplan/`
- Format: Plain text files named `mealplan_YYYY-MM-DD.txt`
- Cloud-based for access on phone
- **Plan format -- minimal, only what the apps need**:
  ```
  WEEKLY MEAL PLAN: Month DD - Month DD, YYYY

  ========================================
  DINNERS
  ========================================

  Mon M/DD  Recipe Name [Health] | Cook Time
            https://davidmallison.github.io/menubuilder-recipes/Recipe_Name

  ...

  BALANCE: N Heart-Healthy, N Moderate

  ========================================
  REMINDERS
  ========================================
  - MON: one-line timing note
  - TUE: one-line timing note
  ...
  ```
  - No LAST WEEK section, no LUNCHES, no SNACKS, no inline shopping list
  - Shopping list goes to `shopping_YYYY-MM-DD.csv` only
  - REMINDERS populate calendar event descriptions -- keep them to one line per day
- **Weekly balance**: 5-6 heart-healthy meals, 1 fun/indulgent meal okay
- **IMPORTANT - Meal logging before new menu**:
  - **ALWAYS ask the user to log last week's meals BEFORE generating a new menu**
  - Prompt: "Which meals did you cook last week? Any you didn't make or didn't enjoy?"
  - Do NOT skip this step -- it drives `times_cooked` and `last_cooked_date` updates
  - Gather feedback on new recipes
- **Historical tracking**: Use `last_cooked_date` in JSON -- do not re-read old plan files to check recency
  - Avoid meals cooked within the last 3 weeks
  - Monitor fried food frequency (max once every 2 weeks)

## Food Inventory
- Current inventory tracked in: `/Users/Shared/cooking/inventory.json` (path also in `config.json` as `inventory_path`)
- **Update process**: Ashley texts receipt photos to Keanu → GroceryAgent parses and appends/updates items in `inventory.json`
- **Meal planning approach**:
  - Ideally recommend meals using existing inventory
  - Okay to suggest new things that require shopping, especially on weekends
  - Balance using what's on hand with trying new recipes
- **Tracking**: Remove items as they're used in meals

## Dietary Preferences & Restrictions

### Health Requirements (PRIORITY)
- **Adult 1**: Managing cholesterol
  - Focus on: Lean proteins, fiber-rich foods, healthy fats (olive oil, avocado)
  - Limit: Saturated fats, red meat frequency, full-fat dairy, fried foods
  - Prioritize: Fish, chicken breast, whole grains, vegetables, fruits

- **Adult 2**: Managing blood pressure
  - Focus on: Low-sodium meals, potassium-rich foods (bananas, sweet potatoes, spinach)
  - Limit: Salt/sodium, processed foods, canned items with high sodium
  - Prioritize: Fresh ingredients, herbs for flavor instead of salt, DASH diet principles

### Spice Level
- Kids do not like spicy food
- Keep meals mild or offer spice on the side for adults

### Kids' Food Preferences
- **Takeout preferences**: Popeyes (preferred) > Chick-fil-A (acceptable)
- **Frozen meals**: Trader Joe's Orange Chicken (both parents and kids like)
- **Plain foods**: Kids prefer plain versions
  - Plain chicken (no sauce)
  - Plain pasta (no sauce)

### Recipe Structure Preference
- **Layered cooking**: When possible, design recipes so kids' portions can be pulled out before adding sauces/seasonings
- Examples:
  - Cook chicken fully, pull out kids' portions, then add sauce for adults
  - Cook pasta, pull out plain portions for kids, then toss remaining with sauce
- Not required for every recipe, but preferred when feasible

### Meal Variety Guidelines
- **Heart-healthy meals**: Prioritize 5-6 times per week
  - Fish (especially salmon, rich in omega-3s)
  - Chicken breast (lean protein)
  - Vegetarian options with beans/legumes
  - Whole grains
- **Fun/Indulgent meals**: ONE per week is okay
  - Fried foods, rich pasta dishes, red meat, etc.
  - Special occasions or weekend treats
  - Balance with rest of week's healthy eating
- **Red meat**: Limit to once per week or less
- **Fried food**: Maximum once every 2 weeks
- Balance comfort foods with lighter, healthier options while meeting health goals

## Recipe Development & Feedback

- **New meal suggestions**: Can recommend new meals not yet in the recipe collection during weekly planning
- **Feedback loop**: After the family tries a new meal:
  - If they liked it: Create standardized `.md` file, add to `~/Dropbox/LLMContext/cooking/recipes/`, add metadata entry
  - If they didn't like it: Document what specifically they didn't like to avoid in future recommendations

## Recipe Processing System

### Recipe Ideas Folder
- **Location**: `~/Dropbox/LLMContext/cooking/recipeideas/`
- **Purpose**: External app inbox only — SMS assistant and other outside apps write new ideas here
- **MenuBuilder never reads this folder directly** — `recipe_metadata.json` is the single source of truth for all recipes at every stage (`idea`, `active`, `disliked`, `ignored`)
- **Workflow**: External app writes file → weekly workflow (step 3) reviews with user → promoted to JSON as `status: "idea"` → file deleted
- **Do NOT** create `.md` files or active metadata entries until user confirms they tried and liked the recipe

## Tools
- **WeeklyShoppingList.app** -- populates "Grocery" Reminders list from the meal plan. Run: `open /Applications/WeeklyShoppingList.app`
- **WeeklyMealCalendar.app** -- adds dinner events to iCloud "Calendar" from meal plan. Run: `open /Applications/WeeklyMealCalendar.app`
- See `release-notes.md` for details on implemented features
- See `backlog.md` for planned features

## SMS Assistant
- **Location**: `/Users/Shared/sms-assistant/` (canonical — `~/projects/personal/sms-assistant/` is a stale stub, do not use)
- **Purpose**: Allows texting the assistant (Keanu) from a phone to query meal plans, recipes, and inventory
- **How it works**: Python polling script against `chat.db`; sends replies via AppleScript. No Twilio, ngrok, or FastAPI involved.
- **To start**: `cd /Users/Shared/sms-assistant && ./start.sh`
- **Guardrails**: Phone number whitelist in `config/settings.yaml`. System prompt in `system_prompts/menu.txt`. Neither can be changed via SMS.
- **Feedback queue**: Keanu writes recipe feedback to `/Users/Shared/cooking/feedback_queue.json`. Drained at the start of each menu workflow (step 0) by `process_feedback_queue.py`.
- **Menu approval**: After sending the weekly menu via `send_menu_partner.py`, Keanu captures Ashley's reply and writes it to `/Users/Shared/sms-assistant/menu_feedback_response.json`. MenuBuilder reads this file after approval.

## Meal Plan Generation Rules

### Shopping List
- **Use JSON ingredients first**: If a recipe has an `ingredients` array in `recipe_metadata.json`, use that -- do NOT read the recipe file.
- **Fall back to .md file only** if a recipe has no `ingredients` in JSON yet. After reading the file, add the ingredients to the JSON for next time.
- **Write to CSV only** (`shopping_YYYY-MM-DD.csv`) -- do NOT append to the meal plan txt
- Aggregate shared ingredients across recipes (e.g., total lemons, total chicken broth)
- Flag when inventory items may not cover recipe quantities (e.g., short ribs recipe needs 5 lbs but only 2 ribs in stock)

### Recipe Links
- **ALWAYS include GitHub Pages URLs** in meal plans for each recipe
- Format: `{github_pages_base_url}/{Filename_without_extension}` — get the base URL from `config.json` (`github_pages_base_url`)
- Example: `https://davidmallison.github.io/menubuilder-recipes/Korean_Chicken_Bulgogi`
- For recipes not yet on GitHub Pages (PDF-only, unconverted): fall back to `{dropbox_recipe_base_url}&preview=Filename.pdf`

### Recipe Processing
- When converting recipes from recipeideas to recipes folder, **delete the source file from recipeideas** after the standardized `.md` file is created
- Verify recipe times against actual recipe file content -- metadata times are sometimes wrong

### Time Accuracy
- Always verify cook times from the actual recipe file, not from the metadata (some entries are inaccurate)
- Note marinating time separately (e.g., "1 hour plus 1 hour marinating")
- Recipes over 60 minutes should be classified as Weekend meal_type

## Menu Generation Workflow

**Before starting: check for active SMS session** -- call `get_workflow_state` (MCP tool). If state is not `idle` or `complete`, a Sunday SMS workflow is already in progress. Resume from the current state rather than starting fresh:
- `awaiting_meal_logging` → pick up at step 1
- `awaiting_schedule` → pick up at step 2
- `awaiting_cuisine` → pick up at step 4 (candidates)
- `awaiting_meal_approval` → pick up at step 5
- `awaiting_ashley_signoff` → pick up at step 7

0. **Drain SMS feedback queue** -- `python3 ~/projects/personal/MenuBuilder/process_feedback_queue.py`. This reads `/Users/Shared/cooking/feedback_queue.json` and appends entries to `feedback_current.json`, then empties the queue. Run this before step 1 so queue feedback is available during meal logging. If the queue is empty, move on.
   - Entries with `sentiment: "disliked"` should be flagged during step 1.
   - Entries with `sentiment: "mixed"` should be surfaced for review before including the recipe this week.
1. **Log last week's meals** -- read `feedback_current.json` and the previous week's `mealplan_YYYY-MM-DD.txt`. For each meal in the plan with a feedback entry, auto-update `times_cooked`, `last_cooked_date`, and append entries to the `feedback` array in `recipe_metadata.json`. For meals with no feedback entry, prompt: "Any feedback on [recipe]? Did you make it?" After processing all meals, clear `feedback_current.json` to `{"entries": []}`. If sentiment is `"disliked"`, flag for tombstone discussion -- do not auto-delete. If disliked and confirmed: delete `.md` file, set `status: "disliked"` in JSON.
2. **Check schedule** -- read `~/projects/personal/FamilySchedule/schedule.json` and review `weekly_overrides` for the upcoming week. Identify any evening events that run into dinner time — those nights need quick-cook meals (slow cooker, ≤35 min, or leftovers). Ask the user about any one-off changes not yet in the file.
3. **Process recipeideas inbox** -- check the `recipeideas/` folder for any files not yet in `recipe_metadata.json`. If new files exist, show them to the user (title, source URL if present) and flag any that look like duplicates or are similar to existing active/idea entries. Wait for user to confirm before adding each one to the JSON as `status: "idea"` and deleting the file. Then review the full `status: "idea"` list — if there are fewer than 5 ideas or none have been added in 2+ weeks, suggest the user run recipe idea agents. Do NOT auto-spawn them.
4. **Run candidate filter** -- `python3 ~/projects/personal/MenuBuilder/suggest_meals.py`. Based on the schedule review in step 2, pass `--quick` for any evenings with late-running events (e.g. `--quick tue,thu`). The script filters by `last_cooked_date`, health balance, protein variety, cuisine variety, and seasonal method. Use its output as the candidate pool -- do not re-scan the JSON manually.
   - **Budget context**: Before proposing meals, read `~/Dropbox/LLMContext/Personal/grocery_budget_status.json` if it exists. If `suggested_weekly_spend` is low (tight week), prioritize inventory-heavy meals and avoid recipes that require many fresh or specialty ingredients. Mention the budget posture to the user before proposing candidates.
5. **Propose recipe names only** -- let user approve/swap before generating
6. **Send to Ashley** -- `python3 ~/projects/personal/MenuBuilder/send_menu_partner.py` (day + meal name only). **DO NOT fetch recipe URLs, create `.md` files, or populate ingredients for any `idea` meals yet -- wait for Ashley's approval first. If she swaps a meal out, that work is wasted.**
7. **Ashley approves** -- wait for her reply in `/Users/Shared/sms-assistant/menu_feedback_response.json`. Apply any changes.
7a. **Check for ideas** -- automated: scan the post-Ashley final meal list against `recipe_metadata.json` for any entries with `status: "idea"`. Use the post-Ashley list, not the original candidates — Ashley may have swapped in a meal that is itself an idea.
7b. **If ideas found** -- fetch content from the source URL directly. If the fetch fails, ask the user to paste the content manually. Once content is obtained: create `.md` file in `recipes/`, populate `ingredients` in JSON, set `status: "active"`. Do this before generating the plan.
8. **Generate meal plan** -- save `mealplan_YYYY-MM-DD.txt` (minimal format) + `shopping_YYYY-MM-DD.csv`
9. **Run apps** -- `open /Applications/WeeklyShoppingList.app` then `open /Applications/WeeklyMealCalendar.app`
10. **Send prep guide** -- via Keanu

## Learned Preferences
- **No duplicate proteins in a week** -- e.g., don't put salmon on two nights
- **Hoisin-Glazed Pork Tenderloin** is overused; avoid unless specifically requested
- **Incorporate new recipes regularly** -- check recipeideas folder and recently added recipes for variety
- **Cuisine variety matters** -- aim for zero cuisine repeats across the week when possible
- **Prefer direct, concise meal plans** -- no verbose notes in the plan file
- **Coconut Chicken Curry** -- family hit, kids ate the chicken. Good rotation candidate.
- **Asian Chicken Lettuce Wraps** -- kids liked them. Serve mushrooms on the side, not mixed into the meat. Works well as part of a spread (with wontons, soup dumplings, edamame) rather than standalone.
- **Lettuce wraps as a component** -- lighter recipes work better when paired with sides as a multi-dish meal
- **Herb-Marinated Lamb Rib Chops** -- new recipe Feb 2026. Buy double-cut frenched rib chops from Whole Foods. Cooked with herb rub (rosemary/thyme/oregano) + mint-dill sauce + white wine pan jus. First cook.
- **Pork Chops with White Wine and Herb Pan Sauce** -- new recipe Feb 2026. Good use of fresh herbs and white wine. Simple French technique, weeknight-friendly.
- **Vietnamese Lemongrass Chicken Bowl (Bun Ga Nuong)** -- family loved it. ~45 min active. Marinate 30 min to 2 hrs max -- lime in marinade breaks down chicken if left longer. Kids get plain chicken and noodles, skip Nuoc Cham.
