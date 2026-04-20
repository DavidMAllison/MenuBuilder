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
- All recipes stored as PDFs in: `~/Dropbox/LLMContext/cooking/recipes/`
- Recipes follow standardized format: Title, Time, Ingredients, Instructions, Notes
- Cloud-based (Dropbox) for access on phone while cooking
- **PDF generation**: MUST use Python `fpdf` library to create real PDFs. Do NOT write plain text to a `.pdf` file -- it won't open. Use the `fpdf` PDF generation approach for all recipe PDFs.

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
- **Usage**: JSON is the single source of truth for all metadata and ingredients. PDFs contain only recipe content (title, ingredients, instructions, notes) -- no metadata footer.
- **Ingredients**: Each recipe entry has an `ingredients` array: `[{"name", "quantity", "unit", "category"}]`. Categories: Proteins, Produce, Dairy, Pantry/Asian, Dry Goods, Spices/Herbs. Populate when a recipe is first used in a meal plan.
- **Status**: `"active"` (in collection, PDF exists) | `"idea"` (in recipeideas, not yet tried) | `"disliked"` (tried, didn't like — PDF deleted, entry kept as tombstone)
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
            https://www.dropbox.com/...

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
- Current inventory tracked in: `~/Dropbox/LLMContext/cooking/inventory.md`
- **Update process**: User shares receipt photos from Kroger/Costco, inventory gets updated
- **Meal planning approach**:
  - Ideally recommend meals using existing inventory
  - Okay to suggest new things that require shopping, especially on weekends
  - Balance using what's on hand with trying new recipes
- **Tracking**: Remove items as they're used in meals

## Dietary Preferences & Restrictions

### Health Requirements (PRIORITY)
- **Adult 1**: Needs to reduce lipoprotein levels
  - Focus on: Lean proteins, fiber-rich foods, healthy fats (olive oil, avocado)
  - Limit: Saturated fats, red meat frequency, full-fat dairy, fried foods
  - Prioritize: Fish, chicken breast, whole grains, vegetables, fruits

- **Adult 2**: Needs to lower blood pressure
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
  - If they liked it: Create standardized PDF, add to `~/Dropbox/LLMContext/cooking/recipes/`, add metadata entry
  - If they didn't like it: Document what specifically they didn't like to avoid in future recommendations

## Recipe Processing System

### Recipe Ideas Folder
- **Location**: `~/Dropbox/LLMContext/cooking/recipeideas/`
- **Purpose**: Staging area for recipes the family hasn't tried yet
- **Workflow**: Save idea → family tries it from source → if they liked it → THEN create PDF + add to recipes/ + add metadata
- **Do NOT** create PDFs or metadata entries for recipeideas files preemptively — only after user confirms they liked it

## Tools
- **WeeklyShoppingList.app** -- populates "Grocery" Reminders list from the meal plan. Run: `open /Applications/WeeklyShoppingList.app`
- **WeeklyMealCalendar.app** -- adds dinner events to iCloud "Calendar" from meal plan. Run: `open /Applications/WeeklyMealCalendar.app`
- See `release-notes.md` for details on implemented features
- See `backlog.md` for planned features

## SMS Assistant
- **Location**: `~/projects/personal/sms-assistant/`
- **Purpose**: Allows texting the assistant from a phone to query meal plans, recipes, and inventory
- **Current transport**: Twilio WhatsApp sandbox (personal use only, awaiting toll-free SMS verification)
- **How it works**: FastAPI server + ngrok tunnel + Claude API. Reads the same cooking context files (meal plans, recipe_metadata.json, inventory.md) as this project. Read-only via SMS.
- **To start**: `cd ~/projects/personal/sms-assistant && ./start.sh`
- **Guardrails**: Phone number whitelist in `config/settings.yaml`. System prompt in `system_prompts/menu.txt`. Neither can be changed via SMS.
- **Note**: The SMS assistant can accept and save recipe feedback (writes to `mealplan_YYYY-MM-DD_feedback.json`). All meal plan generation, recipe creation, and other data updates still happen through desktop Claude sessions in this project.

## Meal Plan Generation Rules

### Shopping List
- **Use JSON ingredients first**: If a recipe has an `ingredients` array in `recipe_metadata.json`, use that -- do NOT read the PDF.
- **Fall back to PDF only** if a recipe has no `ingredients` in JSON yet. After reading the PDF, add the ingredients to the JSON for next time.
- **Write to CSV only** (`shopping_YYYY-MM-DD.csv`) -- do NOT append to the meal plan txt
- Aggregate shared ingredients across recipes (e.g., total lemons, total chicken broth)
- Flag when inventory items may not cover recipe quantities (e.g., short ribs recipe needs 5 lbs but only 2 ribs in stock)

### Recipe Links
- **ALWAYS include full Dropbox HTTP URLs** in meal plans for each recipe
- Format: `https://www.dropbox.com/home/LLMContext/cooking/recipes?preview=Filename.pdf`

### Recipe Processing
- When converting recipes from recipeideas to recipes folder, **delete the source file from recipeideas** after the standardized PDF is created
- Verify recipe times against actual PDF content -- metadata times are sometimes wrong

### Time Accuracy
- Always verify cook times from the actual recipe PDF, not from the metadata (some entries are inaccurate)
- Note marinating time separately (e.g., "1 hour plus 1 hour marinating")
- Recipes over 60 minutes should be classified as Weekend meal_type

## Menu Generation Workflow
1. **Log last week's meals** -- read `mealplan_YYYY-MM-DD_feedback.json` for the previous week first. For each meal with feedback entries, auto-update `times_cooked`, `last_cooked_date`, and append entries to the `feedback` array in `recipe_metadata.json`. For meals with no feedback, prompt: "Any feedback on [recipe]? Did you make it?" If all adults disliked a recipe (adult_score = 0), flag for tombstone discussion -- do not auto-delete. If disliked and confirmed: delete PDF, set `status: "disliked"` in JSON.
2. **Check schedule** -- ask about any one-off changes this week (standing constraints are in `family-schedule.md`)
3. **Check recipeideas** -- if the folder is empty or hasn't had new files in 2+ weeks, suggest the user run recipe idea agents. Do NOT auto-spawn them.
4. **Run candidate filter** -- `python3 ~/projects/personal/MenuBuilder/suggest_meals.py`. Pass the week's busy nights (e.g. `--quick mon,tue,thu`). The script filters by `last_cooked_date`, health balance, protein variety, cuisine variety, and seasonal method. Use its output as the candidate pool -- do not re-scan the JSON manually.
5. **Propose recipe names only** -- let user approve/swap before generating
6. **Generate meal plan** -- save `mealplan_YYYY-MM-DD.txt` (minimal format) + `shopping_YYYY-MM-DD.csv`
7. **Run apps** -- `open /Applications/WeeklyShoppingList.app` then `open /Applications/WeeklyMealCalendar.app`

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
