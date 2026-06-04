# Handoff: Sunday AM SMS Workflow

**Project**: sms-assistant (`/Users/Shared/sms-assistant/`)  
**Date**: Jun 2026  
**Requested by**: MenuBuilder session

---

## Overview

Build the Sunday 9 AM auto-generation workflow. A launchd plist (already created at
`~/Library/LaunchAgents/com.menubuilder.sundaymenu.plist`) fires at 9 AM every Sunday
and runs `/Users/Shared/sms-assistant/trigger_menu.py`. The trigger starts the workflow
and sends the opening message to David via SMS. The rest of the flow is sequential SMS
back-and-forth until candidates are approved, then continues through Ashley's approval
and finalization.

---

## Architecture: two-phase dispatch

The current `dispatch()` reads state exclusively from the MCP bridge
(`call_menubuilder_tool("get_workflow_state")`). The local `_handle_schedule` and
`_handle_cuisine` functions already exist but are dead code — `dispatch()` never calls
them.

The new flow splits into two halves:

**Local phase** (dispatch reads from local session state):
- `awaiting_meal_logging` → `_handle_meal_logging()` (sequential, new)
- `awaiting_schedule` → `_handle_schedule()` (already written, now wired)
- `awaiting_cuisine` → `_handle_cuisine()` (already written, now wired)

**Bridge phase** (dispatch reads from MCP bridge, same as today):
- `awaiting_meal_approval` → `approve_menu` / `swap_meal` bridge tools
- `awaiting_ashley_signoff` → unchanged
- `awaiting_idea_activation` → unchanged
- `awaiting_finalization` → unchanged

**Seam between phases**: when `_handle_cuisine()` finishes selecting meals, it must call
`_sync_session_state("awaiting_meal_approval")` to hand off to the bridge leg. From that
point forward, dispatch reads from the bridge as today.

---

## 1. New file: `trigger_menu.py`

Create `/Users/Shared/sms-assistant/trigger_menu.py`. Called by launchd at 9 AM Sunday.

```python
#!/usr/bin/env python3
"""
trigger_menu.py — Sunday 9 AM launchd entry point.

Calls handle_start() directly and sends the opening message to the admin
via Keanu's HTTP API. Does not go through the SMS routing layer.
"""
import json
import logging
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import yaml
from agents import menu_workflow

logging.basicConfig(
    filename="/Users/Shared/sms-assistant/trigger_menu.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def load_config() -> dict:
    settings_path = Path("/Users/Shared/sms-assistant/config/settings.yaml")
    with open(settings_path) as f:
        return yaml.safe_load(f)


def send_via_keanu(handle: str, text: str, port: int = 5050) -> bool:
    payload = json.dumps({"handle": handle, "text": text}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/send",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            log.info(f"Sent opening message to {handle}, status={resp.status}")
            return True
    except urllib.error.URLError as e:
        log.error(f"Could not reach Keanu at port {port}: {e}")
        return False


def main():
    log.info("Sunday menu trigger fired")
    try:
        config = load_config()
        admin_handle = config["security"].get("menu_admin")
        if not admin_handle:
            log.error("No menu_admin in config — aborting")
            return

        reply = menu_workflow.handle_start(config)
        log.info(f"handle_start returned: {reply[:80]}")

        if not send_via_keanu(admin_handle, reply):
            log.error("Failed to send opening message — Keanu may not be running")

    except Exception as e:
        log.exception(f"Trigger failed: {e}")


if __name__ == "__main__":
    main()
```

---

## 2. Modify `handle_start()` in `agents/menu_workflow.py`

**Current behavior**: dumps full meal list, asks for feedback, requires "done".

**New behavior**:
- Open with "Let's make this week's menu."
- Find meals where `sms_feedback is None`
- If none → skip straight to schedule (set local state `awaiting_schedule`)
- If some → store `feedback_queue` in session, ask about first missing meal

```python
def handle_start(config: dict) -> str:
    """idle → awaiting_meal_logging (local) or awaiting_schedule (local)."""
    result = call_menubuilder_tool("start_menu_workflow")
    if "error" in result:
        return "Sorry, couldn't start the menu workflow. Check the logs."

    meals = result.get("last_week_meals", [])
    missing = [m for m in meals if not m.get("sms_feedback")]

    session = _load_session()
    session["last_week_meals"] = meals
    session["week_start"] = result.get("week_start", "")

    if not missing:
        # All feedback captured — skip to schedule
        session["state"] = "awaiting_schedule"
        _save_session(session)
        return "Let's make this week's menu.\n\nAny schedule changes this week?"

    session["state"] = "awaiting_meal_logging"
    session["feedback_queue"] = [m["name"] for m in missing]
    _save_session(session)

    first = missing[0]["name"]
    return f"Let's make this week's menu.\n\nHow did {first} go?"
```

---

## 3. Modify `_handle_meal_logging()` in `agents/menu_workflow.py`

**Current behavior**: free-form feedback, fuzzy-matches to any meal, requires "done".

**New behavior**: sequential — record reply for current meal, ask about next, auto-advance
to schedule when queue is empty. No "done" keyword needed.

```python
def _handle_meal_logging(text: str, session: dict, config: dict) -> str:
    meals = session.get("last_week_meals", [])
    queue = session.get("feedback_queue", [])

    # Record reply against the current (first in queue) meal
    if queue:
        current_name = queue[0]
        for meal in meals:
            if meal["name"] == current_name:
                meal["sms_feedback"] = text.strip()
                break
        queue = queue[1:]

    session["last_week_meals"] = meals
    session["feedback_queue"] = queue
    _save_session(session)

    if queue:
        return f"How did {queue[0]} go?"

    # All done — write metadata updates, clear feedback file, advance to schedule
    _update_metadata_for_cooked_meals(meals)
    if not DRY_RUN:
        FEEDBACK_CURRENT_FILE.write_text(json.dumps({"entries": []}, indent=2))

    session["state"] = "awaiting_schedule"
    _save_session(session)
    return "Any schedule changes this week?"
```

---

## 4. Wire `_handle_schedule()` and `_handle_cuisine()` — already written, not yet called

Both functions exist and are correct. No logic changes needed except at the seam.

**Critical seam — end of `_handle_cuisine()`:**

The MCP bridge (`get_workflow_state`) reads from `MenuBuilder/menu_activity.json`.
The local phase writes to `menu_session.json` via `_sync_session_state`. These are
two different files — `_sync_session_state("awaiting_meal_approval")` alone does NOT
update the bridge state.

To fix the seam, `_handle_cuisine()` must call the new `advance_to_meal_approval` MCP
tool after selecting meals. This writes `selected_meals` + `state: awaiting_meal_approval`
into `menu_activity.json` so the bridge phase works correctly on the next message.

At the end of `_handle_cuisine()`, replace:
```python
session["state"] = "awaiting_meal_approval"
_save_session(session)
return _format_numbered_list(selected, week_start, quick_days)
```

With:
```python
# Hand off to bridge: write selected meals into MenuBuilder's activity file
call_menubuilder_tool(
    "advance_to_meal_approval",
    selected_meals=selected,
    quick_days=quick_days,
    schedule_notes=session.get("schedule_notes", []),
    cuisine_direction=session.get("cuisine_direction", ""),
)
session["state"] = "awaiting_meal_approval"
_save_session(session)
return _format_numbered_list(selected, week_start, quick_days)
```

The `advance_to_meal_approval` tool was added to MenuBuilder's `mcp/menu_server.py`
specifically for this seam.

---

## 5. Modify `_format_numbered_list()` in `agents/menu_workflow.py`

**Current format** (numbered, with dates and cook times):
```
1. Sun 6/1: Herb-Roasted Chicken (1 hr 30 min)
2. Mon 6/2: Korean Chicken Bulgogi (30 min)
```

**New format** — compact, day name only, no numbers, no dates, no cook times:
```
Sun: Herb-Roasted Chicken
Mon: Korean Chicken Bulgogi
Tue: Yakisoba
Wed: Coconut Chicken Curry
Thu: Pasta alla Vodka
Fri: Blackened Cod Tacos
Sat: Smash Burgers
```

```python
def _format_numbered_list(selected: dict, week_start: date, quick_days: Optional[list] = None) -> str:
    """Format selected meals as compact Sun-Sat list for SMS approval."""
    ordered = [(day, selected[day]) for day in DAYS_ORDER if day in selected]
    lines = [f"{day}: {name}" for day, name in ordered]
    return "\n".join(lines)
```

**Note**: swap commands now use day names only — "swap Wed to tacos", not "swap 3 to
tacos". `_parse_swap()` already handles day-named swaps so no changes needed there.

---

## 6. Rewrite `dispatch()` in `agents/menu_workflow.py`

This is the main structural change. `dispatch()` must now check local session state for
the early phase and fall through to the bridge for the later phase.

```python
def dispatch(text: str, session: dict, config: dict) -> str:
    """Route to correct handler based on current workflow state."""

    # Hold — leave state as-is, user picks up on Mac
    if any(p in text.lower() for p in ("hold", "pause", "not now", "later", "stop for now")):
        return "Got it — pick it up on your Mac whenever you're ready."

    # --- Local phase: read from session file ---
    local_state = session.get("state", "idle")

    if local_state == "awaiting_meal_logging":
        return _handle_meal_logging(text, session, config)

    if local_state == "awaiting_schedule":
        return _handle_schedule(text, session, config)

    if local_state == "awaiting_cuisine":
        return _handle_cuisine(text, session, config)

    # --- Bridge phase: read from MCP bridge ---
    state_result = call_menubuilder_tool("get_workflow_state")
    state = state_result.get("state", "idle")

    if state in ("idle", "complete", None):
        return ""

    if state == "awaiting_meal_approval":
        lowered = text.lower().strip()
        approval = ("looks good", "good", "ok", "okay", "approved", "go ahead", "perfect",
                    "great", "sounds good", "yes", "yep", "yeah")
        if any(lowered == p or lowered.startswith(p + " ") for p in approval):
            result = call_menubuilder_tool("approve_menu")
            _sync_session_state(result.get("state", "awaiting_ashley_signoff"))
            return "Sent to Ashley."
        else:
            result = call_menubuilder_tool("swap_meal", reason=text)
            _sync_session_state(result.get("state", state))
            selected = result.get("selected_meals", {})
            week_start = date.fromisoformat(result.get("week_start", date.today().isoformat()))
            if selected:
                return _format_numbered_list(selected, week_start)
            return (
                "Say 'looks good' to approve, or tell me what to change — "
                "e.g. 'swap Wed to pasta' or 'change tuesday to tacos'.\n\n"
                + _format_numbered_list(state_result.get("selected_meals", {}), date.today())
            )

    if state == "awaiting_ashley_signoff":
        return "Waiting on Ashley's OK. I'll let you know when she replies."

    if state == "awaiting_idea_activation":
        pending = state_result.get("pending_idea", "")
        result = call_menubuilder_tool("activate_idea_recipe", name=pending, content=text)
        _sync_session_state(result.get("state", state))
        if result.get("remaining_pending", 0) == 0:
            call_menubuilder_tool("finalize_plan")
            _sync_session_state("complete")
            return "Thanks! Finishing up the plan..."
        next_idea = result.get("next_pending", "")
        return f"Got it! Now paste the content for '{next_idea}'."

    if state == "awaiting_finalization":
        result = call_menubuilder_tool("finalize_plan")
        _sync_session_state("complete")
        return "Plan ready!"

    log.warning(f"Unknown menu workflow state: local={local_state} bridge={state}")
    return ""
```

**Key difference from current**: the old dispatch read bridge state first for everything,
including `awaiting_meal_logging` and `awaiting_suggestions`. The new version reads local
session for the early phase, then falls to the bridge. The `awaiting_suggestions` state is
no longer needed in dispatch — `_handle_cuisine()` produces meals locally and hands off
directly to `awaiting_meal_approval` via `_sync_session_state`.

---

## 7. Install Keanu plist (deployment step — no code change)

The Keanu plist exists at `/Users/Shared/sms-assistant/com.keanu.sms-assistant.plist`
but is not installed in LaunchAgents. Run from terminal (not Claude Code):

```bash
cp /Users/Shared/sms-assistant/com.keanu.sms-assistant.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.keanu.sms-assistant.plist
```

---

## Testing

**Manual trigger test** (fires immediately, no waiting for Sunday):
```bash
cd /Users/Shared/sms-assistant
python3 trigger_menu.py
```
Check `/Users/Shared/sms-assistant/trigger_menu.log` for output.

**launchd test** (verify plist fires):
```bash
launchctl load ~/Library/LaunchAgents/com.menubuilder.sundaymenu.plist
launchctl start com.menubuilder.sundaymenu
```

---

## Known risks

- macOS may prompt for permissions (Full Disk Access, Automation) on first launchd run.
  Grant in System Settings → Privacy & Security.
- Mac must be awake at 9 AM Sunday. launchd fires the missed job on wake but timing drifts.
- Test on the first Sunday after deployment.
