# MenuBuilder - Completed Features Archive

Items moved here when shipped. Kept for reference.

---

### Simplify Recipe Status Model — Eliminate idea/active Split
**Completed**: Jun 9, 2026

- `migrate_ideas_to_active.py`: one-shot migration script — created `.md` files for all 36 idea entries, fixed `source_url` for 3, flagged 8 with `needs_review=true`, set all to `status: "active"`. Re-runnable.
- `fill_menu_ideas.py`: now writes `.md` at intake and sets `status: "active"` directly. `_quality_check()` + `_build_recipe_md()` added. Low-quality content gets `needs_review=true` banner.
- `suggest_meals.py`: filter changed from `status == "active"` to `status not in ("disliked", "ignored")`. "FROM RECIPEIDEAS" section replaced with "NEEDS REVIEW" section.
- `menu_server.py`: candidate pool filters updated to use `times_cooked == 0` for "untried" signal. Step 6b (idea activation) removed from workflow. `activate_idea_recipe` tool kept for backward compat but no longer triggered in normal operation.
- `CLAUDE.md`: step 6b and 6a removed, all `status: "idea"` references updated.
- GitHub Pages: 33 new recipes added, 162 total. `generate_github_pages_data.py` run + pushed.
- New model: `status: "active"` = in rotation; `status: "disliked"` = tried/didn't like; `status: "ignored"` = skipped; `times_cooked: 0` = never tried; `needs_review: true` = verify .md before first cook.

---

### Menu Balance Enforcement at Generation Time
**Completed**: Jun 9, 2026 (sms-assistant `menu_workflow.py`)

- Bug A — Indulgent cap: added `indulgent_count` tracker in `_select_meals`; guard `if c["health"] == "Indulgent" and indulgent_count >= 1: continue` prevents more than 1 Indulgent meal per week.
- Bug B — week_start off-by-one: normalize block at line ~361 advances any non-Monday `week_start` to the next Monday before writing to session. `_get_week_start()` itself was always correct; fix was in how the MCP-returned date was consumed.

---

### Meal Logging: First-Cook Feedback Only
**Completed**: Jun 9, 2026 (sms-assistant `menu_workflow.py`)

- `_handle_start` partitions last week's meals into `first_cook_missing` (times_cooked == 0, no feedback) vs silently-skipped (established recipes with no feedback entry).
- Only first-cooks prompt "You tried [recipe] for the first time — keep it in rotation?"
- Established recipes auto-log without prompting.
- Rules: `not_cooked` in feedback → skip; `disliked` → tombstone flow; first-cook + no feedback → prompt; established + no feedback → auto-log.

---

### Idea Activation: Handle Missing URL
**Completed**: Jun 9, 2026 (sms-assistant `menu_workflow.py` + MenuBuilder `menu_server.py`)

- Pre-existing bug fixed: `pending_ideas[0]` was rendering a dict in the outbox message; `session["pending_idea"]` was never set so `_handle_idea_content` always activated with `name=""`.
- New behavior: if `source_url` is present but fetch failed → ask for paste (existing path, now fixed). If `source_url` is absent → ask "What's the source URL?" → try fetch → fallback to paste.
- New session state `awaiting_idea_url` added; `_handle_idea_url` handler added.
- MenuBuilder `activate_idea_recipe`: `content` made optional; if empty + `source_url` given, calls `_try_auto_activate`; returns `needs_content: true` if fetch fails.

---

### Shopping CSV — Move Generation to MenuBuilder
**Completed**: Jun 9, 2026

- `generate_shopping_list` MCP tool in `mcp/menu_server.py` writes `shopping_{week_start}.csv`, returns path + row count.
- `_build_shopping_csv` updated with `ingredients_raw` fallback for idea recipes.
- sms-assistant calls `generate_shopping_list` MCP tool instead of its own CSV logic.

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
