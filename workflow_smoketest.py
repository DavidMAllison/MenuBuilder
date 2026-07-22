#!/usr/bin/env python3
"""
workflow_smoketest.py — Saturday pre-flight for the Sunday menu workflow.

Validates every tool, file, permission, and data dependency needed for the
weekly workflow. Simulates the SMS flow with mock replies to catch logic
failures before they happen under Sunday time pressure.

Usage:
    python3 workflow_smoketest.py           # full check + SMS simulation
    python3 workflow_smoketest.py --quick   # pre-flight checks only
    python3 workflow_smoketest.py > report.txt 2>&1   # save to file
"""

import base64
import json
import re
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

# ── Colors (strip when piped to file) ─────────────────────────────────────────
_COLOR = sys.stdout.isatty()
def _c(code, s): return f"\033[{code}m{s}\033[0m" if _COLOR else s
G   = lambda s: _c("92", s)
R   = lambda s: _c("91", s)
Y   = lambda s: _c("93", s)
B   = lambda s: _c("1",  s)
DIM = lambda s: _c("2",  s)

# ── Canonical paths ────────────────────────────────────────────────────────────
HOME      = Path.home()
DROPBOX   = HOME / "Dropbox/LLMContext/cooking"
STATE_DIR = Path("/Users/Shared/cooking-state")
PLANS_DIR = STATE_DIR / "weeklyplan"
META_PATH = DROPBOX / "recipe_metadata.json"
INV_PATH  = STATE_DIR / "inventory.json"
LUNCH_PATH = STATE_DIR / "lunch_state.json"
SCHED_PATH = HOME / "projects/personal/FamilySchedule/schedule.json"
PROJECT   = HOME / "projects/personal/MenuBuilder"
CONFIG_PATH = PROJECT / "config.json"
FEEDBACK  = PLANS_DIR / "feedback_current.json"
TCC_DB    = HOME / "Library/Application Support/com.apple.TCC/TCC.db"

SHOPPING_APP   = Path("/Applications/WeeklyShoppingList.app")
CALENDAR_APP   = Path("/Applications/WeeklyMealCalendar.app")
CAL_HELPER     = PROJECT / "parse_meal_calendar.py"
SUGGEST_SCRIPT = PROJECT / "suggest_meals.py"
RECIPES_DIR    = DROPBOX / "recipes"
GH_REPO        = HOME / "projects/personal/menubuilder-recipes"

# ── Result tracking ────────────────────────────────────────────────────────────
_results: list[tuple[bool, str]] = []

def check(label: str, fn):
    """Run fn(); record PASS/FAIL. fn() returns a detail string or None."""
    try:
        detail = fn()
        _results.append((True, label))
        tail = f"  {DIM('(' + str(detail) + ')') if detail else ''}"
        print(f"  {G('[PASS]')} {label}{tail}")
        return True
    except AssertionError as e:
        _results.append((False, label))
        print(f"  {R('[FAIL]')} {label}  → {e}")
        return False
    except Exception as e:
        _results.append((False, label))
        print(f"  {R('[FAIL]')} {label}  → {type(e).__name__}: {e}")
        return False

def section(title: str):
    print(f"\n{B(title)}")
    print("─" * 60)

def note(msg: str):
    print(f"       {DIM(msg)}")

def step(n: int, msg: str):
    print(f"  {Y(f'[STEP {n}]')} {msg}")

def mock_reply(msg: str):
    print(f"  {DIM('[MOCK]')}   ← {msg}")

# ── Pre-flight check functions ─────────────────────────────────────────────────

def _check_metadata():
    data = json.loads(META_PATH.read_text())
    recipes = data.get("recipes", {})
    active = sum(1 for r in recipes.values() if r.get("status") == "active")
    assert active > 0, "no active recipes"
    return f"{active} active recipes"

def _check_latest_plan():
    plans = sorted(PLANS_DIR.glob("mealplan_*.json"), reverse=True)
    assert plans, "no meal plan JSON found"
    plan = json.loads(plans[0].read_text())
    meals = plan.get("meals", [])
    assert meals, "plan has no meals"
    return f"{plans[0].name}  {len(meals)} meals  week_start={plan.get('week_start','?')}"

def _check_feedback():
    assert FEEDBACK.exists(), f"missing {FEEDBACK}"
    data = json.loads(FEEDBACK.read_text())
    n = len(data.get("entries", []))
    return f"{n} pending entries"

def _check_inventory():
    assert INV_PATH.exists(), f"missing {INV_PATH}"
    data = json.loads(INV_PATH.read_text())
    items = data.get("items", data)
    count = len(items) if isinstance(items, (list, dict)) else "?"
    return f"{count} items"

def _check_lunch_state():
    assert LUNCH_PATH.exists(), f"missing {LUNCH_PATH}"
    data = json.loads(LUNCH_PATH.read_text())
    status = data.get("status", "unknown")
    pick   = data.get("current_pick") or "none"
    return f"status={status}  pick={pick!r}"

def _check_schedule():
    assert SCHED_PATH.exists(), f"missing {SCHED_PATH}"
    data = json.loads(SCHED_PATH.read_text())
    assert data, "schedule file is empty"
    return "readable"

def _jwt_expiry(token: str):
    """Decode a JWT's payload (no signature check -- informational only)
    and return its 'exp' claim as a UTC datetime, or None if absent/unparseable."""
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        exp = payload.get("exp")
        return datetime.fromtimestamp(exp, tz=timezone.utc) if exp else None
    except Exception:
        return None

def _check_atk_cookie_expiry():
    if not CONFIG_PATH.exists():
        return "config.json not found — skipped"
    cfg = json.loads(CONFIG_PATH.read_text())
    refresh_token = cfg.get("atk", {}).get("cookies", {}).get("refresh_token", "")
    if not refresh_token:
        return "no ATK session cached yet — will login on next atk_agent run"

    expiry = _jwt_expiry(refresh_token)
    assert expiry, "ATK refresh_token present but not a decodable JWT"

    days_left = (expiry - datetime.now(timezone.utc)).total_seconds() / 86400
    assert days_left > 0, f"ATK refresh token EXPIRED {abs(days_left):.1f} days ago — atk_agent will need a fresh Playwright login"

    if days_left < 7:
        return f"⚠ expires in {days_left:.1f} days — re-login will trigger soon"
    return f"expires in {days_left:.0f} days"

def _check_github_publish():
    # Several intake paths write .md files but only the Review UI auto-publishes.
    # Any active recipe missing from the Pages repo means a 404 in that week's plan links.
    import unicodedata
    norm = lambda s: unicodedata.normalize("NFC", s)  # macOS stores filenames NFD
    recipes = json.loads(META_PATH.read_text()).get("recipes", {})
    repo_files = {norm(f.name) for f in GH_REPO.glob("*.md")}
    local_files = {norm(f.name) for f in RECIPES_DIR.glob("*.md")}
    unpublished, orphaned = [], []
    for name, r in recipes.items():
        if not isinstance(r, dict) or r.get("status") != "active":
            continue
        fn = norm(r.get("filename") or (name.replace(" ", "_") + ".md"))
        if fn in repo_files:
            continue
        (unpublished if fn in local_files else orphaned).append(name)
    assert not unpublished, (
        f"{len(unpublished)} active recipe(s) not on GitHub Pages (plan links will 404): "
        + ", ".join(unpublished[:5])
        + " — copy .md to repo, run generate_github_pages_data.py, push"
    )
    assert not orphaned, (
        f"{len(orphaned)} active recipe(s) have no .md file at all: " + ", ".join(orphaned[:5])
    )
    return "all active recipes published"

def _check_md_structure():
    # Catches structural drift, not just missing files: any active recipe whose
    # .md still embeds Time/Servings/Adapted-from/Source/Watch lines has
    # regressed to the pre-Jun-25 format (duplicates what the Pages template
    # renders from JSON). This has recurred multiple times because new intake
    # paths keep getting added without following the canonical structure —
    # see project_recipe_md_structure memory. This check is the actual
    # enforcement; don't rely on generator code review alone to catch it.
    metadata_line = re.compile(
        r'^\s*[*_]{0,2}(Time|Servings|Serves|Yield|Source|Watch)[*_]{0,2}\s*:',
        re.IGNORECASE,
    )
    adapted_line = re.compile(r'^\s*[*_]*Adapted from', re.IGNORECASE)
    recipes = json.loads(META_PATH.read_text()).get("recipes", {})
    active_filenames = {
        r.get("filename") or (name.replace(" ", "_") + ".md")
        for name, r in recipes.items()
        if isinstance(r, dict) and r.get("status") == "active"
    }
    offenders = []
    for f in RECIPES_DIR.glob("*.md"):
        if f.name not in active_filenames:
            continue
        lines = f.read_text(encoding="utf-8").splitlines()[:12]
        if any(metadata_line.match(l) or adapted_line.match(l) for l in lines):
            offenders.append(f.name)
    assert not offenders, (
        f"{len(offenders)} recipe(s) have embedded Time/Servings/Adapted-from lines "
        "(pre-Jun-25 format, duplicates the JSON-rendered .meta bar): "
        + ", ".join(offenders[:5])
    )
    return f"{len(active_filenames)} active recipes, no structural drift"

def _check_shopping_csv():
    csvs = sorted(PLANS_DIR.glob("shopping_*.csv"), reverse=True)
    assert csvs, "no shopping CSV found"
    lines = csvs[0].read_text().splitlines()
    assert len(lines) > 1, "CSV has no data rows"
    return f"{csvs[0].name}  {len(lines)-1} items"

def _check_shopping_app():
    applet = SHOPPING_APP / "Contents/MacOS/applet"
    assert SHOPPING_APP.exists(), "app not found"
    assert applet.exists(), "binary is not an AppleScript applet — was it replaced with a Python script?"
    return "applet binary present"

def _check_calendar_app():
    applet = CALENDAR_APP / "Contents/MacOS/applet"
    assert CALENDAR_APP.exists(), "app not found"
    assert applet.exists(), "binary is not an AppleScript applet — needs rebuild (see feedback_weeklymealcalendar_open.md)"
    return "applet binary present"

def _check_cal_helper():
    assert CAL_HELPER.exists(), f"missing {CAL_HELPER}"
    r = subprocess.run([sys.executable, str(CAL_HELPER)], capture_output=True, text=True, timeout=15)
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    if r.returncode != 0 and not lines:
        raise AssertionError(f"script error: {r.stderr[:200]}")
    dinners = sum(1 for l in lines if l.startswith("DINNER"))
    lunches = sum(1 for l in lines if l.startswith("LUNCH"))
    return f"{dinners} dinners  {lunches} lunches"

def _tcc_query(client_like: str, indirect_obj: str) -> str:
    """Return matching client name if Allowed (auth_value=2) entry exists."""
    conn = sqlite3.connect(str(TCC_DB))
    rows = conn.execute(
        "SELECT client, auth_value FROM access "
        "WHERE service='kTCCServiceAppleEvents' "
        "  AND indirect_object_identifier=? "
        "  AND client LIKE ?",
        (indirect_obj, f"%{client_like}%"),
    ).fetchall()
    conn.close()
    allowed = [c for c, v in rows if v == 2]
    assert allowed, (
        f"no Allowed TCC entry matching '{client_like}' → {indirect_obj}\n"
        f"       Fix: run 'open /Applications/{client_like}.app' and approve the permission dialog"
    )
    return f"TCC client: {allowed[0]}"

def _check_tcc_calendar():
    return _tcc_query("WeeklyMealCalendar", "com.apple.iCal")

def _check_tcc_reminders():
    return _tcc_query("WeeklyShoppingList", "com.apple.reminders")

def _check_review_server():
    try:
        resp = urllib.request.urlopen("http://localhost:5051/", timeout=3)
        return f"HTTP {resp.status}"
    except Exception as e:
        raise AssertionError(f"not reachable on :5051 — is recipe_review_server running?  ({e})")

def _check_mcp_state():
    state_path = STATE_DIR / "menu_activity.json"  # keep in sync with mcp/menu_server.py ACTIVITY_FILE
    if not state_path.exists():
        return "no state file (idle)"
    data = json.loads(state_path.read_text())
    state = data.get("state", "")
    if state not in ("idle", "complete", ""):
        raise AssertionError(
            f"workflow state is '{state}' — a previous session may be in-progress\n"
            f"       Check {state_path}"
        )
    return f"state={state!r}"

def _check_suggest_meals():
    r = subprocess.run([sys.executable, str(SUGGEST_SCRIPT)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"exit {r.returncode}: {r.stderr[:200]}"
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    assert lines, "no candidates output"
    if len(lines) < 7:
        raise AssertionError(f"only {len(lines)} candidates — pool is thin (need 7+ for a full week)")
    return f"{len(lines)} candidates"

# ── SMS workflow simulation ────────────────────────────────────────────────────

def simulate_workflow():
    section("SMS WORKFLOW SIMULATION  (mock replies · no mutations)")

    plans = sorted(PLANS_DIR.glob("mealplan_*.json"), reverse=True)
    current = json.loads(plans[0].read_text()) if plans else {}
    prev    = json.loads(plans[1].read_text()) if len(plans) > 1 else {}

    # Step 0 — drain feedback queue
    step(0, "Drain SMS feedback queue")
    try:
        entries = json.loads(FEEDBACK.read_text()).get("entries", []) if FEEDBACK.exists() else []
        disliked = [e.get("recipe_name", "?") for e in entries if e.get("sentiment") == "disliked"]
        mixed    = [e.get("recipe_name", "?") for e in entries if e.get("sentiment") == "mixed"]
        if disliked: note(f"⚠  Disliked (flagged for tombstone): {disliked}")
        if mixed:    note(f"⚠  Mixed feedback (surface before planning): {mixed}")
        _results.append((True, "Step 0: drain feedback queue"))
        print(f"  {G('[PASS]')} Queue: {len(entries)} entries ({len(disliked)} disliked, {len(mixed)} mixed)")
    except Exception as e:
        _results.append((False, "Step 0: drain feedback queue"))
        print(f"  {R('[FAIL]')} {e}")

    # Step 1 — log last week's meals
    step(1, "Log last week's meals")
    try:
        meals = prev.get("meals", [])
        meta  = json.loads(META_PATH.read_text()).get("recipes", {})
        not_found = [m.get("title", "?") for m in meals if m.get("title") not in meta]
        if not_found:
            note(f"⚠  Titles not in metadata (lookup would fail): {not_found}")
        _results.append((True, "Step 1: meal logging"))
        note(f"Previous plan: {plans[1].name if len(plans) > 1 else 'none'}  ({len(meals)} meals)")
        print(f"  {G('[PASS]')} {len(meals)} meals would be logged")
    except Exception as e:
        _results.append((False, "Step 1: meal logging"))
        print(f"  {R('[FAIL]')} {e}")

    # Step 2 — schedule check
    step(2, "Check FamilySchedule for quick-cook evenings")
    try:
        sched = json.loads(SCHED_PATH.read_text())
        overrides = sched.get("weekly_overrides", {})
        today = date.today()
        upcoming = [k for k in overrides if k >= str(today)][:5]
        note(f"Upcoming overrides (next 5): {upcoming or 'none'}")
        _results.append((True, "Step 2: schedule check"))
        print(f"  {G('[PASS]')} Schedule readable")
    except Exception as e:
        _results.append((False, "Step 2: schedule check"))
        print(f"  {R('[FAIL]')} {e}")

    # Step 3 — candidate filter
    step(3, "Run suggest_meals.py  (candidate filter)")
    try:
        r = subprocess.run([sys.executable, str(SUGGEST_SCRIPT)], capture_output=True, text=True, timeout=30)
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        if r.returncode != 0:
            raise AssertionError(r.stderr[:200])
        thin = len(lines) < 7
        if thin: note(f"⚠  Only {len(lines)} candidates — consider running fill_menu_ideas.py first")
        for l in lines[:5]: note(l)
        if len(lines) > 5: note(f"  … and {len(lines)-5} more")
        _results.append((not thin, "Step 3: candidate filter"))
        status = G("[PASS]") if not thin else Y("[WARN]")
        print(f"  {status} {len(lines)} candidates")
    except Exception as e:
        _results.append((False, "Step 3: candidate filter"))
        print(f"  {R('[FAIL]')} {e}")

    # Step 4 — menu proposal (mock)
    step(4, "Propose menu to David via SMS  [MOCK]")
    mock_reply("Swap Wednesday to something lighter, rest looks good")
    note("Would call swap_meal MCP for Wednesday, then advance_to_meal_approval")
    _results.append((True, "Step 4: menu proposal (mock)"))
    print(f"  {G('[PASS]')} Mock proposal accepted")

    # Step 5 — Ashley signoff (mock)
    step(5, "Send proposed menu to Ashley via SMS  [MOCK]")
    mock_reply("Looks great!")
    note("Would call handle_ashley_reply → approve_menu if no swaps requested")
    _results.append((True, "Step 5: Ashley signoff (mock)"))
    print(f"  {G('[PASS]')} Mock signoff accepted")

    # Step 6 — plan validation
    step(6, "Validate current plan JSON")
    try:
        meals = current.get("meals", [])
        assert meals, "no meals in current plan"
        missing_urls = [m.get("title", "?") for m in meals if not m.get("url")]
        missing_health = [m.get("title", "?") for m in meals if not m.get("health")]
        if missing_urls:   note(f"⚠  Missing URLs: {missing_urls}")
        if missing_health: note(f"⚠  Missing health classification: {missing_health}")
        hh = sum(1 for m in meals if "Heart" in (m.get("health") or ""))
        note(f"Balance: {hh} heart-healthy, {len(meals)-hh} other")
        _results.append((True, "Step 6: plan validation"))
        print(f"  {G('[PASS]')} Plan: {len(meals)} meals")
    except AssertionError as e:
        _results.append((False, "Step 6: plan validation"))
        print(f"  {R('[FAIL]')} {e}")

    # Step 7 — shopping CSV check
    step(7, "Validate shopping CSV")
    try:
        csvs = sorted(PLANS_DIR.glob("shopping_*.csv"), reverse=True)
        assert csvs, "no CSV found"
        lines = csvs[0].read_text().splitlines()
        assert len(lines) > 1, "CSV is empty"
        note(f"{csvs[0].name}  ({len(lines)-1} items)")
        _results.append((True, "Step 7: shopping CSV"))
        print(f"  {G('[PASS]')} {len(lines)-1} items")
    except AssertionError as e:
        _results.append((False, "Step 7: shopping CSV"))
        print(f"  {R('[FAIL]')} {e}")

    # Step 8 — calendar events preview
    step(8, "Preview calendar events  (parse_meal_calendar.py dry-run)")
    try:
        r = subprocess.run([sys.executable, str(CAL_HELPER)], capture_output=True, text=True, timeout=15)
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        if r.returncode != 0 and not lines:
            raise AssertionError(r.stderr[:200])
        for l in lines: note(l[:90])
        dinners = sum(1 for l in lines if l.startswith("DINNER"))
        lunches = sum(1 for l in lines if l.startswith("LUNCH"))
        _results.append((True, "Step 8: calendar preview"))
        print(f"  {G('[PASS]')} Would create {dinners} dinner events + {lunches} lunch events")
    except AssertionError as e:
        _results.append((False, "Step 8: calendar preview"))
        print(f"  {R('[FAIL]')} {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    quick = "--quick" in sys.argv

    print(f"\n{B('MenuBuilder Workflow Smoke Test')}")
    print(DIM(str(date.today())))

    section("DATA FILES")
    check("recipe_metadata.json",  _check_metadata)
    check("latest meal plan",      _check_latest_plan)
    check("feedback_current.json", _check_feedback)
    check("inventory.json",        _check_inventory)
    check("lunch_state.json",      _check_lunch_state)
    check("FamilySchedule",        _check_schedule)
    check("shopping CSV",          _check_shopping_csv)
    check("GitHub Pages publish",  _check_github_publish)
    check("Recipe .md structure",  _check_md_structure)
    check("ATK session expiry",    _check_atk_cookie_expiry)

    section("APP BINARIES")
    check("WeeklyShoppingList.app",  _check_shopping_app)
    check("WeeklyMealCalendar.app",  _check_calendar_app)
    check("parse_meal_calendar.py",  _check_cal_helper)

    section("TCC PERMISSIONS")
    check("WeeklyMealCalendar → Calendar",   _check_tcc_calendar)
    check("WeeklyShoppingList → Reminders",  _check_tcc_reminders)

    section("SERVER & WORKFLOW STATE")
    check("recipe_review_server  (port 5051)", _check_review_server)
    check("MCP workflow state",                _check_mcp_state)

    section("MEAL CANDIDATES")
    check("suggest_meals.py  (≥7 candidates)", _check_suggest_meals)

    if not quick:
        simulate_workflow()

    # ── Summary ────────────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    passed = sum(1 for ok, _ in _results if ok)
    failed = [label for ok, label in _results if not ok]
    total  = len(_results)

    if not failed:
        print(B(G(f"✓  {passed}/{total} checks passed — WORKFLOW READY")))
    else:
        print(B(R(f"✗  {len(failed)}/{total} checks FAILED")))
        print(f"\n{B('Failed:')}")
        for label in failed:
            print(f"  {R('✗')} {label}")

    note("\nManual check: verify iCloud storage is not full (Reminders won't sync to phone if full)")

    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
