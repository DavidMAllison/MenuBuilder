# MenuBuilder MCP Server

Exposes the weekly menu build workflow as MCP tools. Claude Code and Keanu call
these tools; MenuBuilder owns all state.

## Requirements

Python 3.12 via Homebrew + the project venv:

```bash
# One-time setup (already done if .venv exists)
python3.12 -m venv /Users/davidallison/projects/personal/MenuBuilder/.venv
.venv/bin/pip install mcp anthropic httpx beautifulsoup4
```

## Running manually (test)

```bash
cd /Users/davidallison/projects/personal/MenuBuilder
.venv/bin/python3.12 mcp/menu_server.py
```

The server speaks MCP over stdio. It won't produce output unless a client connects.

## Wire into Claude Code

Add to `~/.claude/claude_desktop_config.json` (create if it doesn't exist):

```json
{
  "mcpServers": {
    "menubuilder": {
      "command": "/Users/davidallison/projects/personal/MenuBuilder/.venv/bin/python3.12",
      "args": ["/Users/davidallison/projects/personal/MenuBuilder/mcp/menu_server.py"]
    }
  }
}
```

Restart Claude Code after editing the config. Confirm tools appear with `/mcp`.

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
| `log_meal_feedback` | `feedback: str` | Records ratings; "done" finalizes and advances state |
| `get_meal_suggestions` | `cuisine_direction`, `constraints` | Loads candidates, auto-selects 7 meals |
| `swap_meal` | `day`, `reason`, `replacement?` | Replaces one day (auto-picks if no replacement given) |
| `approve_menu` | — | Sends selected meals to Ashley via Keanu |

## State machine

```
idle
  └─ start_menu_workflow()
       ↓
awaiting_meal_logging
  └─ log_meal_feedback("done")
       ↓
awaiting_suggestions
  └─ get_meal_suggestions(...)
       ↓
awaiting_meal_approval
  ├─ swap_meal(...) → stays here
  └─ approve_menu()
       ↓
awaiting_ashley_signoff
```

## What's out of scope (not yet in these tools)

- Plan text generation (`mealplan_YYYY-MM-DD.txt`)
- Shopping CSV generation
- App launching (WeeklyShoppingList, WeeklyMealCalendar)
- Ashley's reply handling (idea activation, finalization)

These remain in the Claude Code interactive session. They can be added as
additional MCP tools in a future iteration.

## Keanu integration notes

Once Keanu migrates to calling MCP tools, `menu_workflow.py` in sms-assistant
can be simplified to:
1. Check `get_workflow_state()` to see what step we're on
2. Pass user input to the appropriate tool
3. Forward the tool response back to the user via iMessage

The old `menu_session.json` at `/Users/Shared/cooking/menu_session.json` is
superseded by `menu_activity.json` here. Leave it in place until Keanu
migration is complete.
