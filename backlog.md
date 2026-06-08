# MenuBuilder - Backlog

## In Progress

### Sunday Morning Auto-Generation
- launchd cron fires Sunday AM
- Sends iMessage asking about schedule changes for the week ("Any schedule changes? Nights out?")
- User replies via text; script feeds reply + context files (meal plans, inventory, metadata) into Claude API
- Claude proposes menu; user approves/swaps via text; final plan saved + shopping list populated
- **Transport**: iMessage via dedicated iCloud account
- **Dual-mode**: primary interaction via SMS; user may also pick up workflow on laptop (Claude Code). Session state at `/Users/Shared/cooking/menu_session.json` is the bridge.
- **launchd plist created**: `~/Library/LaunchAgents/com.menubuilder.sundaymenu.plist` — 9 AM Sunday
- **sms-assistant changes complete** (Jun 4 2026) — trigger_menu.py, handle_start, _handle_meal_logging, dispatch rewrite, advance_to_meal_approval seam all done
- **Test**: Sunday Jun 8 — first live run. Permission dialogs may appear on first launchd fire.

---

## Planned Features

### Simplify Recipe Status Model — Eliminate idea/active Split
**Decision (Jun 7 2026)**: `status: "idea"` and `status: "active"` are redundant once every JSON entry has `ingredients_raw`, `instructions`, and a `.md` file at intake. `times_cooked` already captures "new vs established." Status only needs to exist for out-of-rotation recipes.

**Target model**:
- No `status` field (or `status: "active"`) = in rotation
- `status: "disliked"` = tried, didn't like
- `status: "ignored"` = skipped without trying
- `times_cooked: 0` = never tried (the "new recipe" signal for weekly planning)
- `needs_review: true` = auto-created .md may have poor formatting; review before first cook

**Changes required**:
- `fill_menu_ideas.py`: create `.md` file at write time; add quality check → set `needs_review: true` if content is poor (< 3 steps, steps < 30 chars, < 4 ingredients, HTML artifacts). Add warning banner to flagged .md files.
- `suggest_meals.py`: change filter from `status == "active"` to `status not in ["disliked", "ignored"]`
- `menu_server.py` / menu workflow: remove step 6b (activation); remove `activate_idea_recipe` MCP tool
- `recipe_metadata.json`: existing `status: "idea"` entries → migrate to no status or `"active"` after backfilling missing ingredients/instructions (see cleanup task below)
- GitHub Pages: push new .md files after each replenish run

**Cleanup task**: audit existing `status: "idea"` entries — backfill `ingredients_raw` + `instructions` from source URLs, then migrate. Entries that can't be fetched → remove or flag.

### Shopping CSV — Move Generation to MenuBuilder
**MenuBuilder side DONE Jun 8 2026**:
- `generate_shopping_list` MCP tool added to `mcp/menu_server.py`
- `_build_shopping_csv` updated with `ingredients_raw` fallback (was missing)
- Tool writes `shopping_{week_start}.csv` and returns path + row count

**sms-assistant handoff** — see below; not yet done.
- sms-assistant should call `generate_shopping_list` instead of its own CSV logic
- **Owner (remaining)**: sms-assistant

### fill_menu_ideas.py — Capture Full Recipe Metadata at Intake
- **Partially fixed Jun 7 2026**:
  - `fill_menu_ideas.py` now stores `ingredients_raw` (list of raw strings), `instructions`, and `url` alias on every new idea entry
  - `indian_agent.py` `real_ingredients` filter fixed: `isdigit()` → `isnumeric()` so Unicode fractions (½, ¾, ⅓) in Indian recipe measurements are no longer dropped, which caused the agent to return 0 results
  - CLAUDE.md updated: shopping list now falls back to `ingredients_raw` for idea recipes before trying to read .md file
- **Remaining**: structured `ingredients` array (name/quantity/unit/category) still not populated at intake — ideas have `ingredients_raw` (strings) only. Structured parsing happens at activation (step 6b). If this causes shopping list issues for activated-but-not-yet-structured recipes, add a Haiku parsing step to fill_menu_ideas.py.
- Goal: ideas should be activation-ready — no manual URL lookup or copy-paste required

### Meal Logging: First-Cook Feedback Only (sms-assistant handoff)
- **Decision (Jun 8 2026)**: feedback prompting only applies to first-cook recipes (`times_cooked == 0`). Established recipes auto-log as cooked with no prompting.
- **Rules**:
  - `not_cooked` in feedback_current.json → skip logging
  - `disliked` in feedback_current.json → tombstone flow
  - First-cook + no feedback entry → ask "You tried [recipe] for the first time — keep it in rotation?"
  - Established recipe + no feedback entry → auto-log silently
- **Claude Code side**: CLAUDE.md step 1 updated Jun 8 2026
- **sms-assistant side**: `_handle_meal_logging` in `menu_workflow.py` needs the same logic. Currently prompts for every meal without a feedback entry. Fix: check `times_cooked` from `recipe_metadata.json` before deciding whether to ask.
- **Owner**: sms-assistant (`menu_workflow.py`)

### Menu Balance Enforcement at Generation Time
- SMS workflow allowed 3 Indulgent meals in one week (Jun 8 2026) — over the 1/week guideline
- **Diagnosed Jun 8 2026** — two bugs in `menu_workflow.py`:

**Bug A — Indulgent cap missing** (`_select_meals`, line ~349)
- `heart_healthy_count` is tracked but there is no `indulgent_count`
- Fix: in `_select_meals`, add `indulgent_count = 0` alongside `heart_healthy_count`
- In `pick_for`, add `nonlocal indulgent_count`
- Before selecting a candidate, add: `if c["health"] == "Indulgent" and indulgent_count >= 1: continue`
- After selecting, add: `if c["health"] == "Indulgent": indulgent_count += 1`

**Bug B — Week date display off by one** (`_build_plan_text`, line ~460)
- Plan showed "June 07–13" when the week started June 8
- `_build_plan_text` derives display Sunday as `week_start - 1 day` — correct if `week_start` is Monday
- Root cause: the session stored `week_start = "2026-06-08"` (Sunday) instead of `"2026-06-09"` (Monday)
- Look for any code path (e.g. `trigger_menu.py`, session init) that sets `week_start` to `date.today()` directly instead of calling `_get_week_start()`
- `_get_week_start()` itself is correct; the bug is in how `week_start` gets written to the session

- **Owner**: sms-assistant (`_select_meals` and session init in `menu_workflow.py` / `trigger_menu.py`)

### Idea Activation: Handle Missing URL
- When activating an idea recipe (status: "idea" → "active"), if `url` is empty in `recipe_metadata.json`, Keanu currently gets confused and the user has to copy-paste the recipe manually
- Fix: during activation, check for empty URL first — if missing, ask "What's the source URL for [recipe]?" before attempting to fetch
- Applies in both the SMS workflow (handle_ashley_reply idea activation path) and any direct activation call
- **Owner**: sms-assistant (activation logic in menu_workflow.py) + MenuBuilder (activate_idea_recipe MCP tool)

### Cuisine Direction: Accept Source Labels
- When user provides a cuisine direction hint via SMS (e.g. "Serious Eats"), map it to a `cuisine_type` tag before passing to candidate scoring
- "Serious Eats" → `"American"`; build a small alias map in `menu_server.py`
- Fallback: if no alias found, surface a prompt: "I don't recognize '[label]' as a cuisine — did you mean [closest match]?"
- Unblocks using source names as shorthand for cuisine preferences

### Recipe Site MCP Servers ⬅ NEXT
- Build MCP servers for frequently used recipe sites (ATK, Serious Eats, etc.) to avoid fetching raw HTML in context
- MCP fetches page with auth cookies, parses and returns structured recipe data (title, ingredients, instructions only)
- Saves tokens vs. Claude fetching directly; more reliable than WebFetch on paywalled sites
- Consider local cache: pull once, store, Claude reads from cache thereafter
- ATK requires session cookie auth (paid subscription)
- Goal: use MCP instead of web fetch when looking up recipe ideas during meal planning

### Create Missing Recipe Files
- **Tinga Verde** — blank PDF, source is Cooking Con Claudia. Provide URL or paste recipe to create `.md` file.

### PDF-to-Markdown Migration
**Status**: 1 PDF remaining (down from 28). Pan-Seared Broccolini complete Jun 2026.

- `tinga_verde_recipe.pdf` — image-based scan; need source URL or content to create `.md` manually

### Recipe Verbatim Scan
**Status**: ATK .md files complete Jun 2026. 15 recipes rewritten, 6 formatting bugs fixed, 2 duplicates removed.
- Remaining: 12 ATK recipes still in PDF format (can't scan until converted); non-ATK sources not yet checked

### Recipe Agents for All MenuBuilder Sources
- **Cuisine agents** (source-specific): Mexican (done), Asian/East+Southeast (done — Japanese, Korean, Thai, Vietnamese, Chinese), Indian (done — Jun 2026, its own agent separate from Asian), Italian, etc.
- **Indian agent** (`indian_agent.py`) — BUILT Jun 2026. Sources: Indian Healthy Recipes, Hebbars Kitchen, Chetna Makan (two HTML patterns handled), Kannamma Cooks (South Indian). Symlinked as `~/.local/bin/indian`. Results to `/tmp/indian_agent_results_{uid}.json`.
- **indianhealthyrecipes.com instructions** — ld+json only exposes step 1 in `recipeInstructions`. Full instructions are in WPRM HTML: `.wprm-recipe-instruction-text` elements (confirmed Jun 7 2026 via direct scrape). Need to add WPRM HTML fallback to `fetch_recipe()` in `indian_agent.py` for IHR URLs. Same pattern likely applies to other WPRM-based sites.
- **ranveerbrar.com** — debug needed: WP REST search works, but `recipeIngredient` in ld+json only contains section header labels (e.g. `["Cereal & Pulses"]`), not actual ingredients. Need to find alternate extraction — either WPRM HTML div or scraping the ingredient list from the page body.
- **archanaskitchen.com** — debug needed: all tested recipe URLs return 404. May have changed URL structure or be behind Cloudflare. Try searching site directly and following links to find current URL pattern.
- **Chef agent** (`chef_agent.py`) — done: Alton Brown, Deb Perelman (Smitten Kitchen), Chetna Makan. Symlinked as `~/.local/bin/chef`. Results to `/tmp/chef_agent_results.json`.
- **Sites agent** (`sites_agent.py`) — BUILT. Serious Eats live via Playwright (bypasses 403). Symlinked as `~/.local/bin/sites`. Registry-based: add a new site = one dict entry in SITES.
- **Kenji Lopez-Alt** — blocked: seriouseats.com returns 403. Need alternate source (his Substack, YouTube, or wait for Serious Eats solution).
- **ATK / America's Test Kitchen** — blocked: paywall, needs session cookie auth.
- **Chetna Makan YouTube** (nice to have): attach YouTube video link to recipes fetched from chetnamakan.co.uk. Channel: `UC1VkNUPA6ieOuwXmk4SSJZw`.
- Goal: full recipe discovery pipeline — any source accessible via agent, no manual URL fetching

### Cuisine Agents — Idea Pool Replenishment (between cycles)
- **DONE Jun 8 2026**: renamed to `fill_menu_ideas.py`; skill `/fillmenuideas`; plist updated to `com.menubuilder.fillmenuideas`; README, CLAUDE.md, backlog updated.

**Decision**: agents run **outside** the weekly workflow — not inline. Reason: token cost, latency (2-4 min per agent), and raw results lack the metadata (health, cook time, ingredients) needed to score them against weekly criteria. `suggest_meals.py` operates on `recipe_metadata.json`; agents feed the JSON, they don't replace it.

**Pattern**: run `fill_menu_ideas.py` on-demand between cycles when the idea pool needs refreshing. It runs all agents in parallel, deduplicates against existing entries, classifies health via Claude Haiku, and writes new entries as `status: "idea"`.

**`fill_menu_ideas.py`** — BUILT Jun 2026.
- `python3 fill_menu_ideas.py` — all agents, default topics
- `python3 fill_menu_ideas.py --agents indian,mexican` — specific agents
- `python3 fill_menu_ideas.py --topic "fish dinner"` — override query for all agents
- `python3 fill_menu_ideas.py --dry-run` — preview without writing
- Deduplicates by URL and title; classifies health in one Haiku batch call; infers meal_type and cooking_method from recipe data

**Weekly cron** — `com.menubuilder.fillmenuideas.plist` loaded Jun 2026. Fires Saturday 10 AM (ideas ready before Sunday planning). Log: `fill_menu_ideas.log` in project root. Review/unload after ~40 weeks (around Apr 2027) once idea pool is stable.

**One enhancement to build**: at step 3, after `suggest_meals.py` runs, if the candidate pool is thin (< 5 options) or light on a cuisine, surface a prompt: "idea pool is light on [cuisine] — want me to run an agent before proposing?" Keeps agents available without running them by default.

### SMS Recipe Display (show_recipe via Keanu)
- Text a recipe name to Keanu, get back formatted ingredients + steps
- Uses same JSON-first lookup as `show_recipe.py`

### Local Recipe Browser (Web UI)
- Simple local Flask server with a search box for browsing and viewing the existing recipe collection
- Reads from `recipe_metadata.json` for search/filter; renders `.md` files as styled HTML (same as `show_recipe.py`)
- Zero API cost — no LLM calls needed for browse/view
- Replaces `show_recipe.py` for desktop use; search box replaces having to know the exact recipe name
- Filter ideas: cuisine, meal type, health, cook time, last cooked

### RAG for Recipe Collection (Learning/Experiment)
- Build a local RAG pipeline over the recipe `.md` files for semantic search and experimentation
- **Stack**: `sentence-transformers` (local embeddings) + ChromaDB (local vector store, Python-native)
- **Example queries**: "find something similar to Korean Chicken Bulgogi but Italian", "weeknight fish that isn't salmon"
- **Why not yet**: collection fits in a single context window; `suggest_meals.py` handles structured filtering. Learning project, not a workflow gap.

### WeeklyShoppingList.app - Completed-Item Skip Window Too Wide
- **Bug**: 7-day completed-item skip window causes items to be silently dropped when the same ingredient appears on consecutive weekly lists (e.g. parsley, mushrooms completed last week → skipped this week). Both items ARE in the CSV; the drop happens at the Reminders import layer.
- **Attempted fix (Jun 8 2026)**: Delete all `[menu]` items (completed + uncompleted) before rebuild, remove skip check entirely. Reverted — trade-off is too bad: re-running mid-week for a swap clears all completed state.
- **Better fix needed**: Narrow the skip window to items completed within the last ~24 hours (handles same-day re-runs), or tie it to the meal plan start date so only items completed since this plan was generated are skipped.
- **File**: `/Applications/WeeklyShoppingList.app/Contents/Resources/Scripts/main.scpt`; backup at `/tmp/WeeklyShoppingList_backup.applescript`

### WeeklyShoppingList.app - Grouped Reminders by Category
- **Known limitation**: iOS Reminders auto-groups items only when added via iOS UI — AppleScript bypasses NLP categorization entirely. No API path available.
- **Current workaround**: On iPhone, toggle list type Standard → save → Groceries → save. Triggers auto-grouping.
- **macOS 16 (Tahoe) Jun 2026**: No change — SDEF confirms `list` class still exposes no list-type property. Revisit on future major releases.

### WeeklyMealCalendar.app Improvements
- Handle edge cases: recipes with no cook time, multi-component meals

### Meal Swap Handling
- Support swapping a planned meal mid-week
- Involves: updating the meal plan txt, updating the calendar event via WeeklyMealCalendar.app, logging the uncooked meal so it's not counted at Sunday feedback
- Calendar update flow needs design: WeeklyMealCalendar.app rewrites all events on each run

### Consolidate Meal Plan to JSON Format
- Currently meal plan is `.txt` (for WeeklyShoppingList.app and WeeklyMealCalendar.app) with a separate feedback JSON
- Goal: single `mealplan_YYYY-MM-DD.json` containing meals + feedback together
- Requires reworking both apps to read JSON instead of txt

### Meal Costing (Long-Term)
- Once price history accumulates, cost recipes using `ingredients` array + price-per-unit averages from `price_history.json`
- MenuBuilder will skip entries missing `price_per_unit` gracefully
- **Owner**: GroceryAgent pipeline feeds the data; MenuBuilder is the consumer
