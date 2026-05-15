# MenuBuilder - Backlog

## Planned Features

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
- **Chef agents** (cross-cuisine, auto-tags cuisine at fetch time): Kenji Lopez-Alt, ATK, Serious Eats, etc.
- Chef agents classify dish cuisine from title/ingredients and tag accordingly in metadata
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

### Sunday Prep Plan
- After Ashley approves the menu, send two texts via Keanu: (1) final menu summary, (2) Sunday prep guide
- **Metadata schema** — add to each recipe in `recipe_metadata.json`:
  - `prep_components`: list of ingredients/components that can be prepped Sunday (e.g. `["garlic", "onion", "sauce"]`)
  - `prep_notes`: optional string for recipe-specific storage behavior (e.g. "sauce freezes well", "lime marinade — prep day-of only")
- **Design principle**: store only recipe-specific data; general food knowledge (shelf life of common ingredients, aggregation math) comes from Claude at plan-generation time
- **Aggregation**: at plan time, Claude sums quantities across all recipes using the same prepped ingredient (e.g. garlic in 3 recipes = dice it all at once) using the `ingredients` array already in JSON
- **Shelf life**: Claude applies general food knowledge defaults (diced garlic: 7 days, onion: 5 days, etc.); `prep_notes` overrides for recipe-specific exceptions
- **Text format** (v1 — iterate from here):
  ```
  Sunday Prep:
  Mole (Thu): sauce, garlic, onion — [dropbox link]
  Lemongrass Chicken (Mon): marinade, garlic — [dropbox link]
  Total garlic: 10 cloves
  ```
- **Metadata population**: add `prep_components` as recipes come up in weekly planning; no bulk backfill needed
- Build after Partner Menu Feedback Loop is complete (prep text is step 2 of the post-approval flow)

### Partner Menu Feedback Loop via Keanu
- MenuBuilder sends the weekly plan to Partner via Keanu's outbox (new `send_menu_partner.py` script, run at step 6)
- Keanu checks for `/Users/Shared/sms-assistant/menu_feedback_pending.json`; if present, captures Partner's reply to `/Users/Shared/cooking/menu_feedback_response.json` and texts User a notification
- MenuBuilder polls for the response file, displays feedback, then applies it to the plan
- Keanu sends Partner a confirmation reply ("Thanks, passed it on!")
- Once the menu is confirmed, MenuBuilder deletes the pending file — Keanu reverts to normal routing
- **Design notes**: Pending file is the on/off switch (MenuBuilder owns its lifecycle). No timeout — killed explicitly on confirmation.


### Sunday Morning Auto-Generation (In Progress)
- launchd cron fires Sunday AM
- Sends iMessage asking about schedule changes for the week ("Any schedule changes? Nights out?")
- User replies via text; script feeds reply + context files (meal plans, inventory, metadata) into Claude API
- Claude proposes menu; user approves/swaps via text; final plan saved + shopping list populated
- **Transport**: iMessage via dedicated iCloud account (not Twilio)
- **Blocked on**: iCloud account setup -- attempting via old physical iPhone (browser setup had issues)
- **Next**: Once iCloud account is ready on device, build the AppleScript/Python iMessage sender + launchd job

### Structured Ingredient Database ✓ IN PROGRESS
- **Structure**: `ingredients` array in each recipe entry in `recipe_metadata.json`
- **Schema**: `[{"name": str, "quantity": str, "unit": str, "category": str}]`
- **Categories**: Proteins, Produce, Dairy, Pantry/Asian, Dry Goods, Spices/Herbs
- **Status**: 23/106 active recipes populated (Mar 2026). Populating incrementally as recipes are used in meal plans.
- **Workflow**: Use JSON ingredients for shopping list; fall back to PDF only if not yet populated, then add to JSON.

### WeeklyShoppingList.app - Reminder Time Defaults to 12am
- Reminders created with a due date have no time set, so iOS triggers them at midnight
- Fix: set a default reminder time (e.g. 8am) when creating each reminder in the app
- AppleScript: set the `remind me date` to the due date at 8:00am instead of midnight

### WeeklyShoppingList.app - Grouped Reminders by Category
- **Goal**: Group shopping list items by category in the Grocery list
- **What we learned**: iOS Reminders auto-groups items in a Grocery-type list, but only when added via iOS UI — not via AppleScript. Groups also only appear on iPhone, not macOS desktop. AppleScript can't create groups or trigger auto-categorization.
- **Current workaround**: On iPhone, toggle list type Standard → save → Groceries → save. This triggers auto-grouping of all existing items.
- **Mar 2026 progress**: Implemented clean item names in reminder title (ingredient only), qty + meal name in Notes, due date set per item. iOS should now be able to auto-categorize correctly since reminder titles are plain ingredient names. Toggle workaround still needed to trigger grouping. Testing in progress.

### Regenerate Blank PDFs
- `tinga_verde_recipe.pdf` — blank content, source is Cooking Con Claudia. User to provide URL or paste recipe.
- `pan_seared_broccolini.pdf` — blank content, source unknown. User to provide recipe.

### GitHub Pages Recipe Styling
- Current custom layout is functional but doesn't fully match the local `show_recipe.py` viewer
- Goal: match the local style — warm cream background, card shadow, meta row (time, source link, health badge), Georgia serif body text
- Reference: `show_recipe.py` HTML_TEMPLATE is the target design
- Consider pulling health/time/source from a data file at build time so badges render on the web version too

### Recipe Verbatim Scan
- Scan all recipe `.md` files to verify steps are reworded/reformatted and not copied verbatim from source
- Attribution policy: recipes must be adapted, not direct copies — original source link included for anyone wanting exact wording
- 98 `.md` files to check; ATK and other paywalled sources are highest priority
- Can be done in batches; flag any that read as verbatim for manual rewrite

### CLAUDE.md Cleanup
- Fix remaining stale content in CLAUDE.md (deferred from Mar 2026 review)

### WeeklyMealCalendar.app Improvements
- Handle edge cases: recipes with no cook time, multi-component meals

### PDF-to-Markdown Migration Script
- Write a script to batch-convert all active recipe PDFs to `.md` files in the same `recipes/` folder
- Pull ingredients from JSON (already populated) rather than parsing PDFs, since PDFs are truncated
- Instructions will need to be extracted from PDFs or sourced manually where PDFs are truncated
- After migration: update `recipe_metadata.json` `filename` field from `.pdf` to `.md` for each recipe
- Update meal plan URL format from `?preview=Filename.pdf` to `?preview=Filename.md`
- Delete PDFs once Markdown versions are verified

### Replace PDF Recipe Storage with a Better Format
- PDFs are cumbersome to generate (requires fpdf), can't be read by Claude for SMS/text queries, and aren't easily diffable or editable
- **Goal**: migrate recipes to a plain-text or structured format that Claude can read directly and that works on mobile
- **Candidates**: Markdown (`.md`) per recipe, or a single structured JSON with full recipe content alongside metadata
- Markdown is the likely winner — human-readable, Dropbox-accessible on phone, Claude can read it natively via SMS context
- **Migration**: write a script to extract content from existing PDFs into Markdown files; update WeeklyMealCalendar.app and WeeklyShoppingList.app if recipe links change
- **Dropbox links**: update meal plan URL format from `?preview=Filename.pdf` to the equivalent Markdown file

### Recipe Ideas Architecture
- **recipeideas/ folder = external inbox**: SMS and other outside apps write here only. No external app writes directly to `recipe_metadata.json`.
- **recipe_metadata.json = desktop only**: promotion from idea → JSON is always a manual desktop Claude session.
- Add `status: "ignored"` flag to JSON for ideas we've decided not to pursue (in addition to `"disliked"` for tried-and-didn't-like).
- Status lifecycle: `idea` (in JSON, not yet tried) → `active` (tried and liked) | `disliked` (tried, didn't like) | `ignored` (decided to skip)
- **SMS idea submission**: when someone texts in a recipe idea, the SMS assistant saves it to `recipeideas/` as a file. The desktop workflow picks it up and promotes it to the JSON manually.
- **Human in the loop**: adding an idea to the JSON is always manual — user pastes the recipe, Claude populates the entry.
- **TODO**: implement SMS idea submission in the sms-assistant (currently not built).
- **Future enhancement**: SMS assistant detects duplicates or similar recipes at submission time (before writing to recipeideas/), warns the sender if a close match already exists in the JSON.

### Recipe Viewer Enhancements (show_recipe.py)
- **In-page feedback**: thumbs up/down voting + freeform notes field rendered in the HTML page; on submit, writes to `feedback_current.json` so it flows into the Sunday logging workflow
- **SMS recipe display**: text a recipe name to Keanu, get back a formatted recipe (ingredients + steps); would use the same JSON-first lookup as show_recipe.py
- **Stale header cleanup**: batch-remove `Source:`, `Time:`, `Yield:` header blocks from older `.md` files that still have them baked in (those fields now live in JSON)

### Display Recipe in Chat (Desktop)
- When on desktop, user should be able to ask for a recipe and have it printed inline in the chat
- Claude reads the `.md` file directly and renders it — no need to copy/paste Dropbox links
- Workflow: look up recipe filename from `recipe_metadata.json`, read from `recipes/`, render in chat
- This is now standard behavior; backlog item is to document it in CLAUDE.md as the default desktop pattern

### Meal Swap Handling
- Support swapping a planned meal mid-week (e.g. grilling instead of the scheduled dinner)
- Involves: updating the meal plan txt, updating the calendar event via WeeklyMealCalendar.app, and logging the uncooked meal so it's not counted at Sunday feedback
- Need to think through calendar update flow since WeeklyMealCalendar.app rewrites all events on each run

### Consolidate meal plan to JSON format
- Currently meal plan is `.txt` (for WeeklyShoppingList.app and WeeklyMealCalendar.app) with a separate feedback JSON
- Goal: single `mealplan_YYYY-MM-DD.json` containing meals + feedback together
- Requires reworking both apps to read JSON instead of txt
