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
    selected = srv._select_meals([], [], None, dish_boost_names=[target])
    check("_select_meals surfaces dish_boost_names entry",
          target in selected.values(), f"target={target!r} selected={selected}")


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
        args.week = (today + timedelta(days=days_ahead)).isoformat()

    sys.exit(run(args.week, args.keep))
