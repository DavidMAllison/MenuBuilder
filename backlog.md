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
- **Cuisine agents** (source-specific): Mexican (done), Asian (done), Indian (done — Jun 2026), Italian, etc.
- **Indian agent** (`indian_agent.py`) — BUILT Jun 2026. Sources: Indian Healthy Recipes, Hebbars Kitchen, Chetna Makan (two HTML patterns handled), Kannamma Cooks (South Indian). Symlinked as `~/.local/bin/indian`. Results to `/tmp/indian_agent_results_{uid}.json`.
  - Blocked sites: ranveerbrar.com (ld+json schema broken — ingredients are category headers only), archanaskitchen.com (all URLs 404)
- **Chef agent** (`chef_agent.py`) — done: Alton Brown, Deb Perelman (Smitten Kitchen), Chetna Makan. Symlinked as `~/.local/bin/chef`. Results to `/tmp/chef_agent_results.json`.
- **Sites agent** (`sites_agent.py`) — BUILT. Serious Eats live via Playwright (bypasses 403). Symlinked as `~/.local/bin/sites`. Registry-based: add a new site = one dict entry in SITES.
- **Kenji Lopez-Alt** — blocked: seriouseats.com returns 403. Need alternate source (his Substack, YouTube, or wait for Serious Eats solution).
- **ATK / America's Test Kitchen** — blocked: paywall, needs session cookie auth.
- **Chetna Makan YouTube** (nice to have): attach YouTube video link to recipes fetched from chetnamakan.co.uk. Channel: `UC1VkNUPA6ieOuwXmk4SSJZw`.
- Goal: full recipe discovery pipeline — any source accessible via agent, no manual URL fetching

### Cuisine Agents as MenuBuilder Data Source
- MenuBuilder calls cuisine agents (mexican, italian, asian, etc.) instead of fetching recipe URLs directly
- Each agent knows its sites, handles fetching and saving; MenuBuilder just asks by cuisine + constraints
- End state: step 3 of the weekly workflow becomes "run cuisine agents to replenish ideas" rather than manual URL fetching

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
