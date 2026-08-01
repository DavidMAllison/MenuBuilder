#!/usr/bin/env python3
"""
workflow_smoketest_dish_matching.py -- manual live smoke test for the
protein/dish-specific matching fix (Haiku-backed _match_named_dishes).

Reproduces the exact SMS transcript failure from Aug 1 2026: David asked
for pork belly / country ribs repeatedly and never got them despite the
recipes existing in the collection. Runs the real Haiku call end-to-end.

NOT run by the pre-push hook (see test_workflow.py for the deterministic,
offline suite that IS) -- real LLM output is inherently non-deterministic,
and the pre-push gate's value is specifically being a free, fast, 100%
offline check. Run this by hand after touching _match_named_dishes,
_text_names_specific_dish, swap_meal's dish-matching step, or
_select_meals'/get_meal_suggestions' dish_boost_names wiring.

Requires ANTHROPIC_API_KEY and network access. Falls back to reading it
from ~/projects/personal/sms-assistant/.env (same convention as
prep_utils.py's _make_client()) if not already in the environment.

Usage:
    python3 workflow_smoketest_dish_matching.py
"""

import importlib.util as _ilu
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).parent

if not os.environ.get("ANTHROPIC_API_KEY"):
    env_path = Path.home() / "projects/personal/sms-assistant/.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                break

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("ANTHROPIC_API_KEY not set and not found in sms-assistant/.env -- aborting.")
    sys.exit(1)

_spec = _ilu.spec_from_file_location("menu_server", PROJECT / "mcp" / "menu_server.py")
srv = _ilu.module_from_spec(_spec)
sys.modules["menu_server"] = srv
_spec.loader.exec_module(srv)

_COLOR = sys.stdout.isatty()
def _c(code, s): return f"\033[{code}m{s}\033[0m" if _COLOR else s
def OK(s):   return _c("92", f"  OK  {s}")
def FAIL(s): return _c("91", f" FAIL {s}")
def INFO(s): return _c("2",  f"      {s}")

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        print(OK(label))
        passed += 1
    else:
        print(FAIL(label))
        failed += 1
        if detail:
            print(INFO(detail))
    return cond


def run() -> int:
    print("\nLive dish-matching smoke test (real Haiku calls, network required)\n")

    srv.cleanup_test_data()  # clear any leftover state from a prior run
    result = srv.set_test_mode(True)
    check("set_test_mode(True)", result.get("ok"), str(result))

    week = "2026-08-03"
    srv.start_menu_workflow(week_start=week)
    srv.log_meal_feedback("done")

    # Case 1: initial generation should surface a pork-belly recipe somewhere
    # in the week when named up front -- mirrors the real "Cuisine open,
    # let's use pork belly, country ribs, chicken" request, which produced
    # an all-chicken plan before this fix.
    result = srv.get_meal_suggestions(
        cuisine_direction="Cuisine open, let's use pork belly, country ribs, chicken"
    )
    selected = result.get("selected_meals", {})
    belly_hit = any("belly" in name.lower() for name in selected.values())
    check("get_meal_suggestions surfaces a pork belly recipe somewhere in the week",
          belly_hit, f"selected={selected}")

    # Case 2: explicit per-day force should return an actual belly recipe,
    # not just any pork dish (before this fix: always tenderloin/chops/ragu).
    sun_before = selected.get("Sun", "")
    result = srv.swap_meal(day="Sun", reason="force pork belly recipe")
    new_recipe = result.get("new_recipe", "")
    check("swap_meal('force pork belly recipe') returns an actual belly dish",
          "belly" in new_recipe.lower(),
          f"outgoing={sun_before!r} new_recipe={new_recipe!r} note={result.get('note')}")

    # Case 3: "country ribs" should either match a ribs dish or clearly
    # signal it couldn't -- never a silent unrelated substitution (before
    # this fix: silently fell through to an unrelated recipe, e.g. turkey).
    result = srv.swap_meal(day="Wed", reason="swap to use country ribs")
    new_recipe = result.get("new_recipe", "")
    ribs_hit = "rib" in new_recipe.lower()
    honest_miss = bool(result.get("note")) and not ribs_hit
    check("swap_meal('swap to use country ribs') matches ribs or clearly says it couldn't",
          ribs_hit or honest_miss,
          f"new_recipe={new_recipe!r} note={result.get('note')}")

    srv.set_test_mode(False)
    srv.cleanup_test_data()

    print(f"\n  {passed} passed, {failed} failed\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
