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
- **Purpose**: Inbox for human-sourced items only — user texts a URL or image from phone, SMS assistant writes it here. Nothing else touches this folder.
- **NEVER recommend items from this folder** — contents have not been reviewed and have no metadata. They are not candidates for weekly meal planning.
- **Processing**: On-demand only — user asks "any recipe ideas?" and we process them together. Not part of the weekly menu workflow.
- **Workflow**: User/SMS writes file → user asks to review → show each file (title, source URL), check for duplicates against `recipe_metadata.json` → user confirms → **fetch URL and extract ingredients + instructions** → add complete entry to `recipe_metadata.json` as `status: "active"` with a `.md` file → delete the file
- **If fetch fails**: do NOT add a partial entry. Leave the file in the inbox and ask the user to paste the recipe content.

### Two paths into recipe_metadata.json
- **Agent pipeline** (`fill_menu_ideas.py`): automated. Runs cuisine agents → fetches full recipe data → writes directly to `recipe_metadata.json` as `status: "active"` with `ingredients_raw`, `instructions`, `url`, and a `.md` file. Low-quality auto-generated content gets `needs_review: true`. Skips any recipe the agent couldn't fully fetch. No manual step.
- **Human inbox** (`recipeideas/` folder): always manual. User reviews → confirms → fetch URL → write complete entry to JSON + create `.md` → delete inbox file.

### JSON invariant — no naked entries
**Every entry in `recipe_metadata.json` must have `ingredients_raw` and `instructions` populated.** No title-only or URL-only stubs. If an entry can't be written with full data, it doesn't get written at all — it stays in the inbox or is skipped by the agent. This rule applies at write time for all new entries going forward.

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
- **Fall back to `ingredients_raw`** if `ingredients` is absent or empty: idea recipes added via fill_menu_ideas.py have an `ingredients_raw` field (list of raw strings, e.g. `"¾ cup toor dal"`). Use these directly — format each as the full raw string in the Item column, with quantity already included. No need to parse.
- **Fall back to .md file** only if neither `ingredients` nor `ingredients_raw` is present. After reading the file, add the ingredients to the JSON for next time.
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
- `awaiting_cuisine` → pick up at step 3 (candidates)
- `awaiting_meal_approval` → pick up at step 4
- `awaiting_ashley_signoff` → pick up at step 6

0. **Drain SMS feedback queue** -- `python3 ~/projects/personal/MenuBuilder/process_feedback_queue.py`. This reads `/Users/Shared/cooking/feedback_queue.json` and appends entries to `feedback_current.json`, then empties the queue. Run this before step 1 so queue feedback is available during meal logging. If the queue is empty, move on.
   - Entries with `sentiment: "disliked"` should be flagged during step 1.
   - Entries with `sentiment: "mixed"` should be surfaced for review before including the recipe this week.
1. **Log last week's meals** -- read `feedback_current.json` and the previous week's `mealplan_YYYY-MM-DD.txt`. Process each meal using these rules:
   - **`not_cooked` in feedback_current.json** → skip; do not increment times_cooked or last_cooked_date
   - **`disliked` in feedback_current.json** → flag for tombstone discussion (do not auto-delete); confirm with user, then delete `.md` file and set `status: "disliked"` in JSON
   - **First-cook recipe (`times_cooked == 0`) with no feedback entry** → ask: "You tried [recipe] for the first time — what did you think? Keep it in rotation?" Yes → log as cooked. No → tombstone after confirmation.
   - **All other meals** (established recipes, no feedback entry) → auto-log as cooked: increment `times_cooked`, set `last_cooked_date`. No prompting.
   After processing all meals, clear `feedback_current.json` to `{"entries": []}`.
   - **Do not ask for feedback on established recipes** — if they have something to share they'll bring it up.
2. **Check schedule** -- read `~/projects/personal/FamilySchedule/schedule.json` and review `weekly_overrides` for the upcoming week. Identify any evening events that run into dinner time — those nights need quick-cook meals (slow cooker, ≤35 min, or leftovers). Ask the user about any one-off changes not yet in the file.
3. **Run candidate filter** -- `python3 ~/projects/personal/MenuBuilder/suggest_meals.py`. Based on the schedule review in step 2, pass `--quick` for any evenings with late-running events (e.g. `--quick tue,thu`). The script filters by `last_cooked_date`, health balance, protein variety, cuisine variety, and seasonal method. Use its output as the candidate pool -- do not re-scan the JSON manually.
   - **Budget context**: Before proposing meals, read `~/Dropbox/LLMContext/Personal/grocery_budget_status.json` if it exists. If `suggested_weekly_spend` is low (tight week), prioritize inventory-heavy meals and avoid recipes that require many fresh or specialty ingredients. Mention the budget posture to the user before proposing candidates.
   - **Thin pool check**: If fewer than 5 candidates or the list is weak on variety, mention it and ask: "The idea pool is light on [cuisine] — want me to run an agent to add some options before we pick?" Do NOT run agents silently or by default. Agents are outside the workflow; they add latency and return unclassified recipes that need metadata before they can be scored.
4. **Propose recipe names only** -- before presenting, verify the 7-meal list against the Variety Rules above (protein limits, max 2 per cuisine family, at least 1 newer recipe). Adjust candidates if needed, then present. Let user approve/swap before generating.
5. **Send to Ashley** -- `python3 ~/projects/personal/MenuBuilder/send_menu_partner.py` (day + meal name only).
6. **Ashley approves** -- wait for her reply in `/Users/Shared/sms-assistant/menu_feedback_response.json`. Apply any changes.
7. **Generate meal plan** -- save `mealplan_YYYY-MM-DD.txt` (minimal format) + `shopping_YYYY-MM-DD.csv`
8. **Run apps** -- `open /Applications/WeeklyShoppingList.app` then `open /Applications/WeeklyMealCalendar.app`
9. **Send prep guide** -- via Keanu

## Learned Preferences

### Variety Rules (enforce at step 4 before proposing)
- **Protein variety** -- max 2 chicken meals per week; max 1 of every other protein (salmon, pork, beef, shrimp, vegetarian). If the candidate list is heavy on one protein, swap before proposing.
- **Cuisine variety** -- max 2 meals from the same cuisine family (Asian, Mexican, Italian, etc.) per week. "Asian" counts as one family — Japanese + Korean + Chinese = 3 Asian meals is too many.
- **New recipe pressure** -- at least 1 meal per week should be a recipe not cooked in the last 6 weeks, ideally from the `idea` list. Agents (mexican_agent, asian_agent, indian_agent, chef_agent) feed the idea pool between cycles — they don't run during planning. Don't default to the same rotation every week.
- **Cross-check before proposing** -- before presenting the 7-meal list, verify: no protein appears more than the limits above, no cuisine family appears more than twice, at least 1 is relatively new to the rotation.

### Other Preferences
- **No duplicate proteins in a week** -- e.g., don't put salmon on two nights
- **Hoisin-Glazed Pork Tenderloin** -- fine to include now that the recipe pool is large enough to rotate naturally; don't over-represent pork tenderloin but no longer needs a blanket avoid
- **Incorporate new recipes regularly** -- pull from `times_cooked: 0` entries in recipe_metadata.json (all recipes are now `status: "active"` at intake) for variety
- **Cuisine variety matters** -- aim for zero cuisine repeats across the week when possible
- **Prefer direct, concise meal plans** -- no verbose notes in the plan file
- **Coconut Chicken Curry** -- family hit, kids ate the chicken. Good rotation candidate.
- **Asian Chicken Lettuce Wraps** -- kids liked them. Serve mushrooms on the side, not mixed into the meat. Works well as part of a spread (with wontons, soup dumplings, edamame) rather than standalone.
- **Lettuce wraps as a component** -- lighter recipes work better when paired with sides as a multi-dish meal
- **Herb-Marinated Lamb Rib Chops** -- new recipe Feb 2026. Buy double-cut frenched rib chops from Whole Foods. Cooked with herb rub (rosemary/thyme/oregano) + mint-dill sauce + white wine pan jus. First cook.
- **Pork Chops with White Wine and Herb Pan Sauce** -- new recipe Feb 2026. Good use of fresh herbs and white wine. Simple French technique, weeknight-friendly.
- **Vietnamese Lemongrass Chicken Bowl (Bun Ga Nuong)** -- family loved it. ~45 min active. Marinate 30 min to 2 hrs max -- lime in marinade breaks down chicken if left longer. Kids get plain chicken and noodles, skip Nuoc Cham.
