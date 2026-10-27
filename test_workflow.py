#!/usr/bin/env python3
"""
test_workflow.py — Sandboxed end-to-end test of the planning workflow.

Calls workflow functions directly (no MCP transport) against test_output/
so no production data is touched: no metadata writes, no plan files, no
shopping CSV, no app launches, no SMS.

Usage:
    python3 test_workflow.py                  # full workflow run
    python3 test_workflow.py --keep           # leave test_output in place after run
    python3 test_workflow.py --week 2026-07-07  # plan for a specific week
"""

import argparse
import importlib.util as _ilu
import json
import sys
from datetime import date
from pathlib import Path

# Import the server module directly by file path (avoids mcp package conflict)
PROJECT = Path(__file__).parent
_spec = _ilu.spec_from_file_location("menu_server", PROJECT / "mcp" / "menu_server.py")
srv = _ilu.module_from_spec(_spec)
sys.modules["menu_server"] = srv
_spec.loader.exec_module(srv)

# ── Helpers ───────────────────────────────────────────────────────────────────

_COLOR = sys.stdout.isatty()
def _c(code, s): return f"\033[{code}m{s}\033[0m" if _COLOR else s
def OK(s):   return _c("92", f"  OK  {s}")
def FAIL(s): return _c("91", f" FAIL {s}")
def INFO(s): return _c("2",  f"      {s}")


def _check(label, cond, detail=""):
    if cond:
        print(OK(label))
    else:
        print(FAIL(label))
        if detail:
            print(INFO(detail))
    return cond


passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    ok = _check(label, cond, detail)
    if ok:
        passed += 1
    else:
        failed += 1
    return ok


def check_dish_gate():
    """Pure-function checks for _text_names_specific_dish — no setup needed."""
    cases = [
        ("force pork belly for Sunday", True),
        ("swap to use country ribs", True),
        ("we've had too much chicken this week", False),
        ("prefer ideas", False),
        ("Cuisine open, let's use pork belly, country ribs, chicken", True),
    ]
    for text, expected in cases:
        got = srv._text_names_specific_dish(text)
        check(f"_text_names_specific_dish({text!r})", got == expected, f"got {got}, want {expected}")


def check_dish_boost():
    """
    _select_meals' dish_boost_names wiring, tested directly (bypassing
    _resolve_dish_boost_names/Haiku entirely) with an otherwise-empty
    candidate pool so the single boosted entry is deterministically picked
    — no dependency on random.choice luck across multiple options.
    """
    recipes = srv._load_metadata()
    target = next(
        (name for name, meta in recipes.items()
         if meta.get("times_cooked", 0) == 0
         and meta.get("status") not in ("disliked", "ignored")
         and not meta.get("recommend_hold")
         and str(meta.get("meal_type", "")).strip().lower() not in ("lunch", "weekend")),
        None,
    )
    if target is None:
        check("_select_meals surfaces dish_boost_names entry", False,
              "no eligible never-cooked recipe found to test with")
        return
    selected, _ = srv._select_meals([], [], None, dish_boost_names=[target])
    check("_select_meals surfaces dish_boost_names entry",
          target in selected.values(), f"target={target!r} selected={selected}")


def check_eating_out_reconcile():
    """A "going out" note recorded after meals were picked must still win at finalize."""
    act = {"schedule_notes": ["Going out Sunday \u2014 no dinner needed", "kid soccer Monday 5:00-6:30 PM"],
           "selected_meals": {"Sun": "Chicken Cacciatore", "Mon": "Some Recipe"}}
    changed = srv._reconcile_eating_out(act)
    check("reconcile: out-note overrides picked recipe", act["selected_meals"]["Sun"] == "Going Out to Eat" and changed == ["Sun"], f"{act}")
    check("reconcile: unrelated day untouched", act["selected_meals"]["Mon"] == "Some Recipe")
    act2 = {"schedule_notes": ["Going out Sunday"], "selected_meals": {"Sun": "Real Recipe"}, "eating_out_overrides": ["Sun"]}
    check("reconcile: later swap_meal override respected", srv._reconcile_eating_out(act2) == [] and act2["selected_meals"]["Sun"] == "Real Recipe")


def check_sms_links(candidates, selected):
    """Pre-finalize texts carry a link after each recipe; finalized text is just the site link."""
    check("get_meal_suggestions candidates carry url",
          all(c.get("url") for c in candidates), f"{[c['name'] for c in candidates if not c.get('url')]}")
    urls = srv._selected_meal_urls(selected, srv._load_metadata())
    check("selected_meal_urls parallel to selected_meals",
          all(len(urls[d]) == len(srv._day_meals(v)) for d, v in selected.items()), f"{urls}")
    check("Going Out to Eat has no url", srv._selected_meal_urls({"Sun": "Going Out to Eat"}, {}) == {"Sun": [""]})

    import send_menu_partner as smp
    msg = smp.format_message([
        {"day": "Mon 10/19", "recipe": "A", "urls": ["https://x/A"]},
        {"day": "Tue 10/20", "recipe": ["B", "C"], "urls": ["https://x/B", "https://x/C"]},
        {"day": "Wed 10/21", "recipe": "Going Out to Eat", "urls": [""]},
    ])
    check("approval text: url line follows recipe",
          "Mon: A\nhttps://x/A\n" in msg, msg)
    check("approval text: multi-recipe day joined, one url line each",
          "Tue: B + C\nhttps://x/B\nhttps://x/C\n" in msg, msg)
    check("approval text: no blank url line", "Wed: Going Out to Eat\n\nAll good?" in msg, msg)

    site = srv._CONFIG.get("review_ui_base_url", "").rstrip("/")
    check("finalized text is Plan ready + site link only", srv._plan_ready_text() == f"Plan ready! {site}",
          srv._plan_ready_text())


def check_partner_classify_and_chmod():
    """classify_partner_reply gates reply capture with no side effects; chmod never aborts a write."""
    cases = [
        ("https://www.americastestkitchen.com/recipes/9801-slow-cooker-chicken-tortilla-soup", "unclear"),
        ("Can we do this on Tuesday https://example.com/recipe", "swap_request"),
        ("Looks good ", "approve"),
    ]
    for text, want in cases:
        got = srv.classify_partner_reply(text)
        check(f"classify_partner_reply({text[:40]!r})", got == want, f"got {got}, want {want}")

    import os as _os
    from unittest import mock
    with mock.patch.object(_os, "chmod", side_effect=PermissionError(1, "Operation not permitted")):
        try:
            srv._chmod_shared("/nonexistent")
            ok = True
        except PermissionError:
            ok = False
    check("_chmod_shared swallows non-owner PermissionError", ok)


def check_intake_checks():
    """Offline checks for intake_checks.py (no API calls) -- Miso Chicken, Oct 2026:
    a 24h marinade hidden from `time`, and a sub-recipe the .md never linked."""
    import intake_checks as ic
    from candidate_scoring import parse_minutes
    from recipe_md import build_recipe_md

    e = {"time": "25 minutes"}
    ic.apply_advance_prep(e, {"hours": 24.0, "label": "marinating", "evidence": ""})
    check("advance prep: wait appended to time", e["time"] == "25 minutes plus 24 hours marinating", e["time"])
    check("advance prep: parse_minutes reads active time only", parse_minutes(e["time"]) == 25)
    check("advance prep: blocks quick pick", ic.is_quick_blocked(e))
    check("advance prep: buy a day ahead", ic.buy_ahead_days(e) == 1)
    check("no advance prep: not blocked", not ic.is_quick_blocked({}) and ic.buy_ahead_days({}) == 0)
    check("advance prep: no keyword -> no LLM call, None",
          ic.detect_advance_prep("T", ["Sear the chicken.", "Serve."]) is None)

    missing = ic.unresolved_homemade(
        ["5 Tbsp Homemade All-Purpose Miso Sauce", "1 cup homemade or store-bought broth", "Homemade hummus"],
        ["All-Purpose Miso Sauce"])
    check("homemade: only unlinked, no-alternative lines flagged", missing == ["Homemade hummus"], missing)

    md = build_recipe_md("T", ["5 Tbsp Homemade All-Purpose Miso Sauce"], ["x"],
                         condiments={"All-Purpose Miso Sauce": "https://u/All-Purpose_Miso_Sauce"})
    check("md: sub-recipe linked in ingredient line",
          "- 5 Tbsp Homemade [All-Purpose Miso Sauce](https://u/All-Purpose_Miso_Sauce)" in md, md)
    check("md: Make first line", "**Make first:** [All-Purpose Miso Sauce]" in md, md)

    # Synthetic recipe -- test_output/recipe_metadata_test.json is a stale copy
    # that only refreshes when deleted, so don't depend on what's in it.
    from unittest import mock
    fake = {"Miso Chicken": {
        "ingredients": [{"name": "chicken thighs", "quantity": "5", "unit": "pieces", "category": "Proteins"}],
        "condiment_deps": ["All-Purpose Miso Sauce"],
        "advance_prep_hours": 24.0,
    }}
    sauce = [{"name": "red miso", "quantity": "1", "unit": "cup", "category": "Pantry/Asian"},
             {"name": "mirin", "quantity": "1", "unit": "cup", "category": "Pantry/Asian"}]
    with mock.patch.object(srv, "_load_metadata", return_value=fake), \
         mock.patch.object(srv, "_condiment_ingredients", return_value=sauce), \
         mock.patch.object(srv, "LUNCH_STATE_FILE", Path("/nonexistent")):
        csv = srv._build_shopping_csv({"Tue": "Miso Chicken"}, date(2026, 10, 26))
    check("shopping: sub-recipe ingredients expanded", "red miso" in csv and "mirin" in csv, csv)
    check("shopping: 24h marinade dated the day before", "2026-10-26" in csv and "2026-10-27" not in csv, csv)

    long_wait = {"time": "25 minutes plus 24 hours marinating", "advance_prep_hours": 24.0,
                 "prep_components": ["Marinate chicken 24 hours"]}
    recipes = {"Miso Chicken": long_wait, "Lemon Chicken": {"time": "30 minutes"},
               "Bulgogi": {"time": "20 minutes", "advance_prep_hours": 2.0}}
    check("day-before note: 24h wait",
          ic.day_before_note("Miso Chicken", long_wait) == "Start Miso Chicken tonight (needs 24 hours marinating)")
    check("day-before note: short wait stays same-day", ic.day_before_note("Bulgogi", recipes["Bulgogi"]) == "")
    info = [{"day": "Mon", "title": "Lemon Chicken", "reminder": "Start at 5:30"},
            {"day": "Tue", "title": "Miso Chicken", "reminder": "Start at 5:35"},
            {"day": "Wed", "title": "Bulgogi", "reminder": ""}]
    srv._add_day_before_reminders(info, recipes)
    check("reminder: marinade start lands on the day before",
          info[0]["reminder"] == "Start Miso Chicken tonight (needs 24 hours marinating). Start at 5:30", info[0])
    check("reminder: cook day unchanged", info[1]["reminder"] == "Start at 5:35", info[1])
    first = [{"day": "Sun", "title": "Miso Chicken", "reminder": ""}]
    srv._add_day_before_reminders(first, recipes)
    check("reminder: first night flags the head start on its own day",
          first[0]["reminder"].startswith("Needs a head start: Miso Chicken"), first[0])
    head = srv._tomorrow_head_start({"Mon": "Lemon Chicken", "Tue": "Miso Chicken"}, "Mon", recipes)
    check("prep guide: tonight mode shows tomorrow's marinade",
          head[:2] == ["\nFor tomorrow: Start Miso Chicken tonight (needs 24 hours marinating).",
                       "- Marinate chicken 24 hours"], head)
    check("prep guide: nothing extra when tomorrow has no long wait",
          srv._tomorrow_head_start({"Tue": "Miso Chicken", "Wed": "Bulgogi"}, "Tue", recipes) == [])


def check_github_sync():
    """Publish spool: Keanu (allisonbot) queues, the davidallison LaunchAgent publishes."""
    import json as _json
    import stat
    import tempfile
    from unittest import mock

    import github_sync as gs

    with mock.patch.object(srv, "request_publish") as rp:
        srv._publish(["X.md"])
    check("publish: test mode never queues", not rp.called)

    with tempfile.TemporaryDirectory() as tmp:
        spool = Path(tmp) / "publish_requests"
        with mock.patch.object(gs, "publish_requests_dir", return_value=spool):
            gs.request_publish(["/a/b/Miso_Chicken.md"], [])
            gs.request_publish(["Miso_Chicken.md", "Other.md"], ["All-Purpose_Miso_Sauce.md"])
            gs.request_publish([], [])
            files = sorted(spool.glob("*.json"))
            check("publish: one request file per non-empty call", len(files) == 2, [f.name for f in files])
            check("publish: request file world-writable (cross-account)",
                  stat.S_IMODE(files[0].stat().st_mode) == 0o666, oct(files[0].stat().st_mode))
            check("publish: paths reduced to filenames",
                  _json.loads(files[0].read_text())["recipes"] == ["Miso_Chicken.md"])
            with mock.patch.object(gs, "publish", return_value=True) as pub:
                gs.main()
            args = pub.call_args.args if pub.called else ()
            check("publish: drain merges requests without duplicates",
                  args[:2] == (["Miso_Chicken.md", "Other.md"], ["All-Purpose_Miso_Sauce.md"]), args)
            check("publish: drained requests removed", not list(spool.glob("*.json")))


# ── Main test ─────────────────────────────────────────────────────────────────

def run(week_start_str: str, keep: bool) -> int:
    print(f"\nWorkflow testbed — targeting week {week_start_str}\n")

    # 1. Enable test mode
    result = srv.set_test_mode(True)
    check("set_test_mode(True)", result.get("ok") and result.get("mode") == "test",
          str(result))
    test_dir = Path(result.get("test_dir", ""))
    check("test_output/ exists", test_dir.exists())
    check("recipe_metadata_test.json copied", (test_dir / "recipe_metadata_test.json").exists())

    # Verify _TEST_DIR is set
    check("_TEST_DIR module var set", srv._TEST_DIR is not None)

    # 2. start_menu_workflow
    result = srv.start_menu_workflow(week_start=week_start_str)
    check("start_menu_workflow returns state", result.get("state") == "awaiting_meal_logging",
          str(result.get("state")))
    check("activity saved to test dir",
          (test_dir / "menu_activity.json").exists())

    activity_data = json.loads((test_dir / "menu_activity.json").read_text())
    check("activity week_start matches", activity_data.get("week_start") == week_start_str)
    check("production menu_activity.json untouched",
          not srv.ACTIVITY_FILE.exists() or
          json.loads(srv.ACTIVITY_FILE.read_text()).get("state") != "awaiting_meal_logging")

    # 3. log_meal_feedback — skip to done immediately (no last-week meals in test)
    result = srv.log_meal_feedback("done")
    check("log_meal_feedback(done) advances state",
          result.get("state") == "awaiting_suggestions",
          str(result.get("state")))

    # 4. get_meal_suggestions
    result = srv.get_meal_suggestions()
    check("get_meal_suggestions returns candidates", len(result.get("candidates", [])) > 0)
    check("get_meal_suggestions returns selected_meals",
          bool(result.get("selected_meals")))
    selected = result.get("selected_meals", {})
    check("selected_meals has at least 5 days", len(selected) >= 5)
    check("state is awaiting_meal_approval",
          json.loads((test_dir / "menu_activity.json").read_text()).get("state") == "awaiting_meal_approval")

    # 4b. Dish/protein matching wiring (mocked Haiku call — deterministic, no network)
    check_dish_gate()
    check_dish_boost()
    check_eating_out_reconcile()
    check_sms_links(result.get("candidates", []), selected)
    check_partner_classify_and_chmod()
    check_intake_checks()
    check_github_sync()

    # Weekday-safe only: swap_meal excludes "weekend" meal_type candidates
    # before step 1b ever runs when swap_day isn't Sat/Sun, so a target with
    # meal_type == "weekend" would get filtered out regardless of the Haiku
    # match, making this flaky if picked at random.
    eligible_names = [
        c["name"] for c in srv._load_candidates()
        if c["name"] not in selected.values()
        and str(c.get("meal_type", "")).strip().lower() != "weekend"
    ]
    swap_day = next((d for d in selected if d not in ("Sat", "Sun")), None)
    if swap_day and len(eligible_names) >= 1:
        target = eligible_names[-1]
        orig_match = srv._match_named_dishes
        try:
            srv._match_named_dishes = lambda *a, **k: [target]
            result = srv.swap_meal(day=swap_day, reason="give me the zzzznarwhal dish")
            check("swap_meal uses matched dish when Haiku matches",
                  result.get("new_recipe") == target, str(result))

            srv._match_named_dishes = lambda *a, **k: None
            result2 = srv.swap_meal(day=swap_day, reason="give me the zzzznarwhal dish again")
            check("swap_meal falls back cleanly when Haiku finds no match",
                  bool(result2.get("new_recipe")) and "note" in result2, str(result2))
        finally:
            srv._match_named_dishes = orig_match
    else:
        check("swap_meal dish-matching wiring", False, "no eligible day/candidate to test with")

    # 5. finalize_plan
    result = srv.finalize_plan()
    check("finalize_plan returns state=complete",
          result.get("state") == "complete", str(result))

    plan_path = Path(result.get("plan_path", ""))
    check("plan written to test_output/weeklyplan/",
          plan_path.exists() and "test_output" in str(plan_path))

    shopping_path_str = result.get("shopping_path", "")
    if shopping_path_str:
        shopping_path = Path(shopping_path_str)
        check("shopping CSV written to test_output/weeklyplan/",
              shopping_path.exists() and "test_output" in str(shopping_path))
        csv_lines = shopping_path.read_text().splitlines()
        check("shopping CSV has header + at least 5 rows", len(csv_lines) >= 6,
              f"got {len(csv_lines)} lines")

    # Verify production weeklyplan untouched
    prod_plan = srv.WEEKLYPLAN_DIR / f"mealplan_{week_start_str}.json"
    check("production plan file not created", not prod_plan.exists())

    # Verify plan JSON structure
    if plan_path.exists():
        plan_data = json.loads(plan_path.read_text())
        check("plan has meals array", isinstance(plan_data.get("meals"), list))
        check("plan has balance dict", isinstance(plan_data.get("balance"), dict))
        meals = plan_data.get("meals", [])
        check("plan meals have required fields",
              all("day" in m and "title" in m and "date" in m for m in meals))

    # 6. Disable test mode + verify cleanup
    srv.set_test_mode(False)
    check("_TEST_DIR cleared after disable", srv._TEST_DIR is None)

    # 7. cleanup_test_data
    if not keep:
        result = srv.cleanup_test_data()
        check("cleanup_test_data ok", result.get("ok"))
        check("test_output/weeklyplan/ removed",
              not (test_dir / "weeklyplan").exists())
        check("test_output/menu_activity.json removed",
              not (test_dir / "menu_activity.json").exists())

    print(f"\n  {passed} passed, {failed} failed\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sandboxed workflow testbed")
    parser.add_argument("--keep", action="store_true",
                        help="Leave test_output/ in place after the run")
    parser.add_argument("--week", default="",
                        help="ISO Monday date to plan for (default: next Monday)")
    args = parser.parse_args()

    if not args.week:
        from datetime import timedelta
        today = date.today()
        days_ahead = (0 - today.weekday()) % 7 or 7
        target = today + timedelta(days=days_ahead)
        # The sandbox check asserts no *production* plan gets written for the
        # target week, so skip past any week whose real plan already exists
        # (e.g. pushing after Sunday planning has finalized next Monday's plan).
        while (srv.WEEKLYPLAN_DIR / f"mealplan_{target.isoformat()}.json").exists():
            target += timedelta(days=7)
        args.week = target.isoformat()

    sys.exit(run(args.week, args.keep))
