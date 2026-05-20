# MenuBuilder - Backlog

## Planned Features

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
- **Transport**: iMessage via dedicated iCloud account (not Twilio)
- **Blocked on**: iCloud account setup -- attempting via old physical iPhone (browser setup had issues)
- **Next**: Once iCloud account is ready on device, build the AppleScript/Python iMessage sender + launchd job

### WeeklyShoppingList.app - Grouped Reminders by Category
- **Goal**: Group shopping list items by category in the Grocery list
- **What we learned**: iOS Reminders auto-groups items in a Grocery-type list, but only when added via iOS UI — not via AppleScript. Groups also only appear on iPhone, not macOS desktop. AppleScript can't create groups or trigger auto-categorization.
- **Current workaround**: On iPhone, toggle list type Standard → save → Groceries → save. This triggers auto-grouping of all existing items.
- **Mar 2026 progress**: Implemented clean item names in reminder title (ingredient only), qty + meal name in Notes, due date set per item. iOS should now be able to auto-categorize correctly since reminder titles are plain ingredient names. Toggle workaround still needed to trigger grouping. Testing in progress.

### Create Missing Recipe Files
- **Tinga Verde** — blank PDF, source is Cooking Con Claudia. User to provide URL or paste recipe; create as `.md` file.
- **Pan-Seared Broccolini** — blank PDF, source unknown. User to provide recipe; create as `.md` file.

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

### WeeklyMealCalendar.app Improvements
- Handle edge cases: recipes with no cook time, multi-component meals

### PDF-to-Markdown Migration
- Script (`convert_recipes_to_md.py`) exists and has been used — 98 `.md` files done as of May 2026
- **28 PDFs remain** — run script in batches when time allows; user must be present to approve file deletions
- After each batch: verify `.md` content, update `recipe_metadata.json` filename fields, delete PDFs

### Recipe Viewer Enhancements (show_recipe.py)
- **In-page feedback**: thumbs up/down voting + freeform notes field rendered in the HTML page; on submit, writes to `feedback_current.json` so it flows into the Sunday logging workflow
- **SMS recipe display**: text a recipe name to Keanu, get back a formatted recipe (ingredients + steps); would use the same JSON-first lookup as show_recipe.py
- **Stale header cleanup**: batch-remove `Source:`, `Time:`, `Yield:` header blocks from older `.md` files that still have them baked in (those fields now live in JSON)

### Meal Swap Handling
- Support swapping a planned meal mid-week (e.g. grilling instead of the scheduled dinner)
- Involves: updating the meal plan txt, updating the calendar event via WeeklyMealCalendar.app, and logging the uncooked meal so it's not counted at Sunday feedback
- Need to think through calendar update flow since WeeklyMealCalendar.app rewrites all events on each run

### Consolidate meal plan to JSON format
- Currently meal plan is `.txt` (for WeeklyShoppingList.app and WeeklyMealCalendar.app) with a separate feedback JSON
- Goal: single `mealplan_YYYY-MM-DD.json` containing meals + feedback together
- Requires reworking both apps to read JSON instead of txt
