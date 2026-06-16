# MenuBuilder - Backlog

## Planned Features

### Perishable Herb/Produce Pairing
**Status**: COMPLETE Jun 15 2026.
- `garden_herbs: ["basil", "thyme", "rosemary"]` in `config.json` (update each spring/fall)
- Garden herb recipes get -4 score bonus in `suggest_meals.py` (free herb = slight preference) + `[GARDEN: X]` tag in output
- Shopping CSV (`_build_shopping_csv` in `menu_server.py`) skips garden herb ingredients — they never appear on the shopping list
- Purchased herb pairing note: if cilantro, mint, dill, parsley, tarragon, or chives appear in 3+ candidates, `suggest_meals.py` prints "FRESH HERB PAIRING" section at bottom — pick 2 recipes to use the bunch
- Garden herbs suppress the waste-pairing signal (excluded from bought_herbs check)

### Budget Display in suggest_meals.py
**Status**: COMPLETE Jun 15 2026. Prints `BUDGET: $X remaining / $X/week suggested` at top of output. Reads `grocery_budget_status.json`; silently skipped if file missing. No signal-boosting until inventory accuracy is validated.

### Weeknight Effort Classification
**Status**: COMPLETE Jun 15 2026.
- `weeknight_effort: low | medium | high` added to all 228 active recipes in `recipe_metadata.json`
- Classified by Haiku from `cooking_method`, `prep_components`, `time`, `instructions`
- `suggest_meals.py` shows `[LOW]` / `[MEDIUM]` / `[HIGH]` tags on every candidate
- `--quick` header now reads "Busy nights (prefer LOW/MED effort)" instead of a minute threshold
- `backfill_weeknight_effort.py` is re-runnable for new recipes
- **Feedback path (future)**: when user reports "that was harder than expected," update `weeknight_effort` via SMS/Keanu

### Cooking Notes Into Recipe Files
**Status**: COMPLETE Jun 15 2026. All 8 recipes with actionable notes have `## Notes` sections in their `.md` files. Add notes to new recipes as you cook them — no bulk process needed.

### Ingredient Name Normalization
**Status**: Won't do (Jun 15 2026). `_ING_ALIASES` in `mcp/menu_server.py` already handles the safe normalizations at query time. Remaining variants (fresh vs dried herbs, chopped vs diced tomatoes) are genuinely distinct products — bulk Haiku normalization would risk wrong merges. Fix ingredient names manually when noticed during cooking or shopping.



### Weekly Lunch Recommendation
**Status**: COMPLETE Jun 13 2026.
- Saturday 10 AM SMS → 3 suggestions → Ashley picks → ingredients added to shopping list dated Saturday
- Saturday 6 PM nudge if no reply
- sms-assistant: `set_lunch_pick` + `log_lunch_feedback` wired in `tools.py` + system prompt updated

### Lunch Calendar Event (Nice to Have)
- After `set_lunch_pick` is called, create one iCloud calendar event "Ashley's Lunch: [recipe]" recurring Sun–Fri
- Could be a small `lunch_calendar.py` AppleScript wrapper or an addition to WeeklyMealCalendar.app
- Not blocking anything — for reference visibility only

### ATK / America's Test Kitchen
**Status**: COMPLETE Jun 9 2026. `atk_agent.py` + `sync_atk_recipes` MCP tool.
- 3 collections synced (Try Out, Sunday Dinner, Dinners) + top-rated fallback
- 36 new recipes available to import; run `python3 atk_agent.py --target N` or `sync_atk_recipes` MCP tool
- Playwright auth cached in `config.json`; cookies refresh automatically after 20h

### Sunday Morning Auto-Generation
**Status**: COMPLETE Jun 4 2026. First live test Jun 8 2026.
- launchd plist at `~/Library/LaunchAgents/com.menubuilder.sundaymenu.plist` — 9 AM Sunday
- Full SMS workflow: feedback logging → schedule check → candidate filter → menu proposal → Ashley approval → finalize

### Recipe Site MCP Servers
- ATK: DONE (Jun 9 2026 — `sync_atk_recipes` MCP tool)
- Serious Eats: blocked by Cloudflare (403 on both httpx and headless Playwright as of Jun 2026)
- Other sites: add to `sites_agent.py` registry as needed

### PDF-to-Markdown Migration
**Status**: Complete. All PDFs converted to .md (Jun 2026).

### Recipe Verbatim Scan
**Status**: ATK .md files complete Jun 2026. 15 recipes rewritten, 6 formatting bugs fixed, 2 duplicates removed.
- PDF filenames in metadata: fixed Jun 9 2026 (18 stale `.pdf` → `.md` references updated)
- No verbatim ATK boilerplate found in previously-PDF recipes
- Non-ATK sources: not yet checked

### ATK Recipe Attribution ⬅ IN PROGRESS
- 33 ATK recipes missing "Adapted from" footer with source URL
- `needatklinks.md` in project root — fill in ATK URLs, then hand back to Claude to apply
- Once complete: add `*Adapted from [America's Test Kitchen](url)*` footer to each .md file

### Recipe Agents for All MenuBuilder Sources
- **Cuisine agents** (source-specific): Mexican (done), Asian/East+Southeast (done — Japanese, Korean, Thai, Vietnamese, Chinese), Indian (done — Jun 2026, its own agent separate from Asian), Italian, etc.
- **Indian agent** (`indian_agent.py`) — BUILT Jun 2026. Sources: Indian Healthy Recipes, Hebbars Kitchen, Chetna Makan (two HTML patterns handled), Kannamma Cooks (South Indian), Ranveer Brar, Archana's Kitchen. Symlinked as `~/.local/bin/indian`. Results to `/tmp/indian_agent_results_{uid}.json`.
- **indianhealthyrecipes.com instructions** — FIXED Jun 8 2026. Root cause: IHR uses `HowToSection` grouping in ld+json (not flat `HowToStep`); code now handles nested `itemListElement`. WPRM HTML fallback added as safety net. `html.unescape()` added for entity cleaning.
- **ranveerbrar.com** — FIXED Jun 8 2026. Search via sitemap (~1576 URLs across two sitemap files). ld+json ingredients are category labels — replaced with HTML extraction from `ingredients_cont_wrap` div. Hindi translations stripped from ingredients and title.
- **archanaskitchen.com** — FIXED Jun 8 2026. URL pattern changed to `/recipe/slug` (old flat `/slug` pattern returns 404). Search via sitemap (~6779 recipe URLs); Hindi/Tamil duplicate variants filtered. ld+json works cleanly for both ingredients and instructions.
- **Chef agent** (`chef_agent.py`) — done: Alton Brown, Deb Perelman (Smitten Kitchen), Chetna Makan. Symlinked as `~/.local/bin/chef`. Results to `/tmp/chef_agent_results.json`.
- **Sites agent** (`sites_agent.py`) — BUILT. Serious Eats live via Playwright (bypasses 403). Symlinked as `~/.local/bin/sites`. Registry-based: add a new site = one dict entry in SITES.
- **Kenji Lopez-Alt** — blocked: seriouseats.com returns 403. Need alternate source (his Substack, YouTube, or wait for Serious Eats solution).
- **ATK / America's Test Kitchen** — DONE Jun 9 2026. `atk_agent.py` + `sync_atk_recipes` MCP tool. Playwright auth, httpx fetches, 3 collections.
- **Chetna Makan YouTube** (nice to have): attach YouTube video link to recipes fetched from chetnamakan.co.uk. Channel: `UC1VkNUPA6ieOuwXmk4SSJZw`.
- Goal: full recipe discovery pipeline — any source accessible via agent, no manual URL fetching

### Cuisine Agents — Idea Pool Replenishment (between cycles)
- **DONE Jun 8 2026**: renamed to `fill_menu_ideas.py`; skill `/fillmenuideas`; plist updated to `com.menubuilder.fillmenuideas`; README, CLAUDE.md, backlog updated.

**Decision**: agents run **outside** the weekly workflow — not inline. Reason: token cost, latency (2-4 min per agent), and raw results lack the metadata (health, cook time, ingredients) needed to score them against weekly criteria. `suggest_meals.py` operates on `recipe_metadata.json`; agents feed the JSON, they don't replace it.

**Pattern**: run `fill_menu_ideas.py` on-demand between cycles when the idea pool needs refreshing. It runs all agents in parallel, deduplicates against existing entries, classifies health via Claude Haiku, and writes new entries as `status: "active"` with a `.md` file at intake.

**`fill_menu_ideas.py`** — BUILT Jun 2026.
- `python3 fill_menu_ideas.py` — all agents, default topics
- `python3 fill_menu_ideas.py --agents indian,mexican` — specific agents
- `python3 fill_menu_ideas.py --topic "fish dinner"` — override query for all agents
- `python3 fill_menu_ideas.py --dry-run` — preview without writing
- Deduplicates by URL and title; classifies health in one Haiku batch call; infers meal_type and cooking_method from recipe data

**Weekly cron** — `com.menubuilder.fillmenuideas.plist` loaded Jun 2026. Fires Saturday 10 AM (ideas ready before Sunday planning). Log: `fill_menu_ideas.log` in project root. Review/unload after ~40 weeks (around Apr 2027) once idea pool is stable.

*(Thin-pool prompt removed Jun 2026 — 163 active recipes across 30+ cuisines; pool is never thin enough to warrant this. Saturday cron replenishes weekly.)*

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
**Status**: FIXED Jun 9 2026.
- **Was**: rolling 7-day cutoff caused recurring ingredients (parsley, mushrooms, etc.) to be silently dropped on consecutive weekly lists.
- **Fix**: cutoff anchor changed from `(current date) - 7 days` to the plan start date extracted from the CSV filename (`shopping_YYYY-MM-DD.csv`). Items completed before this plan was generated are treated as prior-week → re-added correctly. Items completed on or after plan start → already bought this week → skipped.
- **Fallback**: if CSV filename parse fails, falls back to 24-hour window (handles same-day re-runs).
- **File**: `/Applications/WeeklyShoppingList.app/Contents/Resources/Scripts/main.scpt`; source backup at `/tmp/WeeklyShoppingList_backup.applescript`

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

### Recipe Review UI — image backfill for existing collection
- Existing recipes in `recipe_metadata.json` have no `image` field (only Italian agent captures it at intake)
- Backfill: for each active recipe with a `source_url`, scrape `og:image` and write to metadata
- Makes Full Collection view cards useful (currently mostly placeholders)

### Recipe Review UI — remove button in Full Collection view
- Full Collection view should have a Remove button in the modal toolbar
- Removes recipe from `recipe_metadata.json`, deletes `.md` file from Dropbox, deletes from GitHub Pages repo
- Requires confirmation step before deleting

### Recipe Review UI — metadata caching
- `recipe_review_server.py` loads `recipe_metadata.json` on every `/api/recipes` request
- Cache in memory with a file-modified-time check; only re-read when file changes
- Not needed at current scale but will matter when recipe pool grows

### Meal Costing (Long-Term)
- Once price history accumulates, cost recipes using `ingredients` array + price-per-unit averages from `price_history.json`
- MenuBuilder will skip entries missing `price_per_unit` gracefully
- **Owner**: GroceryAgent pipeline feeds the data; MenuBuilder is the consumer
- **Hold until**: ~Sep 15 2026 — need 3 more months of receipt data before price_history has enough coverage to be useful
