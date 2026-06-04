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
  - **Mexican** — Pati Jinich, Rick Bayless, Cooking Con Claudia
  - **Chef** — Alton Brown, Smitten Kitchen (Deb Perelman), Chetna Makan
  - **Asian** — Just One Cookbook (Japanese), Maangchi (Korean), Hot Thai Kitchen (Thai), Viet World Kitchen (Vietnamese), Woks of Life (Chinese)
  - **Sites** — Serious Eats (Playwright-based to bypass Cloudflare)
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
process_feedback_queue.py     # Drain SMS feedback queue into feedback_current.json
meal_swap.py                  # Mid-week meal swap — all swap logic lives here
send_menu_partner.py          # Send weekly menu to partner for approval via Keanu
show_inventory.py             # Opens browser with full inventory grouped by category
recipe_agent.py               # Recipe search orchestrator — single entry point for all agents
mexican_agent.py              # Mexican recipe sources (Pati Jinich, Rick Bayless, Cooking con Claudia)
asian_agent.py                # Asian recipe sources (JOC, Maangchi, Hot Thai Kitchen, Viet World Kitchen, Woks of Life)
chef_agent.py                 # Chef recipe sources (Alton Brown, Smitten Kitchen, Chetna Makan)
sites_agent.py                # Cross-cuisine sites (Serious Eats) via Playwright
save_to_recipeideas.py        # Save agent results to the recipeideas inbox
generate_github_pages_data.py # Generate _data/recipes.json for GitHub Pages — run after metadata changes
eval_mexican_agent.py         # Eval harness for mexican_agent
eval_chef_agent.py            # Eval harness for chef_agent
eval/                         # Eval prompt suites (mexican_prompts.json, chef_prompts.json)
mcp/
  menu_server.py              # MCP server — exposes 7 workflow tools over stdio
  README.md                   # MCP setup and Claude Code wiring instructions
recipe_metadata.json          # (not committed) Single source of truth for all recipe data
menu_activity.json            # (not committed) Active workflow state (created by MCP server)
config.json                   # (not committed) Local paths and settings — see config.example.json
CLAUDE.md                     # AI assistant context and workflow instructions
backlog.md                    # Planned features
release-notes.md              # Shipped features log
```

## MCP Server

`mcp/menu_server.py` exposes the weekly menu workflow as 6 tools callable from Claude Code
or any MCP-compatible client (e.g. Keanu via SMS):

| Tool | What it does |
|---|---|
| `get_workflow_state` | Returns current workflow step and state data |
| `start_menu_workflow` | Drains feedback queue, loads last week, initializes activity |
| `log_meal_feedback` | Records last-week ratings; `"done"` finalizes and advances state |
| `get_meal_suggestions` | Scores candidates, auto-selects 7 meals for the week |
| `advance_to_meal_approval` | Writes selected meals into menu_activity.json; bridges local SMS phase to MCP bridge phase |
| `swap_meal` | Replaces one day's meal (auto-picks or takes explicit name) |
| `approve_menu` | Sends selected meals to Ashley via Keanu for signoff |

Activity state lives in `menu_activity.json` (MenuBuilder's territory). See `mcp/README.md`
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
