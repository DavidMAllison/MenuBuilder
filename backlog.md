# MenuBuilder - Backlog

## Planned Features

### Grocery Budget Tracking + Meal Costing
**Status**: COMPLETE (May 30, 2026). Full receipt → budget → MenuBuilder pipeline live.

**Owner**: `~/projects/personal/GroceryAgent/` — MenuBuilder is a consumer, not the owner.

- **Receipt logging**: text photo to Keanu → `groceryagent_bridge.py` → `receipt_parser.py` → updates budget + price history ✓
- **Monthly grocery budget** tracked in `~/Dropbox/LLMContext/Personal/grocery_budget_status.json` — $800/month ✓
- **Price history** tracked in `~/Dropbox/LLMContext/Personal/price_history.json` ✓
- **Budget mode** (default): step 4 of weekly workflow reads `grocery_budget_status.json`; if tight, prioritize pantry-heavy meals ✓
- **Yolo mode**: explicit override ("yolo this week") — no budget constraints ✓
- **price_per_unit in price_history** (May 31, 2026): `update_price_history()` now calls `_parse_quantity()` and stores `quantity_value`, `quantity_unit`, `price_per_unit` on every new entry ✓ (GroceryAgent)
- **Meal costing** (long-term): once price history accumulates, cost recipes using `ingredients` array + price-per-unit averages. MenuBuilder will skip entries missing `price_per_unit` gracefully.

**Receipt OCR output schema** — must include `price` per item from day one:
```json
{
  "store": "Kroger",
  "total": 127.43,
  "items": [
    {"name": "boneless chicken breast", "quantity": "3 lbs", "price": 8.97, "category": "Proteins"}
  ]
}
```

**Budget file fields**: `grocery_budget_monthly`, `grocery_spent_to_date`, `grocery_remaining`, `week_number`, `weeks_in_month`, `suggested_weekly_spend`, `as_of`
- New-month detection: if `as_of.month != today.month`, reset `grocery_spent_to_date` to new receipt amount (fresh start)
- Week number: auto-derive from date — `math.ceil(today.day / 7)`

**Inventory updates**: append parsed items to correct sections in `inventory.md` (Produce, Dairy, Pantry Staples)

**Expected OCR iteration areas**: Kroger item name abbreviations, produce PLU codes, multi-buy pricing, Costco receipt format differences

### Expanded Inventory Tracking
**Status**: COMPLETE (May 31, 2026).

- `suggest_meals.py` now matches Pantry, Dry Goods, and Dairy inventory items against recipe ingredients
- Shows `[PANTRY: item1, item2]` tags in candidate output
- Scores `-4` for pantry matches (stacks with protein bonuses)
- Header now shows "IN STOCK (pantry/dairy)" summary line
- `show_inventory.py` added — `python3 show_inventory.py` opens browser with full inventory grouped by category

### SMS Inventory Query (check_inventory tool)
**Status**: COMPLETE (May 31, 2026).

- `check_inventory` tool added to Keanu — "do we have X?" works over iMessage for all family members
- Fixed `INVENTORY_FILE` path in `tools.py` (was pointing to archived `inventory.md`, now `inventory.json`)
- Fixed `_tool_update_inventory()` rewritten for JSON format (markdown version was broken)

### Workflow Fix: Promote Idea Recipes After Ashley Approves (Not Before)
**Status**: FIXED May 31, 2026

Currently CLAUDE.md step 7b correctly gates recipe creation on Ashley's approval, but in practice the workflow can drift toward fetching/creating `.md` files before sending to her. This wastes work if she swaps out a meal.

**Fix**: Add an explicit reminder/gate in the workflow instructions:
- Step 6 (send to Ashley): send meal names only — do NOT fetch recipe content yet for any `idea` meals
- Step 7b (after Ashley replies): only then fetch URLs, create `.md` files, populate ingredients for ideas in the *final approved list*
- If Ashley swaps out an idea meal, skip its file creation entirely

Update CLAUDE.md to make this gate unmistakable — either a warning in step 6 or a reordering note.

---

### WeeklyShoppingList.app: Date Overflow When Current Day > Days in Target Month
**Status**: FIXED May 31, 2026

When today is the 31st and a shopping item is dated for a month with fewer days (e.g. June has 30 days), AppleScript overflows the date. Setting `month = 6` on a date still holding `day = 31` rolls over to July 1, then `day = 4` lands on July 4 instead of June 4.

**Fix**: In the date-setting block, reset the day to 1 before setting the month, then set the real day last:
```applescript
set day of dueDate to 1
set month of dueDate to (monthStr as integer)
set day of dueDate to (dayStr as integer)
```
Affects any month-end run where items are dated into the following month.

---

### WeeklyMealCalendar + WeeklyShoppingList: Launch Target App Before Scripting
**Status**: FIXED May 31, 2026

Both apps assume Reminders and Calendar are already running. If they're not, WeeklyMealCalendar fails with "Application isn't running" (-600) and WeeklyShoppingList gets connection errors.

**Fix for WeeklyMealCalendar.app**: Add `tell application "Calendar" to activate` + a short delay at the top of the script before any event operations.

**Fix for WeeklyShoppingList.app**: Add `tell application "Reminders" to activate` + a short delay at the top before accessing lists.

Both fixes are small one-line additions at the top of each script.

---

### WeeklyShoppingList.app: Preserve Manually-Added Reminders
**Status**: FIXED May 31, 2026

Current behavior: the script deletes ALL incomplete reminders from the Grocery list before recreating from CSV. This wipes any items Ashley or David manually added (e.g. non-meal items like snacks, household goods).

**Fix**: Track which reminders were CSV-generated vs manually added, and only replace the CSV-generated ones.

**Implementation options:**
- **Option A (preferred)**: Stamp each generated reminder with a note prefix or tag (e.g. body starts with `[menu]`) so the script can identify and selectively delete only those. Manually-added items have no tag and are left alone.
- **Option B**: Store the previous week's item names in a sidecar file; on next run, delete only names that appear in that file, then write the new names to it.

Option A is cleaner -- self-contained in the reminder itself, no extra file needed. Requires updating both the create and delete steps in the AppleScript.

---

### RAG for Recipe Collection (Learning/Experiment)
- Build a local RAG pipeline over the recipe `.md` files for semantic search and experimentation
- **Stack**: `sentence-transformers` (local embeddings, no API key needed) + ChromaDB (local vector store, Python-native)
- Roll own retrieval layer — no LangChain, more educational
- **Ingest script**: reads all `.md` files, chunks, embeds, stores in Chroma
- **Query function**: embeds question, retrieves top-N chunks, passes to Claude
- **Example queries**: "find something similar to Korean Chicken Bulgogi but Italian", "what can I make with sweet potatoes", "weeknight fish that isn't salmon"
- **Optional**: hook into SMS assistant for semantic recipe lookup
- **Why not yet**: collection (~100 recipes) fits in a single context window; `suggest_meals.py` handles structured filtering. This is a learning project, not a workflow gap.

### Local Recipe Browser (Web UI)
- Simple local Flask server with a search box for browsing and viewing the existing recipe collection
- Reads from `recipe_metadata.json` for search/filter; renders `.md` files as styled HTML (same as `show_recipe.py`)
- Zero API cost — no LLM calls needed for browse/view
- Replaces `show_recipe.py` for desktop use; search box replaces having to know the exact recipe name
- Keep agent-based recipe finding (mexican, etc.) as a separate console action — not in the browse UI
- Filter ideas: cuisine, meal type, health, cook time, last cooked

### Recipe Agents for All MenuBuilder Sources
- Build agents for every recipe source currently in the collection
- **Cuisine agents** (source-specific): Mexican (done — Pati Jinich, Rick Bayless, Cooking con Claudia), Italian, Asian, Indian, etc.
- **Chef agent** (`chef_agent.py`) — done: Alton Brown, Deb Perelman (Smitten Kitchen), Chetna Makan. Symlinked as `~/.local/bin/chef`. Results to `/tmp/chef_agent_results.json`.
- **Kenji Lopez-Alt** — blocked: seriouseats.com returns 403. Need alternate source (his Substack, YouTube, or wait for Serious Eats solution).
- **ATK / America's Test Kitchen** — blocked: paywall, needs session cookie auth.
- **Sites agent** (`sites_agent.py`) — BUILT. Serious Eats live via Playwright (bypasses 403). Symlinked as `~/.local/bin/sites`. Registry-based: add a new site = one dict entry in SITES. Results to `/tmp/sites_agent_results.json`.
- **Chetna Makan YouTube** (nice to have): attach YouTube video link to recipes fetched from chetnamakan.co.uk — videos don't have instructions but are good to watch alongside. Channel: `UC1VkNUPA6ieOuwXmk4SSJZw`. Would need to match video title to site recipe title.
- Goal: full recipe discovery pipeline — any source accessible via agent, no manual URL fetching

### Cuisine Agents as MenuBuilder Data Source
- MenuBuilder calls cuisine agents (mexican, italian, asian, etc.) instead of fetching recipe URLs directly
- Each agent knows its sites, handles fetching and saving; MenuBuilder just asks by cuisine + constraints (e.g. "weeknight chicken dish")
- Agents already exist for Mexican (patijinich.com); expand to other cuisines as sites are added
- End state: step 3 of the weekly workflow becomes "run cuisine agents to replenish ideas" rather than manual URL fetching

### Recipe Site MCP Servers ⬅ NEXT
- Build MCP servers for frequently used recipe sites (ATK, Serious Eats, etc.) to avoid fetching raw HTML in context
- MCP fetches page with auth cookies, parses and returns structured recipe data (title, ingredients, instructions only)
- Saves tokens vs. Claude fetching directly; more reliable than WebFetch on paywalled sites
- Consider local cache: pull once, store, Claude reads from cache thereafter
- ATK requires session cookie auth (paid subscription)
- Goal: use MCP instead of web fetch when looking up recipe ideas during meal planning

### Sunday Morning Auto-Generation (In Progress)
- launchd cron fires Sunday AM
- Sends iMessage asking about schedule changes for the week ("Any schedule changes? Nights out?")
- User replies via text; script feeds reply + context files (meal plans, inventory, metadata) into Claude API
- Claude proposes menu; user approves/swaps via text; final plan saved + shopping list populated
- **Transport**: iMessage via dedicated iCloud account
- **iCloud blocker resolved** (Jun 2026) — account setup complete
- **Dual-mode**: primary interaction via SMS; user may also pick up workflow on laptop (Claude Code). Session state at `/Users/Shared/cooking/menu_session.json` is the bridge.
- **launchd plist created**: `~/Library/LaunchAgents/com.menubuilder.sundaymenu.plist` — 9 AM Sunday
- **CLAUDE.md updated**: checks `get_workflow_state` before starting; resumes from active session
- **Handoff doc**: `handoff_sunday_sms_workflow.md` — all sms-assistant changes specified
- **sms-assistant changes complete** (Jun 4 2026) — trigger_menu.py, handle_start, _handle_meal_logging, dispatch rewrite, advance_to_meal_approval seam all done
- **Test**: Sunday Jun 8 — first live run. Permission dialogs may appear on first launchd fire.

### WeeklyShoppingList.app - Grouped Reminders by Category
- **Goal**: Group shopping list items by category in the Grocery list
- **What we learned**: iOS Reminders auto-groups items in a Grocery-type list, but only when added via iOS UI — not via AppleScript. Groups also only appear on iPhone, not macOS desktop. AppleScript can't create groups or trigger auto-categorization.
- **Current workaround**: On iPhone, toggle list type Standard → save → Groceries → save. This triggers auto-grouping of all existing items.
- **Mar 2026 progress**: Implemented clean item names in reminder title (ingredient only), qty + meal name in Notes, due date set per item. iOS should now be able to auto-categorize correctly since reminder titles are plain ingredient names. Toggle workaround still needed to trigger grouping. Testing in progress.
- **macOS 16 (Tahoe) investigation Jun 2026**: Reminders SDEF confirmed — `list` class exposes only `id`, `name`, `container`, `color`, `emblem`. No list type property added. Categorization is triggered client-side by the app's NLP on UI input only; API path bypasses it entirely. No change from earlier macOS versions. Revisit on future major releases.

### Create Missing Recipe Files
- **Tinga Verde** — blank PDF, source is Cooking Con Claudia. User to provide URL or paste recipe; create as `.md` file.
- **Pan-Seared Broccolini** — blank PDF, source unknown. User to provide recipe; create as `.md` file.

### GitHub Pages Recipe Styling
**Status**: COMPLETE Jun 2026

- `_data/recipes.json` generated from `recipe_metadata.json` (127 recipes) — run `generate_github_pages_data.py` whenever metadata changes
- `_layouts/default.html` updated: meta row from data file, health badges (color-coded), source links, cuisine, time — matches `show_recipe.py` local viewer
- Handles dual metadata schema (`health`/`health_classification`, `cuisine`/`cuisine_type`)
- Re-run `generate_github_pages_data.py` and push when new recipes are added

### Recipe Verbatim Scan
- Scan all recipe `.md` files to verify steps are reworded/reformatted and not copied verbatim from source
- Attribution policy: recipes must be adapted, not direct copies — original source link included for anyone wanting exact wording
- 98 `.md` files to check; ATK and other paywalled sources are highest priority
- Can be done in batches; flag any that read as verbatim for manual rewrite

### WeeklyMealCalendar.app Improvements
- Handle edge cases: recipes with no cook time, multi-component meals

### PDF-to-Markdown Migration
**Status**: NEARLY COMPLETE — only 2 PDFs remain (down from 28).

- `pan_seared_broccolini.pdf` and `tinga_verde_recipe.pdf` are image-based scans, not text PDFs — `convert_recipes_to_md.py` can't extract them
- **Blocker**: need source URLs or recipe content to create the `.md` files manually
- Once content is provided: create `.md`, push to GitHub repo, update metadata filename field, delete PDFs

### Recipe Viewer Enhancements (show_recipe.py)
- **SMS recipe display**: text a recipe name to Keanu, get back a formatted recipe (ingredients + steps); would use the same JSON-first lookup as show_recipe.py
- **Stale header cleanup**: COMPLETE Jun 2026 — only 1 file had a genuine stale header (Thai Chicken Stir-Fry, pipe-separated Source/Time/Servings block). All other files with `**Time:**` and `**Servings:**` are current standard format. Fixed + attribution added.

### Meal Swap Handling
- Support swapping a planned meal mid-week (e.g. grilling instead of the scheduled dinner)
- Involves: updating the meal plan txt, updating the calendar event via WeeklyMealCalendar.app, and logging the uncooked meal so it's not counted at Sunday feedback
- Need to think through calendar update flow since WeeklyMealCalendar.app rewrites all events on each run

### Consolidate meal plan to JSON format
- Currently meal plan is `.txt` (for WeeklyShoppingList.app and WeeklyMealCalendar.app) with a separate feedback JSON
- Goal: single `mealplan_YYYY-MM-DD.json` containing meals + feedback together
- Requires reworking both apps to read JSON instead of txt
