# MenuBuilder MCP Server

Exposes the complete weekly menu workflow as 9 MCP tools. Claude Code and Keanu
call these tools; MenuBuilder owns all workflow state.

## Requirements

Python 3.12 via Homebrew + the project venv:

```bash
# One-time setup (already done if .venv exists)
python3.12 -m venv /Users/davidallison/projects/personal/MenuBuilder/.venv
.venv/bin/pip install mcp anthropic httpx beautifulsoup4
```

Also requires `ANTHROPIC_API_KEY` in the environment (used by `finalize_plan`
to generate the REMINDERS section via Claude Sonnet).

## config.json fields used by the server

| Field | Required | Purpose |
|---|---|---|
| `metadata_path` | yes | Path to `recipe_metadata.json` |
| `partner_handle` | yes | Ashley's phone number for Keanu outbox |
| `admin_handle` | optional | David's phone number — prep guide + plan summary |
| `github_pages_base_url` | optional | Recipe URL prefix for GitHub Pages links |
| `dropbox_recipe_base_url` | optional | Dropbox fallback URL prefix |

## Running manually (test)

```bash
cd /Users/davidallison/projects/personal/MenuBuilder
.venv/bin/python3.12 mcp/menu_server.py
```

The server speaks MCP over stdio. It won't produce output unless a client connects.

## Wire into Claude Code

Already configured in `~/.claude.json` (mcpServers). Restart Claude Code to pick
up changes, then verify tools appear with `/mcp`.

## Activity state

Stored in `menu_activity.json` at the project root. This file is gitignored.
It is the single source of truth for the current workflow step.

Do **not** read or write `menu_activity.json` directly from Keanu or any other
client — use the MCP tools exclusively.

## Tools

| Tool | Args | What it does |
|---|---|---|
| `get_workflow_state` | — | Returns current activity state |
| `start_menu_workflow` | — | Drains feedback queue, loads last week, initializes state |
| `log_meal_feedback` | `feedback: str` | Records ratings; `"done"` finalizes and advances state |
| `get_meal_suggestions` | `cuisine_direction`, `constraints` | Loads candidates, auto-selects 7 meals |
| `swap_meal` | `day`, `reason`, `replacement?` | Replaces one day (auto-picks if no replacement given) |
| `approve_menu` | — | Sends selected meals to Ashley via Keanu |
| `handle_ashley_reply` | `reply: str` | Processes approval or swap; triggers finalization on approval |
| `activate_idea_recipe` | `name`, `content`, `source_url?` | Activates a pending idea from markdown content |
| `finalize_plan` | — | Generates plan + shopping CSV, launches apps, sends prep guide |
| `get_prep_guide` | — | Returns compact SMS-ready Sunday batch prep list from current plan |

## Full state machine

```
idle
  └─ start_menu_workflow()
       ↓
awaiting_meal_logging
  └─ log_meal_feedback("done")
       ↓
awaiting_suggestions
  └─ get_meal_suggestions(cuisine, constraints)
       ↓
awaiting_meal_approval
  ├─ swap_meal(day, reason) → stays here
  └─ approve_menu()
       ↓
awaiting_ashley_signoff
  └─ handle_ashley_reply(reply)
       ├─ swap text → re-sends, stays here
       ├─ approval + all recipes active → finalize_plan() → complete
       └─ approval + ideas on menu, auto-fetch fails
            ↓
       awaiting_idea_activation
         └─ activate_idea_recipe(name, content) [repeat for each]
              ↓
           awaiting_finalization
              └─ finalize_plan() → complete
```

## finalize_plan details

`finalize_plan()` and `handle_ashley_reply(approval)` both call the same
finalization logic:

1. Generate `mealplan_YYYY-MM-DD.txt`:
   - DINNERS block: day, recipe, health tag, cook time, recipe URL
   - BALANCE line: health classification counts
   - REMINDERS: generated via Claude Sonnet (falls back to plain day list if
     `ANTHROPIC_API_KEY` not available)
2. Generate `shopping_YYYY-MM-DD.csv`: Item / Notes (qty+unit) / Date (cook date)
3. Launch `WeeklyShoppingList.app` and `WeeklyMealCalendar.app` (safe to re-run)
4. Send plan summary to `admin_handle` (David) via Keanu outbox
5. Send prep guide to both `admin_handle` and `partner_handle` via Keanu outbox

## Keanu integration notes

Once Keanu migrates to calling MCP tools, `menu_workflow.py` in sms-assistant
can be simplified to a thin dispatcher:
1. `get_workflow_state()` → determine current step
2. Pass user input to the appropriate tool
3. Forward the tool response back to the user via iMessage

The old `menu_session.json` at `/Users/Shared/cooking/menu_session.json` is
superseded by `menu_activity.json` here. Leave it in place until migration is
complete.
