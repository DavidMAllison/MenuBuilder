# Spec: guard against selected_meals drift before approve_menu

**Where**: `/Users/Shared/sms-assistant/agents/menu_workflow.py`
**Why**: On 2026-07-19, `menu_session.json`'s `selected_meals` (what David saw/agreed to
in chat) diverged from `menu_activity.json`'s `selected_meals` (what actually got sent
to Ashley) for 4 of 7 days. Likely cause: a concurrent write to the shared activity
file from another process (a debugging session was active around the same time —
"some things have been fixed" appears in the transcript right before the divergent
swaps). MenuBuilder's `approve_menu` MCP tool now accepts an optional
`expected_selected_meals` param and refuses to send if it doesn't match the actual
activity state (see `mcp/menu_server.py` — already deployed, committed on the
MenuBuilder side). This spec wires the sms-assistant side up to use it.

## Change 1 — main approve_menu call site (~line 768)

Current:
```python
    if tool_name == "approve_menu":
        result = call_menubuilder_tool("approve_menu")
        if "error" in result:
            log.error(f"approve_menu MCP error: {result['error']}")
            return "Something went wrong sending the menu to Ashley — check the logs."
        # Generate shopping list now that meals are locked
        sl_result = call_menubuilder_tool(
            "generate_shopping_list",
            meals=session.get("selected_meals", {}),
            week_start=session.get("week_start", ""),
        )
        if "error" in sl_result:
            log.warning(f"generate_shopping_list failed: {sl_result['error']}")
        # awaiting_ashley_signoff is bridge-owned — hand state over to MenuBuilder
        session.pop("state", None)
        _save_session(session)
        return "Menu sent to Ashley."
```

Replace with:
```python
    if tool_name == "approve_menu":
        result = call_menubuilder_tool(
            "approve_menu",
            expected_selected_meals=session.get("selected_meals", {}),
        )
        if result.get("error") == "selected_meals_mismatch":
            log.warning(
                f"approve_menu blocked — plan drifted. "
                f"expected={result.get('expected')} actual={result.get('actual')}"
            )
            session["selected_meals"] = result.get("actual", {})
            _save_session(session)
            week_start = date.fromisoformat(session["week_start"])
            plan = _format_numbered_list(
                session["selected_meals"], week_start, session.get("quick_days", [])
            )
            return (
                "Hold on — the plan changed since we last talked about it "
                "(maybe another session touched it). Here's what's actually "
                f"current:\n\n{plan}\n\nSay \"send it\" again if this looks "
                "right, or make changes first."
            )
        if "error" in result:
            log.error(f"approve_menu MCP error: {result['error']}")
            return "Something went wrong sending the menu to Ashley — check the logs."
        # Generate shopping list now that meals are locked
        sl_result = call_menubuilder_tool(
            "generate_shopping_list",
            meals=session.get("selected_meals", {}),
            week_start=session.get("week_start", ""),
        )
        if "error" in sl_result:
            log.warning(f"generate_shopping_list failed: {sl_result['error']}")
        # awaiting_ashley_signoff is bridge-owned — hand state over to MenuBuilder
        session.pop("state", None)
        _save_session(session)
        return "Menu sent to Ashley."
```

## Change 2 — _handle_pending_url_swap (~line 461)

This path never synced `session["selected_meals"]` after resolving Ashley's
similar-recipe choice, so its `approve_menu()` call at the end was always
firing against a stale local mirror (a second, independent instance of the
same bug class, unrelated to the concurrency theory above).

Current:
```python
def _handle_pending_url_swap(text: str, session: dict, config: dict):
    """
    Ashley replied to the 'similar recipe exists' question.
    Pending context is in session["pending_url_swap"].
    """
    admin_handle = config["security"].get("menu_admin")
    partner_handle = config["security"].get("partner_handle", "")
    pending = session.get("pending_url_swap", {})
    url = pending.get("url", "")
    day = pending.get("day", "")
    existing = pending.get("existing_recipe", "")

    lower = text.lower()
    if any(w in lower for w in ("new", "add", "different", "that one", "yes", "yeah", "yep")):
        result = call_menubuilder_tool("process_recipe_url", url=url, day=day, force_add=True)
        new_recipe = result.get("recipe", url)
        _send_to_ashley(f"Added and swapped {day} to {new_recipe}.", partner_handle)
        if admin_handle:
            _send_outbox(admin_handle, f"Ashley chose to add new recipe '{new_recipe}' for {day}.")
    else:
        call_menubuilder_tool("swap_meal", day=day, reason="Ashley request", replacement=existing)
        _send_to_ashley(f"Got it — using {existing} for {day}.", partner_handle)
        if admin_handle:
            _send_outbox(admin_handle, f"Ashley chose existing recipe '{existing}' for {day}.")

    session.pop("pending_url_swap", None)
    _save_session(session)
    call_menubuilder_tool("approve_menu")
```

Replace with:
```python
def _handle_pending_url_swap(text: str, session: dict, config: dict):
    """
    Ashley replied to the 'similar recipe exists' question.
    Pending context is in session["pending_url_swap"].
    """
    admin_handle = config["security"].get("menu_admin")
    partner_handle = config["security"].get("partner_handle", "")
    pending = session.get("pending_url_swap", {})
    url = pending.get("url", "")
    day = pending.get("day", "")
    existing = pending.get("existing_recipe", "")

    lower = text.lower()
    if any(w in lower for w in ("new", "add", "different", "that one", "yes", "yeah", "yep")):
        result = call_menubuilder_tool("process_recipe_url", url=url, day=day, force_add=True)
        new_recipe = result.get("recipe", url)
        _send_to_ashley(f"Added and swapped {day} to {new_recipe}.", partner_handle)
        if admin_handle:
            _send_outbox(admin_handle, f"Ashley chose to add new recipe '{new_recipe}' for {day}.")
    else:
        call_menubuilder_tool("swap_meal", day=day, reason="Ashley request", replacement=existing)
        _send_to_ashley(f"Got it — using {existing} for {day}.", partner_handle)
        if admin_handle:
            _send_outbox(admin_handle, f"Ashley chose existing recipe '{existing}' for {day}.")

    session.pop("pending_url_swap", None)

    # process_recipe_url doesn't return the full selected_meals dict (only
    # swapped_day/outgoing_recipe) — refresh from the bridge instead of
    # trusting a partial/stale local mirror.
    wf = call_menubuilder_tool("get_workflow_state")
    if isinstance(wf, dict) and wf.get("selected_meals"):
        session["selected_meals"] = wf["selected_meals"]
    _save_session(session)

    call_menubuilder_tool(
        "approve_menu",
        expected_selected_meals=session.get("selected_meals", {}),
    )
```

## After applying

Run `tests/test_menu_workflow.py` — the existing `approve_menu` test fixtures
will need `expected_selected_meals` accounted for (either passed through in the
mock or asserted against). Then a manual `trigger_menu.py` dry run per the usual
rule for this kind of change.
