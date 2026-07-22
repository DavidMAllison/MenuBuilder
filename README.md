# MenuBuilder

A personal meal planning system built around real family constraints — health goals, picky kids, busy weeknights, and a rotating recipe library. Designed to reduce the weekly "what's for dinner" decision fatigue while hitting dietary targets consistently.

## What It Does

- **Weekly meal planning**: Proposes 7 dinners tailored to the week's schedule, health balance, and what's already in the fridge
- **Recipe candidate scoring**: Filters recipes by recency, health classification, protein variety, cuisine variety, and seasonal cooking method
- **Shopping list generation**: Aggregates ingredients across the week's plan into a structured CSV, auto-imported into iOS Reminders via a Mac app
- **Calendar integration**: Adds dinner events to iCloud Calendar with cook times and recipe links, built as a Mac app
- **Feedback loop**: Tracks which meals the family liked, surfaces family-favorite signals in future candidate scoring
- **SMS assistant**: Companion WhatsApp bot for querying recipes, meal plans, and inventory from a phone (separate project)
- **Recipe discovery agents**: Automated agents that source new recipe ideas from regional cuisine sites and named chefs. All agents are accessed through a single orchestrator (`recipe_agent.py` / `recipe` CLI).
  - **Mexican** — Pati Jinich, Rick Bayless, Cooking Con Claudia, De Mi Rancho a Tu Cocina (Doña Ángela)
  - **Chef** — Alton Brown, Smitten Kitchen (Deb Perelman), Chetna Makan, J. Kenji López-Alt (YouTube)
  - **Asian** — Just One Cookbook (Japanese), Maangchi (Korean), Hot Thai Kitchen (Thai), Viet World Kitchen (Vietnamese), Woks of Life (Chinese)
  - **Indian** — Indian Healthy Recipes, Hebbars Kitchen, Chetna Makan, Kannamma Cooks (South Indian/Tamil), Ranveer Brar, Archana's Kitchen
  - **Italian** — Giallo Zafferano, Cucchiaio d'Argento, Memorie di Angelina (Frank Fariello)
  - **Mediterranean** — The Mediterranean Dish (Suzy Kazan), My Greek Dish, Feasting at Home
  - **Sites** — Serious Eats (Playwright-based to bypass Cloudflare)
  - **ATK** (`atk_agent.py`) — America's Test Kitchen via saved favorites collections. Playwright for paywall auth, httpx + cookies for fetches. Pulls "Try Out", "Sunday Dinner", "Dinners" collections; falls back to top-rated. Accessible as `sync_atk_recipes` MCP tool or `python3 atk_agent.py`.
- **Sunday auto-generation**: launchd cron fires at 9 AM every Sunday, kicks off a guided SMS workflow — collects last-week feedback, schedule changes, and cuisine preferences before proposing candidates
- **Variety enforcement**: `suggest_meals.py` scores candidates with protein variety limits (max 2 chicken/week), cuisine family caps (max 2 per family), and new-recipe pressure (at least 1 recipe not cooked in 6+ weeks)
- **Agent eval harness**: Three-tier automated evaluation for each agent — source routing check, parse completeness check, and human review template. Prompt suites in `eval/`.
- **Recipe web hosting**: Recipe collection published to [GitHub Pages](https://davidmallison.github.io/menubuilder-recipes/) as styled HTML — clean mobile URLs, no login required, replaces Dropbox preview links

## Design Decisions

**JSON as single source of truth.** All recipe metadata lives in `recipe_metadata.json` — health classification, cuisine type, cook time, cooking method, times cooked, last cooked date, and a structured ingredients array. Recipe `.md` files contain only recipe content (title, ingredients, instructions, notes) — no metadata. This lets the planning system work entirely from the JSON without re-parsing recipe files on every run, reducing token usage and improving planning speed.

**Recipe `.md` files over PDFs.** Recipes are stored as plain Markdown files rather than PDFs. This means Claude can read recipe content directly without parsing, the SMS assistant can serve recipe text inline, and the files render cleanly on GitHub Pages for mobile access during cooking. All recipes are adapted and reformatted from their original sources — the original source link is included in each file for reference.

**Scoring over hard rules.** `suggest_meals.py` produces a ranked candidate list rather than making the final selection. A scoring function penalizes recently-cooked meals, overused recipes, and indulgent options while rewarding heart-healthy choices, family favorites, and in-season grill meals. The human makes the final call from the ranked list.

**Constraint-driven health balance.** The family has specific dietary goals (managing cholesterol and blood pressure). Rather than tracking macros, the system classifies each recipe as Heart-Healthy / Moderate / Indulgent and enforces a weekly ratio (5-6 heart-healthy, max 1 indulgent). Simple enough to maintain, effective enough to shift eating patterns.

**Kids-friendly layered cooking.** Recipes are structured where possible so kids' plain portions can be pulled before adding adult sauces — one meal, two outcomes, no separate cooking.

**Claude Code as the planning interface.** The AI assistant handles the conversational workflow — logging last week's meals, checking the schedule, running the candidate script, proposing options, generating the plan file, and triggering the Mac apps. The Python tooling handles deterministic filtering and file I/O.

## Repository Structure

```
suggest_meals.py              # Candidate meal filter — run before each weekly plan
candidate_scoring.py          # Shared candidate filtering/scoring/inventory-matching — imported by suggest_meals.py and mcp/menu_server.py so the two paths can't drift
workflow_smoketest.py         # Pre-flight check — GitHub publish, .md structure, state paths, app binaries, TCC, ATK session expiry (27 checks)
restart_mcp.sh                # Kill stale menu_server.py MCP subprocess(es) so the next tool call runs current code
ruff.toml                     # Lint config (pyflakes F + pycodestyle E4/E7/E9)
requirements.txt              # Pinned dependency versions
process_feedback_queue.py     # Drain SMS feedback queue into feedback_current.json
meal_swap.py                  # Mid-week meal swap — all swap logic lives here
send_menu_partner.py          # Send weekly menu to partner for approval via Keanu
show_inventory.py             # Opens browser with full inventory grouped by category
recipe_agent.py               # Recipe search orchestrator — single entry point for all agents
mexican_agent.py              # Mexican recipe sources (Pati Jinich, Rick Bayless, Cooking con Claudia)
asian_agent.py                # Asian recipe sources (JOC, Maangchi, Hot Thai Kitchen, Viet World Kitchen, Woks of Life)
indian_agent.py               # Indian recipe sources (Indian Healthy Recipes, Hebbars Kitchen, Chetna Makan, Kannamma Cooks)
chef_agent.py                 # Chef recipe sources (Alton Brown, Smitten Kitchen, Chetna Makan, Kenji López-Alt)
italian_agent.py              # Italian recipe sources (Giallo Zafferano, Cucchiaio, Memorie di Angelina)
mediterranean_agent.py        # Mediterranean recipe sources (Mediterranean Dish, My Greek Dish, Feasting at Home)
sites_agent.py                # Cross-cuisine sites (Serious Eats) via Playwright
yt_utils.py                   # Shared YouTube helpers — fetch_transcript(), enrich_recipe_from_transcript(); used by chef_agent and mexican_agent
recipe_source_patterns.md     # Decision tree for adding new recipe sources — five patterns (website, website+video, YT-description, YT-transcript, paywalled) with extraction technique and routing rules
atk_agent.py                  # America's Test Kitchen — syncs saved ATK collections into recipe_metadata.json (paywall auth via Playwright, httpx for fetches)
fill_menu_ideas.py            # Run all agents in parallel and add new results to recipe_metadata.json as status="active"
prep_utils.py                 # Shared prep classification — prompt, classify_prep(), parse_md_instructions(); used by fill_menu_ideas and menu_server
backfill_prep.py              # One-time (re-runnable) backfill of prep_components/prep_notes for all active recipes
backfill_ingredients.py       # Re-runnable: Haiku batch-parses ingredients_raw → structured ingredients array for all active recipes
suggest_lunch.py              # Scores lunch candidates for Ashley — filters lunch_suitable recipes, avoids last 4 weeks
trigger_lunch_saturday.py     # launchd Saturday 10 AM: sends 3 lunch options to Ashley via Keanu
trigger_lunch_nudge.py        # launchd Saturday 6 PM: nudges if Ashley hasn't picked yet
save_to_recipeideas.py        # Save agent results to the recipeideas inbox
generate_github_pages_data.py # Generate _data/recipes.json for GitHub Pages — run after metadata changes
migrate_plan_to_json.py       # One-shot (re-runnable) migration of mealplan_*.txt → mealplan_*.json
recipe_review_server.py       # Local recipe review web UI server (port 5051) — Flask, session auth, metadata cache, RAG search
recipe_review/
  index.html                  # Recipe review UI — This Week grid, Full Collection, New Recipes views; semantic search; Type filter (Dinner/Lunch/Condiment)
  login.html                  # Login page for recipe review UI
eval_mexican_agent.py         # Eval harness for mexican_agent
eval_chef_agent.py            # Eval harness for chef_agent
eval/                         # Eval prompt suites (mexican_prompts.json, chef_prompts.json)
mcp/
  menu_server.py              # MCP server — exposes workflow tools over stdio; get_prep_guide is on-demand with mode=weekly|tonight|auto
  README.md                   # MCP setup and Claude Code wiring instructions
recipe_metadata.json          # (not committed) Single source of truth for all recipe data
config.json                   # (not committed) Local paths and settings — no secrets (moved to .env)
.env.example                  # Template for secrets (YouTube API key, ATK credentials, Flask secret, review password hash) — copy to .env, loaded via python-dotenv
CLAUDE.md                     # AI assistant context and workflow instructions
backlog.md                    # Planned features
release-notes.md              # Shipped features log
```

## MCP Server

`mcp/menu_server.py` exposes the weekly menu workflow as 18 tools callable from Claude Code
or any MCP-compatible client (e.g. Keanu via SMS):

| Tool | What it does |
|---|---|
| `get_workflow_state` | Returns current workflow step and state data; flags `stale_code_warning` if `menu_server.py` changed on disk since this process started (run `restart_mcp.sh`) |
| `start_menu_workflow` | Drains feedback queue, loads last week, initializes activity |
| `log_meal_feedback` | Records last-week ratings; `"done"` finalizes and advances state |
| `get_meal_suggestions` | Scores candidates, auto-selects 7 meals for the week |
| `advance_to_meal_approval` | Writes selected meals into menu_activity.json; bridges local SMS phase to MCP bridge phase |
| `swap_meal` | Replaces one day's meal (auto-picks or takes explicit name) |
| `approve_menu` | Sends selected meals to Ashley via Keanu for signoff; optional `expected_selected_meals` refuses to send if the caller's local mirror has drifted from the actual workflow state |
| `handle_ashley_reply` | Processes Ashley's approval or swap request; a recipe URL in her reply is fetched and swapped in directly instead of stranding the workflow; auto-activates idea recipes |
| `activate_idea_recipe` | Activates a pending idea from pasted markdown content or URL auto-fetch; `content` is optional — if empty and `source_url` given, fetch is attempted first; returns `needs_content: True` if fetch fails |
| `finalize_plan` | Generates plan + shopping CSV, launches apps, notifies admin |
| `get_prep_guide` | On-demand prep guide — `mode=weekly` (remaining meals this week) or `mode=tonight` (tonight's dinner); applies food-safety classification automatically |
| `generate_shopping_list` | Writes shopping CSV from a finalized meal dict (authoritative — sms-assistant calls this) |
| `get_lunch_suggestions` | Returns 3 scored lunch candidates for Ashley based on recency + variety |
| `set_lunch_pick` | Saves Ashley's lunch pick, adds ingredients to the week's shopping CSV |
| `log_lunch_feedback` | Records post-week lunch feedback (liked/disliked/not_made) |
| `add_lunch_recipe_url` | Fetches a URL, parses it into a lunch-suitable recipe entry |
| `process_recipe_url` | Check for a similar existing recipe by URL or fuzzy title, add if new, optionally swap into a plan day; `force_add=true` skips similarity check |
| `process_recipe_image` | Extract a recipe from a photo (cookbook page) via Claude vision and add to the collection; `force_add=true` re-runs with same image after user confirms |

Runtime state written by both this project and the SMS assistant (`menu_activity.json`,
the weekly plan/shopping CSV, `lunch_state.json`, `feedback_queue.json`, the outbox spool)
lives in `/Users/Shared/cooking-state/` — a shared directory outside Dropbox with an
inheritable ACL, so ownership doesn't matter across the two Mac accounts that write to it.
Recipe data (`recipe_metadata.json`, recipes, images) stays in Dropbox. See `mcp/README.md`
for setup and Claude Code wiring instructions.

## Usage

```bash
# Basic candidate list
python3 suggest_meals.py

# Flag nights with early practices (needs quick meals)
python3 suggest_meals.py --quick mon,tue,thu

# Override week start date
python3 suggest_meals.py --week 2026-04-21
```

Copy `config.example.json` to `config.json` and update the values for your setup:

```bash
cp config.example.json config.json
```

```json
{
  "metadata_path": "~/path/to/recipe_metadata.json",
  "adult_names": ["Parent1", "Parent2"]
}
```

## Mac Apps

- **WeeklyShoppingList.app** — reads `shopping_YYYY-MM-DD.csv` and populates the iOS Grocery Reminders list (syncs to family members)
- **WeeklyMealCalendar.app** — reads the week's meal plan and adds dinner events to iCloud Calendar with cook times and recipe links

Both run via `open /Applications/<AppName>.app` at the end of each planning session.

## Recipe Metadata Schema

```json
{
  "Recipe Name": {
    "source": "America's Test Kitchen",
    "cuisine": "Italian",
    "meal_type": "Weeknight",
    "health": "Heart-Healthy",
    "cooking_method": "stovetop",
    "time": "40 min",
    "times_cooked": 4,
    "last_cooked_date": "2026-03-15",
    "status": "active",
    "ingredients": [
      {"name": "chicken breast", "quantity": "1.5", "unit": "lbs", "category": "Proteins"}
    ],
    "feedback": [
      {"person": "adult", "sentiment": "liked", "note": ""}
    ]
  }
}
```

## Status Fields

| Status | Meaning |
|---|---|
| `active` | In rotation, `.md` file exists |
| `idea` | Staged for trial, not yet tried |
| `disliked` | Tried, didn't work — kept as tombstone |
