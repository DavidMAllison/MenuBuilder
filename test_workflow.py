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
         and not meta.get("needs_review")
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


def check_needs_review_excluded():
    """A needs_review recipe (e.g. instructions cut off mid-recipe) must never
    reach the candidate pool. Pure filter call on an in-memory dict -- no I/O."""
    base = {"status": "active", "times_cooked": 0, "last_cooked_date": None,
            "health": "Heart-Healthy", "meal_type": "Weeknight", "time": "30 minutes",
            "cuisine": "Italian", "ingredients_raw": ["1 lb chicken"], "instructions": ["Cook it."]}
    recipes = {"Zz Clean Recipe": dict(base), "Zz Flagged Recipe": dict(base, needs_review=True)}
    candidates, _ = srv.cs.load_candidates(recipes, adult_names=set(), garden_herbs=[], inventory_items=[])
    names = {c["name"] for c in candidates}
    check("load_candidates keeps clean recipe", "Zz Clean Recipe" in names, f"{names}")
    check("load_candidates drops needs_review recipe", "Zz Flagged Recipe" not in names, f"{names}")


def check_eating_out_reconcile():
    """A "going out" note recorded after meals were picked must still win at finalize."""
    act = {"schedule_notes": ["Going out Sunday \u2014 no dinner needed", "Kid soccer Monday 5:00-6:30 PM"],
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

    # Legacy names stay callable through the bridge until Keanu switches
    for old, new in [("classify_partner_reply", "classify_partner_reply"),
                     ("handle_partner_reply", "handle_partner_reply"),
                     ("send_partner_recipe_batch", "send_partner_recipe_batch"),
                     ("refresh_partner_recipe_queue", "refresh_partner_recipe_queue")]:
        check(f"legacy alias {old} -> {new}", getattr(srv, old, None) is getattr(srv, new))
    check("signoff guard accepts both state names",
          set(srv.SIGNOFF_STATES) == {"awaiting_partner_signoff", "awaiting_partner_signoff"})

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

def check_swap_activation():
    """meal_swap.execute_swap activating a recipe writes a complete entry and runs
    intake checks (Oct 2026: it wrote naked entries and skipped run_intake_checks).
    Fully mocked -- no plan files, metadata, or publish spool touched."""
    import meal_swap as ms
    from unittest import mock

    idea = {"status": "idea", "health": "Heart-Healthy", "source": "Test Kitchen",
            "source_url": "https://example.com/miso", "title_en": None, "notes": "keep me",
            "time": "25 minutes", "ingredients_raw": ["2 lb chicken thighs", "1/2 cup miso"],
            "instructions": ["Marinate the chicken 24 hours.", "Roast at 425F."]}
    store = {"recipes": {"Zzz Miso Chicken": dict(idea), "Old Dinner": {"status": "active"}}}
    saved, published = [], []

    def fake_checks(title, entry, url="", name=""):
        entry["time"] += " plus 24 hours marinating"
        entry["advance_prep_hours"] = 24.0
        return [Path("Miso_Sauce.md")]

    import tempfile
    plan_file = Path(tempfile.mkdtemp()) / "mealplan_2026-10-26.json"
    plan_file.write_text(json.dumps({"meals": [{"day": "Tue", "title": "Old Dinner"}]}))
    patches = [
        mock.patch.object(ms, "_parse_day_to_date", return_value=date(2026, 10, 27)),
        mock.patch.object(ms, "_find_week_files", return_value=(plan_file, Path("/x/s.csv"))),
        mock.patch.object(ms, "_load_metadata", side_effect=lambda: json.loads(json.dumps(store))),
        mock.patch.object(ms, "_save_metadata", side_effect=lambda d: saved.append(json.loads(json.dumps(d)))),
        mock.patch.object(ms, "_create_recipe_md", return_value=Path("/x/r.md")),
        mock.patch.object(ms, "run_intake_checks", side_effect=fake_checks),
        mock.patch.object(ms, "request_publish", side_effect=lambda r, c=(): published.append(([Path(x).name for x in r], [Path(x).name for x in c]))),
        mock.patch.object(ms, "request_gui_launch"),
    ] + [mock.patch.object(ms, f) for f in ("_rebuild_shopping_csv", "_update_meal_plan_json",
                                              "_recompute_balance", "_update_reminder_line",
                                              "_refresh_day_before_notes", "_log_swap_to_feedback")]
    for pt in patches:
        pt.start()
    try:
        bad = ms.execute_swap("Tue 10/27", "Old Dinner",
                              incoming_content={"title": "Zzz Stub", "ingredients": ["1 egg"], "instructions": []})
        check("swap: content without instructions refused", not bad["success"] and not saved, str(bad))

        ask = ms.execute_swap("Tue 10/27", "Old Dinner", incoming_name="Zzz Miso Chicken")
        check("swap: typed name asks first, changes nothing",
              ask["message"] == "needs_confirmation" and ask["options"][0]["recipe"] == "Zzz Miso Chicken"
              and not saved, str(ask))
        res = ms.execute_swap("Tue 10/27", "Old Dinner", incoming_name="Zzz Miso Chicken", confirmed=True)
        check("swap: idea activates from its stored content", res["success"], str(res))
        e = saved[0]["recipes"]["Zzz Miso Chicken"] if saved else {}
        check("swap: entry has ingredients_raw + instructions",
              e.get("ingredients_raw") == idea["ingredients_raw"] and e.get("instructions") == idea["instructions"], str(e))
        check("swap: intake checks applied", e.get("advance_prep_hours") == 24.0
              and e.get("time") == "25 minutes plus 24 hours marinating", str(e))
        check("swap: idea fields preserved", e.get("notes") == "keep me"
              and e.get("source_url") == "https://example.com/miso" and e.get("status") == "active", str(e))
        check("swap: meal_type from active time", e.get("meal_type") == "Weeknight", str(e))
        check("swap: condiment md published", published == [(["Zzz_Miso_Chicken.md"], ["Miso_Sauce.md"])], str(published))
    finally:
        for pt in patches:
            pt.stop()


def check_test_mode_refresh():
    """set_test_mode re-copies production metadata on off->on, keeps it on on->on."""
    copy = srv._TEST_DIR / "recipe_metadata_test.json"
    raw = json.loads(copy.read_text())
    raw["recipes"]["Zzz Stale Marker"] = {"status": "active"}
    copy.write_text(json.dumps(raw))
    srv.set_test_mode(True)
    check("test mode: re-enable while on keeps test copy",
          "Zzz Stale Marker" in json.loads(copy.read_text())["recipes"])
    srv.set_test_mode(False)
    srv.set_test_mode(True)
    fresh = json.loads(copy.read_text())["recipes"]
    prod = json.loads(srv.METADATA_PATH.read_text())["recipes"]
    check("test mode: off->on re-copies production metadata",
          "Zzz Stale Marker" not in fresh and len(fresh) == len(prod), f"{len(fresh)} vs {len(prod)}")


def check_lunch_test_mode():
    """add_lunch_recipe_url goes through the full intake: complete entry tagged for
    lunch, a thin page is refused, an exact URL already in the collection is reused.
    Writes the test copy in test mode, not production."""
    from unittest import mock
    full = {"title": "Zzz Test Lunch Bowl", "time": "20 minutes", "servings": "2", "cuisine": "",
            "ingredients": ["1 cup rice", "1 cucumber"], "instructions": ["Cook rice.", "Top with cucumber."]}
    thin = {"title": "Zzz Thin Lunch", "time": "", "servings": "", "cuisine": "",
            "ingredients": ["1 cup rice"], "instructions": [""]}
    common = [mock.patch.object(srv, "_fetch_atk_recipe", return_value=None),
              mock.patch.object(srv, "RECIPES_DIR", srv._TEST_DIR),
              mock.patch.object(srv, "run_intake_checks", return_value=[]),
              mock.patch("anthropic.Anthropic", side_effect=Exception("offline"))]
    for pt in common:
        pt.start()
    try:
        with mock.patch.object(srv, "_fetch_recipe_data", return_value=full):
            res = srv.add_lunch_recipe_url("https://example.com/zzz-lunch")
        check("lunch intake ok in test mode", res.get("ok"), str(res))
        e = srv._load_metadata().get("Zzz Test Lunch Bowl", {})
        check("lunch intake: complete entry", e.get("ingredients_raw") == full["ingredients"]
              and e.get("instructions") == full["instructions"], str(e))
        check("lunch intake: tagged for lunch", e.get("meal_type") == "lunch" and e.get("lunch_suitable")
              and e.get("times_eaten_lunch") == 0 and "needs_review" not in e, str(e))
        check("lunch intake: production metadata untouched",
              "Zzz Test Lunch Bowl" not in json.loads(srv.METADATA_PATH.read_text())["recipes"])

        with mock.patch.object(srv, "_fetch_recipe_data", return_value=thin), \
             mock.patch.object(srv.httpx, "get", side_effect=Exception("blocked")):
            res = srv.add_lunch_recipe_url("https://example.com/zzz-thin")
        check("lunch intake: thin page refused, nothing written",
              not res.get("ok") and "Zzz Thin Lunch" not in srv._load_metadata(), str(res))

        recipes = srv._load_metadata()
        name, entry = next((k, v) for k, v in recipes.items() if v.get("source_url") and v.get("status") == "active"
                           and not v.get("lunch_suitable") and v.get("meal_type") != "lunch")
        with mock.patch.object(srv, "_fetch_recipe_data", return_value=None):
            res = srv.add_lunch_recipe_url(entry["source_url"])
        check("lunch intake: exact URL reuses the existing recipe",
              res.get("ok") and res.get("recipe") == name and srv._load_metadata()[name].get("lunch_suitable")
              and len(srv._load_metadata()) == len(recipes), str(res))
    finally:
        for pt in common:
            pt.stop()
        (srv._TEST_DIR / "Zzz_Test_Lunch_Bowl.md").unlink(missing_ok=True)


def check_cut_swap():
    """Chicken cut rule (Oct 2026): when breasts vs thighs doesn't matter, the recipe
    lists both with each cut's doneness and marinating time. Offline -- the model
    verdict is faked."""
    from unittest import mock
    import intake_checks as ic
    from plan_utils import canonical_ing
    cand = ic._cut_swap_candidate
    check("cut: single boneless cut is a candidate",
          cand("Thai Salad", ["1 lb rice", "4 boneless, skinless chicken breasts", "2 limes"]) == 1)
    check("cut: title naming the cut isn't", cand("Crispy Chicken Thighs", ["2 lb chicken thighs"]) is None)
    check("cut: bone-in isn't", cand("Roast", ["4 bone-in, skin-on chicken thighs"]) is None)
    check("cut: already lists both isn't", cand("X", ["1 lb chicken breasts or thighs"]) is None)
    check("cut: cutlets aren't", cand("X", ["4 chicken breast cutlets"]) is None)
    check("cut: no LLM call for a non-candidate",
          ic.detect_cut_swap("Beef Stew", ["2 lb chuck"], ["Brown it."]) is None)

    verdict = {"swappable": True, "reason": "", "index": 0, "shop_quantity": "1.5", "shop_unit": "pounds",
               "new_ingredient": "1 1/2 pounds boneless, skinless chicken breasts or thighs",
               "step_changes": [{"index": 0, "text": "Marinate breasts 30 minutes to 2 hours, thighs up to 8 hours."},
                                {"index": 2, "text": "Grill to 160 degrees for breasts or 175 degrees for thighs."}]}
    base = {"ingredients_raw": ["1 1/2 pounds boneless, skinless chicken breasts", "2 limes"],
            "instructions": ["Marinate 30 minutes to 2 hours.", "Heat grill.", "Grill to 160 degrees."],
            "ingredients": [{"name": "boneless skinless chicken breasts", "quantity": "1.5", "unit": "pounds",
                             "category": "Proteins"}]}
    e = json.loads(json.dumps(base))
    check("cut: verdict applied", ic.apply_cut_swap(e, verdict))
    check("cut: ingredient line + changed steps only",
          e["ingredients_raw"][0] == verdict["new_ingredient"] and e["instructions"][1] == "Heat grill."
          and e["instructions"][0].startswith("Marinate breasts") and "175" in e["instructions"][2], str(e))
    check("cut: shopping name lists both", e["ingredients"][0]["name"] == ic.SWAPPED_CHICKEN_NAME
          and canonical_ing(e["ingredients"][0]["name"]) == "boneless skinless chicken breasts or thighs")
    e = json.loads(json.dumps(base))
    check("cut: bad step index changes nothing",
          not ic.apply_cut_swap(e, {**verdict, "step_changes": [{"index": 9, "text": "x"}]}) and e == base)
    check("cut: line without both cuts changes nothing",
          not ic.apply_cut_swap(e, {**verdict, "new_ingredient": "1 lb chicken thighs"}) and e == base)
    check("cut: cut_locked recipe left alone", not ic.apply_cut_swap({**e, "cut_locked": True}, verdict))
    check("cut: not swappable changes nothing", not ic.apply_cut_swap(e, {**verdict, "swappable": False}))

    # Intake writes the .md and entry from the rewritten fields.
    def fake_structured(prompt, schema, client=None, effort="low"):
        return {k: v for k, v in verdict.items() if k != "index"} if schema is ic._CUT_SCHEMA else None
    data = {"title": "Zzz Lime Chicken", "time": "30 minutes", "cuisine": "",
            "ingredients": base["ingredients_raw"], "instructions": base["instructions"]}
    with mock.patch.object(ic, "_structured", side_effect=fake_structured), \
         mock.patch.object(srv, "RECIPES_DIR", srv._TEST_DIR), \
         mock.patch("anthropic.Anthropic", side_effect=Exception("offline")):
        srv._classify_and_write("Zzz Lime Chicken", data, "", "")
    e = srv._load_metadata().get("Zzz Lime Chicken", {})
    md = (srv._TEST_DIR / "Zzz_Lime_Chicken.md").read_text()
    check("cut: intake entry rewritten", e.get("ingredients_raw", [""])[0] == verdict["new_ingredient"]
          and e.get("instructions", [""])[0].startswith("Marinate breasts"), str(e))
    check("cut: intake .md rewritten", "breasts or thighs" in md and "175 degrees" in md, md)
    (srv._TEST_DIR / "Zzz_Lime_Chicken.md").unlink(missing_ok=True)


def check_cuisine_list():
    """ld+json recipeCuisine can be a list; the entry stores one string (Oct 2026:
    ["Chinese", "Taiwanese"] crashed Keanu's similarity check)."""
    from unittest import mock
    from plan_utils import cuisine_name
    check("cuisine: list -> first", cuisine_name(["Chinese", "Taiwanese"]) == "Chinese")
    check("cuisine: string / empty / None", cuisine_name(" Thai ") == "Thai"
          and cuisine_name([]) == "" and cuisine_name(None) == "")
    data = {"title": "Zzz San Bei Ji", "time": "25 minutes", "cuisine": ["Chinese", "Taiwanese"],
            "ingredients": ["1 lb tofu", "3 tbsp soy sauce"], "instructions": ["Fry.", "Braise."]}
    with mock.patch.object(srv, "RECIPES_DIR", srv._TEST_DIR), \
         mock.patch.object(srv, "run_intake_checks", return_value=[]), \
         mock.patch("anthropic.Anthropic", side_effect=Exception("offline")):
        srv._classify_and_write("Zzz San Bei Ji", data, "", "")
    got = srv._load_metadata().get("Zzz San Bei Ji", {}).get("cuisine")
    check("cuisine: intake stores a string", got == "Chinese", repr(got))
    (srv._TEST_DIR / "Zzz_San_Bei_Ji.md").unlink(missing_ok=True)


def check_recipe_lookup():
    """plan_utils.find_recipe_key: a name not in the collection must not resolve to a
    lookalike (Oct 2026: Radicchio Salad became Cumin-Cardamom Chicken ... Salad)."""
    from plan_utils import find_recipe_key as f
    cumin = "Cumin-Cardamom Spiced Chicken with Parsley and Onion Salad"
    rad = "Chicken and Radicchio Salad with Caramelized Honey-Tahini Vinaigrette"
    typed = "Chicken and Radicchio Salad with Caramelized Honey Tahini Vinaigrette"
    check("lookup: unknown recipe -> None, not a lookalike", f(typed, {cumin: {}}) is None)
    check("lookup: punctuation-insensitive exact", f(typed, {cumin: {}, rad: {}}) == rad)
    check("lookup: shared generic words don't match",
          f("Lemon Garlic Pork Chops", {"Baked Chicken Drumsticks with Lemon and Garlic": {}}) is None)
    check("lookup: plan annotations ignored",
          f("Asian Chicken Lettuce Wraps [Heart-Healthy] *NEW*", {"Asian Chicken Lettuce Wraps": {}}) == "Asian Chicken Lettuce Wraps")
    check("lookup: shortened name never auto-resolves",
          f("Lemon Chicken", {"Easy Skillet Lemon Chicken": {}}) is None)
    from plan_utils import name_candidates
    two = {"Easy Skillet Lemon Chicken": {}, "Slow-Cooker Lemon Chicken": {}, "Lemon Garlic Pasta": {},
           "Old Lemon Chicken": {"status": "disliked"}}
    got = name_candidates("lemon chicken", two)
    check("candidates: every recipe it could mean, none disliked",
          sorted(got) == ["Easy Skillet Lemon Chicken", "Slow-Cooker Lemon Chicken"], str(got))
    check("candidates: exact name is the only option", name_candidates("easy skillet lemon chicken", two)
          == ["Easy Skillet Lemon Chicken"])

    from unittest import mock
    with mock.patch.object(srv, "_url_for_name", side_effect=lambda n, r: f"https://u/{n}"):
        ask = srv._confirm_recipe("lemon chicken", two)
        one = srv._confirm_recipe("Easy Skillet Lemon Chicken", two)
    check("confirm: several matches listed with links",
          ask["status"] == "needs_confirmation" and len(ask["options"]) == 2
          and ask["message"].startswith("Which one did you mean? 1) ") and "https://u/" in ask["message"], str(ask))
    check("confirm: exact name still shown back with its link",
          one["message"] == "Is this the right recipe? Easy Skillet Lemon Chicken - https://u/Easy Skillet Lemon Chicken", str(one))


def check_post_finalize_swaps():
    """reorder_days / execute_swap against real files in a temp week (Oct 27 2026:
    a Tue/Wed reorder via two execute_swaps lost shopping rows, set decline
    cooldowns, and said "start by 12:00 AM")."""
    import csv as _csv
    import io as _io
    import tempfile
    import meal_swap as ms
    from unittest import mock

    def ing(name, cat="Produce"):
        return {"name": name, "quantity": "1", "unit": "", "category": cat}
    recipes = {
        "Soy Salmon": {"status": "active", "time": "30 minutes", "health": "Heart-Healthy",
                       "ingredients": [ing("salmon"), ing("garlic")]},
        "Miso Chicken": {"status": "active", "time": "25 minutes plus 24 hours marinating",
                         "advance_prep_hours": 24.0, "health": "Moderate",
                         "ingredients": [ing("chicken thighs"), ing("garlic")]},
        "Thai Chicken Salad": {"status": "active", "time": "30 minutes", "health": "Heart-Healthy",
                               "ingredients": [ing("bibb lettuce")]},
        "Lemon Chicken": {"status": "active", "time": "30 minutes", "health": "Heart-Healthy",
                          "ingredients": [ing("lemon"), ing("garlic")]},
    }
    tmp = Path(tempfile.mkdtemp())
    plan_path, csv_path = tmp / "mealplan_2026-10-26.json", tmp / "shopping_2026-10-26.csv"
    meals = [("Mon", "2026-10-26", "Soy Salmon"), ("Tue", "2026-10-27", "Miso Chicken"),
             ("Wed", "2026-10-28", "Thai Chicken Salad")]
    plan_path.write_text(json.dumps({"meals": [
        {"day": d, "date": dt, "title": t, "health": recipes[t]["health"], "time": recipes[t]["time"],
         "url": f"https://x/{t}", "urls": [f"https://x/{t}"], "reminder": "Practice day."}
        for d, dt, t in meals]}))
    csv_path.write_text("Item,Notes,Date\n")
    saved, launched = [], []
    store = {"recipes": recipes}
    patches = [
        mock.patch.object(ms, "_weeklyplan_dir", return_value=tmp),
        mock.patch.object(ms, "_metadata_path", return_value=tmp / "recipe_metadata.json"),
        mock.patch.object(ms, "_load_metadata", side_effect=lambda: json.loads(json.dumps(store))),
        mock.patch.object(ms, "_save_metadata", side_effect=lambda d: saved.append(d)),
        mock.patch.object(ms, "request_gui_launch", side_effect=lambda apps: launched.append(apps)),
        mock.patch.object(ms, "_log_swap_to_feedback"),
    ]
    for pt in patches:
        pt.start()
    try:
        def rows():
            return {r["Item"]: r for r in _csv.DictReader(_io.StringIO(csv_path.read_text()))}
        res = ms.reorder_days("Tue 10/27", "Wed 10/28")
        plan = {m["day"]: m for m in json.loads(plan_path.read_text())["meals"]}
        check("reorder: days switched", res["success"] and plan["Tue"]["title"] == "Thai Chicken Salad"
              and plan["Wed"]["title"] == "Miso Chicken", str(res))
        check("reorder: url/urls move with the meal", plan["Wed"]["urls"] == ["https://x/Miso Chicken"], str(plan["Wed"]))
        check("reorder: no cooldown / metadata write", saved == [], str(saved))
        check("reorder: marinade note on the night before",
              "Start Miso Chicken tonight (needs 24 hours marinating)" in plan["Tue"]["reminder"], plan["Tue"]["reminder"])
        check("reorder: start time from active minutes", "12:00 AM" not in plan["Wed"]["reminder"]
              and "6:05 PM" in plan["Wed"]["reminder"], plan["Wed"]["reminder"])
        r = rows()
        check("reorder: marinade ingredients dated the day before", r["chicken thighs"]["Date"] == "2026-10-27", str(r.get("chicken thighs")))
        check("reorder: shared row keeps both recipes",
              "Soy Salmon" in r["garlic"]["Notes"] and "Miso Chicken" in r["garlic"]["Notes"], str(r.get("garlic")))
        check("reorder: calendar only, not the Grocery list", launched == [["WeeklyMealCalendar.app"]], str(launched))

        dup = ms.execute_swap("Mon 10/26", "Soy Salmon", incoming_name="Thai Chicken Salad", confirmed=True)
        check("swap: recipe already on another night refused", not dup["success"]
              and dup["message"] == "already_planned", str(dup))

        res = ms.execute_swap("Mon 10/26", "Soy Salmon", incoming_name="Lemon Chicken", confirmed=True)
        plan = {m["day"]: m for m in json.loads(plan_path.read_text())["meals"]}
        r = rows()
        check("swap: plan + urls updated", res["success"] and plan["Mon"]["title"] == "Lemon Chicken"
              and plan["Mon"]["urls"] == [plan["Mon"]["url"]], str(plan["Mon"]))
        check("swap: shared row survives swapping out one of its recipes",
              "Miso Chicken" in r["garlic"]["Notes"] and "Lemon Chicken" in r["garlic"]["Notes"]
              and "Soy Salmon" not in r["garlic"]["Notes"], str(r.get("garlic")))
        check("swap: outgoing-only rows removed", "salmon" not in r, str(list(r)))
    finally:
        for pt in patches:
            pt.stop()


def check_swap_meal_confirm(selected):
    """swap_meal with a typed name asks first; the change lands only with confirmed=True."""
    day = next((d for d in ("Mon", "Tue", "Wed", "Thu") if d in selected), None)
    recipes = srv._load_metadata()
    target = next((k for k, v in recipes.items() if v.get("status") == "active"
                   and k not in selected.values() and str(v.get("meal_type", "")).lower() == "weeknight"), None)
    if not day or not target:
        check("swap_meal confirm round-trip", False, "no day/recipe to test with")
        return
    before = srv._load_activity()["selected_meals"][day]
    ask = srv.swap_meal(day=day, reason="swap it", replacement=target.lower())
    check("swap_meal: typed name returns needs_confirmation with link",
          ask.get("status") == "needs_confirmation" and ask["options"][0]["recipe"] == target
          and ask["options"][0]["url"], str(ask))
    check("swap_meal: nothing changed before confirming",
          srv._load_activity()["selected_meals"][day] == before)
    done = srv.swap_meal(day=day, reason="swap it", replacement=target, confirmed=True)
    check("swap_meal: confirmed swap stores the collection's name", done.get("new_recipe") == target, str(done))


def check_exact_url_intake():
    """An exact source link needs no confirmation -- it IS the recipe."""
    from unittest import mock
    recipes = srv._load_metadata()
    name, entry = next((k, v) for k, v in recipes.items() if v.get("source_url") and v.get("status") == "active")
    with mock.patch.object(srv, "_fetch_recipe_data", return_value=None), \
         mock.patch.object(srv, "_fetch_atk_recipe", return_value=None):
        res = srv.process_recipe_url(entry["source_url"])
    check("url intake: exact source link resolves without asking",
          res.get("status") == "exists" and res.get("recipe") == name, str(res))


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
    check_needs_review_excluded()
    check_eating_out_reconcile()
    check_sms_links(result.get("candidates", []), selected)
    check_partner_classify_and_chmod()
    check_intake_checks()
    check_github_sync()
    check_swap_activation()
    check_test_mode_refresh()
    check_lunch_test_mode()
    check_cut_swap()
    check_cuisine_list()
    check_recipe_lookup()
    check_post_finalize_swaps()
    check_swap_meal_confirm(selected)
    check_exact_url_intake()

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
    blocked = srv.swap_meal(day="Mon", reason="swap it")
    check("swap_meal refuses once the plan is finalized",
          blocked.get("error") == "plan_finalized", str(blocked))

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
