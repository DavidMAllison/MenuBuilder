# MenuBuilder - Completed Features Archive

Items moved here when shipped. Kept for reference.

---

### Grocery Budget Tracking + Meal Costing
**Completed**: May 30, 2026

- Receipt logging: text photo to Keanu → `groceryagent_bridge.py` → `receipt_parser.py` → updates budget + price history
- Monthly grocery budget tracked in `~/Dropbox/LLMContext/Personal/grocery_budget_status.json` — $800/month
- Price history tracked in `~/Dropbox/LLMContext/Personal/price_history.json`
- Budget mode (default): step 4 of weekly workflow reads budget; if tight, prioritize pantry-heavy meals
- Yolo mode: explicit override ("yolo this week") — no budget constraints
- `price_per_unit` added May 31: `update_price_history()` stores `quantity_value`, `quantity_unit`, `price_per_unit` on every new entry
- **Owner**: `~/projects/personal/GroceryAgent/`

---

### Expanded Inventory Tracking
**Completed**: May 31, 2026

- `suggest_meals.py` matches Pantry, Dry Goods, and Dairy inventory against recipe ingredients
- Shows `[PANTRY: item1, item2]` tags in candidate output; scores -4 for pantry matches
- Header shows "IN STOCK (pantry/dairy)" summary line
- `show_inventory.py` added — opens browser with full inventory grouped by category

---

### SMS Inventory Query (check_inventory tool)
**Completed**: May 31, 2026

- `check_inventory` tool added to Keanu — "do we have X?" works over iMessage for all family members
- Fixed `INVENTORY_FILE` path in `tools.py` (was pointing to archived `inventory.md`, now `inventory.json`)
- Fixed `_tool_update_inventory()` rewritten for JSON format

---

### Workflow Fix: Promote Idea Recipes After Ashley Approves (Not Before)
**Completed**: May 31, 2026

- CLAUDE.md step 6 now explicitly gates recipe creation on Ashley's approval
- Send meal names only at step 6 — no URL fetching for `idea` meals until Ashley signs off
- If Ashley swaps out an idea meal, skip its file creation entirely

---

### WeeklyShoppingList.app: Date Overflow Fix
**Completed**: May 31, 2026

- Fixed AppleScript date overflow when setting month on a date holding day > days-in-target-month
- Fix: reset day to 1, set month, then set real day

---

### WeeklyMealCalendar + WeeklyShoppingList: Launch App Before Scripting
**Completed**: May 31, 2026

- Both apps now activate Reminders/Calendar before scripting them
- Prevents "Application isn't running" (-600) errors

---

### WeeklyShoppingList.app: Preserve Manually-Added Reminders
**Completed**: May 31, 2026

- Script now stamps generated reminders with `[menu]` prefix in body
- Only deletes `[menu]`-tagged items on next run; manually-added items are untouched

---

### GitHub Pages Recipe Styling
**Completed**: June 2026

- `_data/recipes.json` generated from `recipe_metadata.json` (127 recipes) via `generate_github_pages_data.py`
- `_layouts/default.html` updated: meta row, health badges (color-coded), source links, cuisine, time
- Handles dual metadata schema (`health`/`health_classification`, `cuisine`/`cuisine_type`)
- Run `generate_github_pages_data.py` + push after metadata changes

---

### Recipe Viewer: Stale Header Cleanup
**Completed**: June 2026

- Audited all `.md` files for stale pipe-separated `Source/Time/Servings` headers
- Only 1 file had a genuine stale header (Thai Chicken Stir-Fry) — fixed + attribution added
- All other files with `**Time:**` and `**Servings:**` confirmed as current standard format

---

### PDF-to-Markdown Migration (Partial)
**Completed**: May 2026 (26 of 28 PDFs converted)

- 26 PDF recipes converted to `.md` format and pushed to GitHub Pages
- 2 remaining (`pan_seared_broccolini.pdf`, `tinga_verde_recipe.pdf`) are image-based scans — blocked on source content
- Tracked in active backlog under "Create Missing Recipe Files"
