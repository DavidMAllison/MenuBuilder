# Architecture Review & Fix Plan — MenuBuilder + sms-assistant

**Date**: 2026-07-12
**Scope**: Why the Sunday menu run breaks every week, plus overall architecture review of both projects.
**Execution note**: Tasks are split by project. sms-assistant tasks (`/Users/Shared/sms-assistant/`) must be executed by a session working in that directory; MenuBuilder tasks in this repo. Do the phases in order — Phase 0 and 1 remove the recurring failure classes; later phases are hardening.

---

## Diagnosis: the five recurring failure classes

Evidence from `trigger_menu.log`, `keanu.log`, launchd state, and file permissions, most recent Sundays:

### 1. Cross-account file permissions (the #1 breaker)
Two macOS users share state through files: `davidallison` (edits code, owns Dropbox) and `allisonbot` (runs Keanu 24/7). `/Users/Shared/cooking` is a **symlink into `~davidallison/Dropbox/LLMContext/cooking`** (created 2026-04-15). So the bot writes directly into Dropbox-synced files. Dropbox periodically rewrites synced files as `davidallison` mode `644`, silently stripping group-write — then the bot's next write fails.

Logged instances:
- 2026-06-07 09:00 — Sunday trigger died: `PermissionError: /Users/Shared/cooking/menu_session.json`
- 2026-06-20 — `swap_meal` failed: `PermissionError: .../weeklyplan/mealplan_2026-06-15.json`
- 2026-06-28 09:11 — `get_meal_suggestions` failed mid-Sunday-run: `PermissionError: .../MenuBuilder/config.json`
- 2026-06-13 — `set_lunch_pick` failed: `FileNotFoundError: /Users/allisonbot/Dropbox/...` (a `~`-relative path expanded under the bot's home)

The current mitigation is `os.umask(0o000)` in `mcp/menu_server.py:72` (world-writable files). It doesn't survive Dropbox rewriting a file, and it's a hack.

### 2. A read path that writes, across the account boundary
`get_meal_suggestions` → `_normalize_cuisine_direction` → `_register_cuisine` (`mcp/menu_server.py:547-555, 634`) **writes `config.json`** whenever the cuisine direction is unrecognized. Consequences:
- Sunday 6/28 died on it (bot can't write a file in David's home).
- `config.json` `cuisine_family_map` is now polluted with garbage "cuisines" like `"what we've got — whole chicken, pork loin chops..."` and `"grilling focus, recipes from collection we haven't tried yet"` — free-text SMS input persisted as config.

### 3. Fragile scheduling chain
The 9 AM Sunday trigger requires ALL of: Mac awake + allisonbot session running Keanu + API credits available. Evidence:
- 2026-07-05: **no trigger log entry at all** (Mac asleep or session down — launchd LaunchAgents don't fire across logout/sleep-through-window reliably).
- 2026-07-12: nothing at 9:00; Keanu didn't start until 11:08; API credits were exhausted (`credit balance is too low`, 11:08); David manually edited the plist at 11:34 and fired the trigger at 11:45.
- `com.keanu.sms-assistant.plist` is **also loaded in davidallison's launchd session** (last exit status 78) — a duplicate that can never work there (wrong user's chat.db) and creates confusion.
- No alerting anywhere: when the trigger fails, nobody is told.

### 4. Split-brain state + an agent that improvises the workflow
Workflow state lives in TWO files with hand-rolled syncing: `menu_session.json` (sms-assistant, `_sync_session_state`) and `menu_activity.json` (MenuBuilder MCP). Historically buggy seam (see `handoff_sunday_sms_workflow.md`).

Worse UX bug, seen today 11:14–11:24: when no session is active, David's texts route to the **general chat agent** (`agent.py`/`menu.txt`), which happily role-plays menu planning with `get_recipe`/`check_inventory` — it has no tool to start the real workflow and `system_prompts/menu.txt` never mentions the Sunday workflow or the "start menu" keyword (`server.py:455-458` is keyword-gated). David spent 20 minutes "following the workflow" with an agent that wasn't in it, then concluded "menu builder is messed up again."

### 5. Bridge brittleness and tool-contract drift
`menubuilder_bridge.py` spawns MenuBuilder's 3,925-line MCP module **cold in a subprocess per tool call** (Python 3.9 CommandLineTools on the bot vs 3.12 venv in David's home). Failure modes on record:
- Tool signature drift: `log_meal_feedback() got an unexpected keyword argument 'meal_name'` (6/8) — sms-assistant defines its own copies of tool schemas (`_build_menu_tools`) with no contract test against MCP signatures.
- `[Errno 7] Argument list too long` (6/15) passing base64 images via argv (since fixed by stdin — good).
- JSON-over-stdout: any stray `print`/warning from the 3,925-line import breaks `json.loads`.
- Outbox race: `menu_workflow._send_outbox` and `menu_server._send_outbox` read-modify-write `.outbox.json` with **no lock**, while `server.py` uses `_outbox_lock` only for its own writes. Concurrent writes can drop messages.

Also on record, non-Sunday: receipt parser fails on non-JSON LLM output and PNG-labeled-JPEG uploads; `NameError: name 're' is not defined` crashed the main loop twice on 6/28 (untested path); ATK auth in `config.json` relies on a refresh token that expires ~30 days from 7/11 — that's a **future recurring breakage** for `sync_atk_recipes`/agents.

### Security notes (fix, but not Sunday-critical)
- `config.json` holds a plaintext ATK password, YouTube API key, Flask secret, and session tokens. It's gitignored (good) but world-readable and shared across accounts. Move secrets to `.env`/Keychain; rotate the YouTube key.
- `os.umask(0o000)` makes all created files world-writable.

### What's actually good (keep)
- Subprocess bridge with stdin payloads is a reasonable answer to the two-interpreter problem.
- The Review-UI gate for agent-sourced recipes ("no naked entries" invariant) is solid design.
- `evals/`, `tests/`, `workflow_smoketest.py` exist — they're just not wired to anything.
- Keanu's in-loop scheduled jobs (`maybe_send_trash_reminder` pattern with a state-file dedupe) are the right primitive — reuse it (Phase 0).

---

## Fix plan

### Phase 0 — Make Sunday self-healing and observable (do first, ~small)

**0.1 (sms-assistant) Move the Sunday trigger into Keanu's main loop.**
Add `maybe_start_sunday_menu(state, config)` to `server.py`, modeled exactly on `maybe_send_trash_reminder` (server.py:146):
- Condition: today is Sunday, time ≥ 9:00, `state.get("last_menu_trigger_date") != today`, and menu session state is `idle`/`complete`/absent.
- Action: call `menu_workflow.handle_start(config)`, queue the opening message to `menu_admin` via the outbox (reuse the `/start_menu_workflow` handler body, `server.py:772-794`), set `last_menu_trigger_date`, `save_state`.
- Result: if the Mac was asleep or Keanu was down at 9:00, the workflow starts the moment Keanu comes back — no launchd dependency. Keep `com.menubuilder.sundaymenu.plist` + `trigger_menu.py` as a redundant kick (it's idempotent if 0.1 checks `last_menu_trigger_date`; make the `/start_menu_workflow` handler check-and-set the same state key so double-fires can't send two openers).

**0.2 (sms-assistant) Sunday pre-flight check, 8:30 AM, same in-loop pattern.**
`maybe_run_preflight(state, config)` runs Sundays 8:30–9:00 (dedupe by date) and texts David ONLY on failure:
- API key works: 1-token Haiku ping; catch `credit balance is too low` specifically.
- Bridge round-trip: `call_menubuilder_tool("get_workflow_state")` returns without `error`.
- Writability: open-for-append then close on `menu_session.json`, `.outbox.json`, MenuBuilder `menu_activity.json`, the weeklyplan dir, `recipe_metadata.json`, `inventory.json`.
- On any failure, send: "Pre-flight for today's menu run failed: <check> — <error>". On success, silence.

**0.3 (main account, manual — David)** One-time ops fixes, write these into `keanu-setup.txt` per house rules:
- `launchctl bootout gui/502 ~/Library/LaunchAgents/com.keanu.sms-assistant.plist && rm ~/Library/LaunchAgents/com.keanu.sms-assistant.plist` (remove the duplicate from davidallison's session; the real one lives in allisonbot's).
- `sudo pmset repeat wakeorpoweron Sun 08:50:00` so the Mac is awake for the run.
- Set an API-credit auto-reload or a calendar reminder; pre-flight (0.2) will catch it either way.

**Acceptance**: kill Keanu Saturday night, reboot Sunday 10 AM → opening SMS arrives within one poll cycle of Keanu starting. Break a file permission deliberately → pre-flight text arrives at 8:30.

### Phase 1 — Kill the permission failure class (~small, high value)

**1.1 (David, manual) Split runtime state out of Dropbox.**
Create a real directory `/Users/Shared/cooking-state/` for everything **both accounts write**: `menu_session.json`, `menu_feedback_response.json`, `lunch_state.json`, `feedback_queue.json`, `inventory.json`, and the weeklyplan output dir. Apply inherited ACLs so ownership never matters again:
```
mkdir -p /Users/Shared/cooking-state
chmod +a "group:staff allow read,write,delete,add_file,add_subdirectory,file_inherit,directory_inherit" /Users/Shared/cooking-state
```
Dropbox keeps: recipes, `recipe_metadata.json`, recipe images (David-owned, bot only reads). If the bot must write `recipe_metadata.json` (it does, via feedback/activation), either (a) accept ACL on the Dropbox cooking folder too — ACLs survive Dropbox rewrites better than mode bits, or (b) route all metadata writes through the MCP running as one user. Start with (a); it's one command on the cooking folder.

**1.2 (MenuBuilder) Repoint paths.** `config.json` `metadata_path`/`inventory_path`, `mcp/menu_server.py` constants (lines 52-72), and the derived `WEEKLYPLAN_DIR` if weeklyplan moves. Remove `os.umask(0o000)` (menu_server.py:72) once ACLs are in place. Keep `/Users/Shared/cooking` symlink temporarily for backward compat; grep both repos for hardcoded `/Users/Shared/cooking` and `Dropbox/LLMContext/cooking` and update.

**1.3 (MenuBuilder) Stop `_register_cuisine` writing config at runtime.**
In `_normalize_cuisine_direction` (menu_server.py:607-636): unknown direction → pass through with a note, do NOT persist. Delete `_register_cuisine` or keep it for an explicit admin tool only. Clean the polluted `cuisine_family_map` entries in `config.json` (anything that isn't a cuisine name).

**Acceptance**: `sudo -u allisonbot` python one-liner appends to every state file successfully after a Dropbox resync of the cooking folder; `get_meal_suggestions` with direction "whatever nonsense text" succeeds and leaves `config.json` untouched.

### Phase 2 — One state store, one tool contract (~medium)

**2.1 Single workflow state.** Make `menu_activity.json` (moved into `/Users/Shared/cooking-state/`) the ONLY workflow state; sms-assistant reads state through `get_workflow_state` and stops maintaining a parallel `state` field in `menu_session.json` (keep that file for conversation history only). Delete `_sync_session_state` (menu_workflow.py:102) and every call site. `server.py` routing (line 954) reads the bridge state via one cached call per message, falling back to conversation-file presence.

**2.2 Contract test.** New test in sms-assistant `tests/test_tool_contract.py`: for each tool schema in `_build_menu_tools()` (menu_workflow.py:520) plus every `call_menubuilder_tool(...)` literal (grep), assert the target function exists in `mcp/menu_server.py` and its signature accepts the passed kwargs (run via the MenuBuilder venv python, `inspect.signature`). Wire it into pre-flight 0.2 (run weekly) or a pre-push hook.

**2.3 Outbox hardening.** Replace multi-writer `.outbox.json` read-modify-write with an append-only `outbox/` spool dir (one JSON file per message, `os.O_CREAT|O_EXCL`), drained by `server.py`. This removes the cross-process race without needing locks that don't work across users anyway.

### Phase 3 — UX guardrails so David can't fall out of the workflow (~small)

**3.1 (sms-assistant)** `system_prompts/menu.txt`: add a short section — "Weekly menu builds happen in a separate workflow. If David asks to build/plan the weekly menu and no workflow is active, tell him to text 'start menu' (or that you'll start it), and do NOT improvise meal planning with get_recipe." Optionally give the admin's general agent a `start_menu_workflow` tool that calls `menu_workflow.handle_start`.
**3.2** Add a "menu status" admin keyword in `server.py` that reports bridge state + session state + last trigger date — first debugging step becomes a text message instead of ssh.
**3.3** Document "recycle koala" (reset) and "menu status" in README.

### Phase 4 — Engineering hygiene (~medium, background)

- **4.1** Pin `requirements.txt` versions on both projects; document the two interpreters (bot: 3.9 CLT — consider `brew install python@3.12` for the bot to end the split).
- **4.2** Add `ruff` (catches the `NameError: re` class of bug statically) + run `tests/` and `evals/runner.py` in a pre-push hook.
- **4.3** De-duplicate scoring: `mcp/menu_server.py` `_load_candidates`/`_candidate_score` "mirrors suggest_meals.py" — extract one shared module in MenuBuilder and import it from both.
- **4.4** Log rotation for `keanu.log`; bridge stderr should be logged in full on JSON parse failure.
- **4.5** Secrets: move ATK password, YouTube API key, Flask secret out of `config.json` into `.env` (gitignored already); rotate the YouTube key. Add an ATK-cookie-expiry check to pre-flight (warn when `cookies_fetched_at` + refresh-token lifetime < 7 days out).
- **4.6** Receipt parser: request JSON via a `tool` schema or strip to first `{...}` block before parsing; sniff image magic bytes instead of trusting mime labels.

### Phase 5 — Roadmap (not now)
- Budget integration: `grocery_budget_status.json` hook already specced in CLAUDE.md step 3 — implement after reliability work.
- Cuisine discovery pressure: the `_candidate_score` new-recipe bonus is working; consider a "new cuisine this month" nudge in `_select_meals` once Sundays are boring.

---

## Priority order for handoff

| # | Task | Project | Size |
|---|------|---------|------|
| 1 | 0.1 in-loop Sunday trigger | sms-assistant | S |
| 2 | 0.2 pre-flight + failure SMS | sms-assistant | S |
| 3 | 0.3 pmset + remove dup plist (David, manual) | ops | XS |
| 4 | 1.1–1.2 state dir + ACLs + repoint | both | S |
| 5 | 1.3 stop config writes, clean map | MenuBuilder | XS |
| 6 | 3.1–3.3 workflow guardrails in prompt/routing | sms-assistant | S |
| 7 | 2.1 single state store | both | M |
| 8 | 2.2 contract test | sms-assistant | S |
| 9 | 2.3 outbox spool | both | S |
| 10 | Phase 4 items | both | M |

Rules for the executing session: change one phase at a time; after each phase run `python3 workflow_smoketest.py` (MenuBuilder) and `tests/` (sms-assistant) plus a manual `trigger_menu.py` dry run; do not refactor beyond the task; never edit files in the other project — write instructions instead.
