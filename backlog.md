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
- **Feedback path**: COMPLETE Jun 24 2026 — SMS reports "that was harder than expected" → Keanu updates `weeknight_effort` for that recipe

### Cooking Notes Into Recipe Files
**Status**: COMPLETE Jun 15 2026. All 8 recipes with actionable notes have `## Notes` sections in their `.md` files. Add notes to new recipes as you cook them — no bulk process needed.

### Ingredient Name Normalization
**Status**: Won't do (Jun 15 2026). `_ING_ALIASES` in `mcp/menu_server.py` already handles the safe normalizations at query time. Remaining variants (fresh vs dried herbs, chopped vs diced tomatoes) are genuinely distinct products — bulk Haiku normalization would risk wrong merges. Fix ingredient names manually when noticed during cooking or shopping.



### Weekly Lunch Recommendation
**Status**: COMPLETE Jun 13 2026.
- Saturday 10 AM SMS → 3 suggestions → Ashley picks → ingredients added to shopping list dated Saturday
- Saturday 6 PM nudge if no reply
- sms-assistant: `set_lunch_pick` + `log_lunch_feedback` wired in `tools.py` + system prompt updated

### Lunch Calendar Event
**Status**: COMPLETE Jun 17 2026.
- `WeeklyMealCalendar.app` creates "Ashley's Lunch: [recipe]" events at noon (12–1 PM) for Sun–Fri of the current plan week
- Reads `/Users/Shared/cooking/lunch_state.json`; skips if no pick is set
- Deletes any existing noon events with "Ashley's Lunch" prefix before recreating (safe to re-run)
- Saturday is excluded; only future dates are processed

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
- Serious Eats: DONE Jun 17 2026. Search page is Cloudflare-blocked; sitemap + httpx recipe pages are accessible. sites_agent.py uses sitemap keyword search → httpx fetch. No Playwright needed.
- Other sites: add to `sites_agent.py` registry as needed

### PDF-to-Markdown Migration
**Status**: Complete. All PDFs converted to .md (Jun 2026).

### Recipe Verbatim Scan
**Status**: COMPLETE Jun 20 2026. ATK done previously; 55 non-ATK recipes rewritten via Haiku batch. All instructions are now reworded adaptations, not verbatim copies.

### ATK Recipe Attribution
**Status**: COMPLETE Jun 17 2026. All ATK recipes have `source_url` in metadata and `*Adapted from [America's Test Kitchen](url)*` footer in `.md` files.

### Recipe Agents for All MenuBuilder Sources
- **Cuisine agents** (source-specific): Mexican (done), Asian/East+Southeast (done — Japanese, Korean, Thai, Vietnamese, Chinese), Indian (done — Jun 2026, its own agent separate from Asian), Italian (done), Mediterranean (done), etc.
- **Indian agent** (`indian_agent.py`) — BUILT Jun 2026. Sources: Indian Healthy Recipes, Hebbars Kitchen, Chetna Makan (two HTML patterns handled), Kannamma Cooks (South Indian), Ranveer Brar, Archana's Kitchen. Symlinked as `~/.local/bin/indian`. Results to `/tmp/indian_agent_results_{uid}.json`.
- **indianhealthyrecipes.com instructions** — FIXED Jun 8 2026. Root cause: IHR uses `HowToSection` grouping in ld+json (not flat `HowToStep`); code now handles nested `itemListElement`. WPRM HTML fallback added as safety net. `html.unescape()` added for entity cleaning.
- **ranveerbrar.com** — FIXED Jun 8 2026. Search via sitemap (~1576 URLs across two sitemap files). ld+json ingredients are category labels — replaced with HTML extraction from `ingredients_cont_wrap` div. Hindi translations stripped from ingredients and title.
- **archanaskitchen.com** — FIXED Jun 8 2026. URL pattern changed to `/recipe/slug` (old flat `/slug` pattern returns 404). Search via sitemap (~6779 recipe URLs); Hindi/Tamil duplicate variants filtered. ld+json works cleanly for both ingredients and instructions.
- **Chef agent** (`chef_agent.py`) — done: Alton Brown, Deb Perelman (Smitten Kitchen), Chetna Makan. Symlinked as `~/.local/bin/chef`. Results to `/tmp/chef_agent_results.json`.
- **Sites agent** (`sites_agent.py`) — BUILT. Serious Eats live via Playwright (bypasses 403). Symlinked as `~/.local/bin/sites`. Registry-based: add a new site = one dict entry in SITES.
- **Cooking con Claudia (YouTube)** — DONE Jun 22 2026. YouTube Data API v3 (not yt_dlp); `search_claudia` + `fetch_claudia` in `mexican_agent.py`. Parses both ingredients and instructions from video description (English + Spanish headers). API key in `config.json` as `youtube_api_key`.
- **ATK / America's Test Kitchen** — DONE Jun 9 2026. `atk_agent.py` + `sync_atk_recipes` MCP tool. Playwright auth, httpx fetches, 3 collections.
- **Chetna Makan YouTube** — COMPLETE Jun 24 2026. `_search_chetna_youtube(title)` in `chef_agent.py` queries her channel (`UC1VkNUPA6ieOuwXmk4SSJZw`) via YouTube Data API v3. Called automatically in `_fetch_chetnamakan()`; result stored as `video_url` in the recipe dict (already handled by `add_recipe`). Single-word match threshold accounts for Hindi spelling variants (gobhi/gobi, palak/saag).
- Goal: full recipe discovery pipeline — any source accessible via agent, no manual URL fetching

### Mediterranean Agent (`mediterranean_agent.py`)
**Status**: COMPLETE Jun 2026.
- Sources: olivetomato.com + themediterraneandish.com; wired into `fill_menu_ideas.py`


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

### Local Recipe Browser + RAG Search
**Status**: COMPLETE Jun 16 2026.
- Local keyword filter (client-side, instant) in Recipe Review UI header search box — filters by title, cuisine, source, ingredients as you type
- Semantic RAG search on Enter: `sentence-transformers all-MiniLM-L6-v2` + ChromaDB in-memory index over 228 recipes; builds in ~20s at startup
- Negation filter: "not X", "isn't X", "without X" strips matching titles from results
- Index status polled via `GET /api/search_status`; hint text shows build progress
- Works across both New and Full Collection views

### WeeklyShoppingList.app - Completed-Item Skip Window Too Wide
**Status**: FIXED Jun 9 2026.
- **Was**: rolling 7-day cutoff caused recurring ingredients (parsley, mushrooms, etc.) to be silently dropped on consecutive weekly lists.
- **Fix**: cutoff anchor changed from `(current date) - 7 days` to the plan start date extracted from the CSV filename (`shopping_YYYY-MM-DD.csv`). Items completed before this plan was generated are treated as prior-week → re-added correctly. Items completed on or after plan start → already bought this week → skipped.
- **Fallback**: if CSV filename parse fails, falls back to 24-hour window (handles same-day re-runs).
- **File**: `/Applications/WeeklyShoppingList.app/Contents/Resources/Scripts/main.scpt`; source backup at `/tmp/WeeklyShoppingList_backup.applescript`

### WeeklyShoppingList.app - Grouped Reminders by Category
**Status**: COMPLETE Jun 20 2026. Works fine in practice.

### WeeklyMealCalendar.app Improvements
**Status**: COMPLETE Jun 22 2026.
- Fixed: `as_escape(None)` crash when `url`, `recipe`, or other fields are JSON `null` — coerced all string fields to `""` in `parse_meal_plan`. No cook time already had a 60-min fallback; multi-component titles work as-is.

### Meal Swap Handling
**Status**: COMPLETE Jun 20 2026. Swap via Keanu SMS; plan JSON updated; visible in Recipe Review UI.

### Consolidate Meal Plan to JSON Format
**Status**: COMPLETE Jun 16 2026.
- `mealplan_YYYY-MM-DD.json` replaces `.txt`. Schema: `{week_start, week_end, generated_date, balance, meals[{day, date, title, health, time, url, reminder}]}`
- All 25 historical plans migrated via `migrate_plan_to_json.py` (pre-Mar 2026 plans used a different format — 0 meals, not migrated; kept as-is for reference)
- `menu_server.py` writes JSON; `_parse_last_plan()` and `get_prep_guide()` read JSON
- `meal_swap.py` reads/writes JSON
- `WeeklyMealCalendar.app` reads JSON (parses dates from `date` field directly, no regex)
- Old `.txt` files left in place as archive

### Recipe Review UI — image backfill for existing collection
**Status**: COMPLETE Jun 16 2026. `backfill_images.py` ran; 140/149 recipes with source_url updated. 78 recipes (ATK + originals) have no source_url and remain without images. Re-run `backfill_images.py --force` after adding new sourced recipes.

### Recipe Review UI — Agent trigger on /New page
**Status**: COMPLETE Jun 24 2026.
- Lightning bolt icon button in view-bar (New view only); on mobile "This Week" collapses to calendar icon to preserve space
- Bottom sheet: agent chips (All / Mexican / Asian / Indian / Chef / Italian / Mediterranean / Sites, multi-select) + optional topic input + Run button
- `POST /api/fill_ideas` + `GET /api/fill_ideas_status` in `recipe_review_server.py`; threading.Lock prevents concurrent runs
- Run button greys out to "Running…" during execution; polls every 10s; auto-reloads New view cards on completion

### Recipe Review UI — More prominent loading indicator
**Status**: COMPLETE Jun 20 2026.

### Recipe Review UI — Cuisine/Source UX overhaul
**Status**: COMPLETE Jun 20 2026.
- Flat grid (alphabetical) replaces cuisine section grouping
- Filters button in view bar opens a bottom sheet with: Health, Time, Tried, Cuisine (multi-select), Source (multi-select)
- Active filter count badge on Filters button; sheet applies filters in real-time; Clear all resets everything
- Filters button hidden on This Week view; shown on New and Full Collection

### Recipe Review UI — dual links in Collection modal (Recipe + Source)
**Status**: COMPLETE Jun 21 2026.
- Collection modal toolbar now shows "Recipe" → GitHub Pages URL + "Source" → original site (when present)
- Original/ATK recipes with no `source_url` show only the "Recipe" link
- `/api/collection` returns `recipe_url` (from `filename`) and `source_url` as separate fields
- New view unaffected (single "Visit" link to source URL for unreviewed recipes)

### Recipe Review UI — remove button in Full Collection view
**Status**: COMPLETE Jun 17 2026.
- Remove button (red trash icon) in Full Collection modal toolbar
- Never-tried (`times_cooked == 0`): hard delete — entry removed from metadata, `.md` deleted
- Previously cooked (`times_cooked > 0`): soft delete — `status: "retired"`, `.md` deleted, entry kept for cook history
- `retired` and `disliked` entries hidden from all UI views (New, Full Collection)
- New view also filters out retired/disliked by `source_url` so agent results that match are silently hidden
- Confirmation prompt shows appropriate message based on cook count

### Recipe Review UI — metadata caching
**Status**: COMPLETE Jun 16 2026. `_load_metadata()` / `_save_metadata()` in `recipe_review_server.py`. mtime-keyed cache; only re-reads from disk when file changes. All five read sites + two write sites converted.

### Recipe Review UI — Autonomous Data-Quality Agent
**Status**: COMPLETE Jun 22 2026.
- `--fix-metadata` flag added to `cleanup_agent.py`: auto-fixes cuisine normalization (variant → canonical), source inference from domain, meal_type mismatches (time > 60 min + Weeknight → Weekend, skips slow_cooker), and needs_review clearing when instructions pass quality check
- All fixes are auto-applied with no user review required
- `fill_menu_ideas.py` runs `cleanup_agent.py --fix-metadata --fix-classify --apply` at completion as a post-run sweep
- On-demand: `python3 cleanup_agent.py --fix-metadata`

### SMS Recipe Image Submission
**Status**: COMPLETE Jun 22 2026. Pending live test tonight.
- Caption "photo of [meal name]" → Keanu routes to `_run_set_recipe_image` in `server.py` (Path -1, before recipe-idea path)
- New MCP tool `set_recipe_image(recipe_name, image_b64, mime_type)` in `menu_server.py`: fuzzy-match → save to `~/Dropbox/LLMContext/cooking/recipe_images/{slug}.jpg` → update `image` field in metadata
- Review server `/api/img` proxy extended to serve local file paths (absolute paths on disk)
- Ambiguous match returns top 3 candidates; user can retry with exact name

### Condiment Handling in Cleanup Agent and Shopping List
**Status**: COMPLETE Jun 24 2026.
- `condiment_deps: [...]` field written to 12 recipes in `recipe_metadata.json` (enchiladas, BBQ, taco recipes)
- `_build_shopping_csv` in `menu_server.py` pulls condiment ingredients via `_condiment_ingredients()` when present — notes field shows "Condiment Name (for Recipe)"
- `condiments.json` schema updated with `source_url` and `image` fields; home recipes carry empty URL
- `_save_agent_condiment()` in `fill_menu_ideas.py` writes agent-found condiments to `condiments.json` (with source_url) instead of discarding them
- `cleanup_agent.py --check-condiments` checks source_url liveness and missing images across all condiments
- Inventory tracking deferred — lower priority

### Meal Costing (Long-Term)
- Once price history accumulates, cost recipes using `ingredients` array + price-per-unit averages from `price_history.json`
- MenuBuilder will skip entries missing `price_per_unit` gracefully
- **Owner**: GroceryAgent pipeline feeds the data; MenuBuilder is the consumer
- **Hold until**: ~Sep 15 2026 — need 3 more months of receipt data before price_history has enough coverage to be useful
