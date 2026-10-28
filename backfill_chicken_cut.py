#!/usr/bin/env python3
"""
backfill_chicken_cut.py

Apply the chicken cut rule (intake_checks.detect_cut_swap / apply_cut_swap) to
recipes already in the collection: when breasts vs thighs doesn't matter, list
both, with each cut's doneness, cook time and (acidic/enzymatic marinades)
marinating time.

Two steps so nothing is written before David sees it:

    python3 backfill_chicken_cut.py --preview cut_preview.json
        Asks the model about every active single-cut boneless chicken recipe,
        saves the verdicts to the JSON and prints a change / leave-alone list.

    python3 backfill_chicken_cut.py --apply cut_preview.json [--skip "A;B"]
        Writes exactly the saved verdicts (minus --skip): recipe_metadata.json
        fields, the .md file edited in place (hand-added notes survive), then
        publishes to GitHub Pages. A recipe edited since the preview is skipped.

    python3 backfill_chicken_cut.py --lock "Miso Chicken"
        Sets cut_locked: true so neither intake nor this script touches it.
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import intake_checks as ic
from github_sync import RECIPES_DIR, publish

_CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())
METADATA_PATH = Path(_CONFIG["metadata_path"])


def _load() -> dict:
    return json.loads(METADATA_PATH.read_text())


def _save(data: dict) -> None:
    data["last_updated"] = date.today().isoformat()
    METADATA_PATH.write_text(json.dumps(data, indent=2))


def _md_text(entry: dict) -> str:
    md_path = RECIPES_DIR / (entry.get("filename") or "")
    return md_path.read_text() if entry.get("filename") and md_path.exists() else ""


def _md_lists(md: str) -> tuple:
    """(ingredient lines, step lines) of a recipe .md, text only, as written."""
    ings, steps, section = [], [], ""
    for line in md.splitlines():
        if line.startswith("## "):
            section = line[3:].strip().lower()
        elif line.startswith("- ") and "ingredient" in section:
            ings.append(line[2:].strip())
        elif re.match(r"\d+\.\s", line) and "instruction" in section:
            steps.append(re.sub(r"^\d+\.\s+", "", line).strip())
    return ings, steps


def _md_edits(md: str, ingredients: list, steps: list, swap: dict) -> list:
    """[(old, new)] text replacements for the .md, from a verdict on `ingredients`
    / `steps`, or None when an old line/step isn't in the file exactly once."""
    if not md:
        return None
    edits = [(ingredients[swap["index"]], swap["new_ingredient"].strip())]
    edits += [(steps[c["index"]], c["text"].strip()) for c in swap["step_changes"]]
    return edits if all(md.count(old) == 1 for old, _ in edits) else None


def _md_verdict(name: str, r: dict, swap: dict):
    """(md_verdict, edits) for the .md. Same verdict as the JSON when the .md
    holds the JSON text; otherwise the .md was reworded at intake (attribution
    rule), so ask about its own lines -- rebuilding it from the JSON would
    publish the source's wording."""
    md = _md_text(r)
    edits = _md_edits(md, r["ingredients_raw"], r.get("instructions") or [], swap)
    if edits is not None:
        return None, edits
    ings, steps = _md_lists(md)
    mv = ic.detect_cut_swap(name, ings, steps)
    if not mv or not mv.get("swappable"):
        return mv, None
    trial = {"ingredients_raw": ings, "instructions": steps}
    if not ic.apply_cut_swap(trial, mv):
        return mv, None
    return mv, _md_edits(md, ings, steps, mv)


def preview(out: Path) -> int:
    recipes = _load()["recipes"]
    todo = [(name, r) for name, r in recipes.items()
            if r.get("status") == "active" and not r.get("cut_locked")
            and ic._cut_swap_candidate(name, r.get("ingredients_raw") or []) is not None]
    print(f"{len(todo)} candidates -- asking the model...", file=sys.stderr)

    def ask(item):
        name, r = item
        return name, ic.detect_cut_swap(name, r["ingredients_raw"], r.get("instructions") or [])

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = dict(pool.map(ask, todo))

    report = {}
    for name, swap in sorted(results.items()):
        r = recipes[name]
        if swap is None:
            report[name] = {"action": "error", "reason": "model call failed"}
            continue
        row = {"action": "leave", "reason": swap["reason"], "verdict": swap,
               "before": {"ingredients_raw": r["ingredients_raw"], "instructions": r.get("instructions")}}
        if swap["swappable"]:
            trial = json.loads(json.dumps(r))
            if not ic.apply_cut_swap(trial, swap):
                row.update(action="error", reason="verdict didn't hold together: " + swap["reason"])
            else:
                md_verdict, edits = _md_verdict(name, r, swap)
                if edits is None:
                    row.update(action="md_mismatch", reason=".md is reworded and its own lines didn't take "
                               "the change -- " + ((md_verdict or {}).get("reason") or "model call failed"))
                else:
                    row.update(action="change", md_verdict=md_verdict, md_before=_md_text(r))
        report[name] = row
    out.write_text(json.dumps(report, indent=2))

    for action, label in (("change", "CHANGE"), ("leave", "LEAVE ALONE"),
                          ("md_mismatch", "NEEDS A LOOK (.md differs)"), ("error", "ERROR")):
        rows = [(n, v) for n, v in report.items() if v["action"] == action]
        if not rows:
            continue
        print(f"\n== {label} ({len(rows)})")
        for name, v in rows:
            print(f"- {name}: {v['reason']}")
            if action == "change":
                sw = v.get("md_verdict") or v["verdict"]
                if v.get("md_verdict"):
                    print("    (as written in the reworded .md)")
                print(f"    ingredient: {sw['new_ingredient']}")
                for c in sw["step_changes"]:
                    print(f"    step {c['index'] + 1}: {c['text']}")
    print(f"\nSaved to {out}", file=sys.stderr)
    return 0


def apply(src: Path, skip: set) -> int:
    report = json.loads(src.read_text())
    data = _load()
    recipes = data["recipes"]
    changed = []
    for name, row in report.items():
        if row["action"] != "change" or name in skip:
            continue
        r = recipes.get(name)
        if not r or r.get("cut_locked") or {"ingredients_raw": r.get("ingredients_raw"),
                                              "instructions": r.get("instructions")} != row["before"]:
            print(f"skip (changed since preview): {name}")
            continue
        md = _md_text(r)
        if md != row["md_before"]:
            print(f"skip (.md changed since preview): {name}")
            continue
        if row.get("md_verdict"):
            ings, steps = _md_lists(md)
            edits = _md_edits(md, ings, steps, row["md_verdict"])
        else:
            edits = _md_edits(md, r["ingredients_raw"], r.get("instructions") or [], row["verdict"])
        if edits is None or not ic.apply_cut_swap(r, row["verdict"]):
            print(f"skip (no longer applies): {name}")
            continue
        md_path = RECIPES_DIR / r["filename"]
        for old, new in edits:
            md = md.replace(old, new)
        md_path.write_text(md)
        changed.append(r["filename"])
        print(f"changed: {name}")
    if changed:
        _save(data)
        ok = publish(changed, message=f"Chicken cut: list breasts or thighs ({len(changed)} recipes)")
        print(f"\n{len(changed)} recipes written; publish {'ok' if ok else 'FAILED -- see github_sync.log'}")
    return 0


def lock(name: str) -> int:
    data = _load()
    if name not in data["recipes"]:
        print(f"not found: {name}")
        return 1
    data["recipes"][name]["cut_locked"] = True
    _save(data)
    print(f"cut_locked: {name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--preview", type=Path, metavar="OUT_JSON")
    g.add_argument("--apply", type=Path, metavar="PREVIEW_JSON")
    g.add_argument("--lock", metavar="TITLE")
    p.add_argument("--skip", default="", help='titles to leave out of --apply, ";"-separated')
    a = p.parse_args()
    if a.preview:
        sys.exit(preview(a.preview))
    if a.apply:
        sys.exit(apply(a.apply, {s.strip() for s in a.skip.split(";") if s.strip()}))
    sys.exit(lock(a.lock))
