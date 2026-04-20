# MenuBuilder - Backlog

## Planned Features

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

### WeeklyShoppingList.app - Grouped Reminders by Category
- **Goal**: Group shopping list items by category in the Grocery list
- **What we learned**: iOS Reminders auto-groups items in a Grocery-type list, but only when added via iOS UI — not via AppleScript. Groups also only appear on iPhone, not macOS desktop. AppleScript can't create groups or trigger auto-categorization.
- **Current workaround**: On iPhone, toggle list type Standard → save → Groceries → save. This triggers auto-grouping of all existing items.
- **Mar 2026 progress**: Implemented clean item names in reminder title (ingredient only), qty + meal name in Notes, due date set per item. iOS should now be able to auto-categorize correctly since reminder titles are plain ingredient names. Toggle workaround still needed to trigger grouping. Testing in progress.

### Regenerate Blank PDFs
- `tinga_verde_recipe.pdf` — blank content, source is Cooking Con Claudia. User to provide URL or paste recipe.
- `pan_seared_broccolini.pdf` — blank content, source unknown. User to provide recipe.

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

### Consolidate meal plan to JSON format
- Currently meal plan is `.txt` (for WeeklyShoppingList.app and WeeklyMealCalendar.app) with a separate feedback JSON
- Goal: single `mealplan_YYYY-MM-DD.json` containing meals + feedback together
- Requires reworking both apps to read JSON instead of txt
