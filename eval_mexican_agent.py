#!/usr/bin/env python3
"""
eval_mexican_agent.py -- Eval harness for mexican_agent.py.

Loads test cases from eval/mexican_prompts.json, runs each through the agent,
applies two automated tiers, and writes a markdown report for human review.

Tier 1: all result URLs match expected_sources (skipped if expected_sources is empty)
Tier 2: every result has non-empty ingredients and instructions

Usage:
  python3 eval_mexican_agent.py
  python3 eval_mexican_agent.py --case carnitas-all
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mexican_agent import run_agent

PROMPTS_FILE = Path(__file__).parent / "eval" / "mexican_prompts.json"
OUTPUT_DIR = Path(__file__).parent / "eval_output"


def check_sources(results: list[dict], expected: list[str]) -> tuple[bool, str]:
    if not expected:
        return True, "skipped (no expected sources defined)"
    bad = [r["url"] for r in results if not any(d in r.get("url", "") for d in expected)]
    if bad:
        return False, f"unexpected sources: {bad}"
    return True, "ok"


def check_parse(results: list[dict]) -> tuple[bool, str]:
    missing = []
    for r in results:
        title = r.get("title", "?")
        if not r.get("ingredients"):
            missing.append(f"{title} (no ingredients)")
        if not r.get("instructions"):
            missing.append(f"{title} (no instructions)")
    if missing:
        return False, "; ".join(missing)
    return True, "ok"


def result_row(r: dict) -> str:
    source = r.get("source", r.get("url", "?")).split(" - ")[0]
    time_str = r.get("time", "")
    meta = " | ".join(filter(None, [source, time_str]))
    ing_count = len(r.get("ingredients", []))
    ins_count = len(r.get("instructions", []))
    return (
        f"- **{r.get('title', '?')}** ({meta})  \n"
        f"  {r.get('url', '')}  \n"
        f"  Ingredients: {ing_count} &nbsp; Instructions: {ins_count}"
    )


def generate_report(case_results: list[dict], run_id: str) -> str:
    failures = sum(1 for c in case_results if not c["t1_passed"] or not c["t2_passed"])

    lines = [
        f"# Mexican Agent Eval — {run_id}",
        f"",
        f"Cases: {len(case_results)} &nbsp; Automated failures: {failures}",
        f"",
        "---",
        "",
    ]

    for c in case_results:
        overall = "PASS" if c["t1_passed"] and c["t2_passed"] else "FAIL"
        lines += [
            f"## [{overall}] {c['id']}",
            f"",
            f"**Intent:** {c['description']}  ",
            f"**Query:** `{c['query']}`  ",
            f"**Results returned:** {len(c['results'])}",
            f"",
            f"| Tier | Check | Result |",
            f"|------|-------|--------|",
            f"| 1 | Source routing | {'PASS' if c['t1_passed'] else 'FAIL'} — {c['t1_detail']} |",
            f"| 2 | Parse completeness | {'PASS' if c['t2_passed'] else 'FAIL'} — {c['t2_detail']} |",
            f"",
        ]

        lines.append("**Results:**")
        lines.append("")
        if c["results"]:
            for r in c["results"]:
                lines.append(result_row(r))
                lines.append("")
        else:
            lines.append("_No recipes returned._")
            lines.append("")

        if c.get("notes"):
            lines += [f"**Notes:** {c['notes']}", ""]

        lines += [
            "**Tier 3 — Human review:**",
            "",
            "- [ ] Recipes are relevant to the query",
            "- [ ] Source routing matched intent",
            "- [ ] Ingredient lists look complete",
            "- [ ] Instructions are readable",
            "",
            "---",
            "",
        ]

    return "\n".join(lines)


def run_eval(case_ids: list[str] | None = None) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    prompts = json.loads(PROMPTS_FILE.read_text())

    if case_ids:
        prompts = [p for p in prompts if p["id"] in case_ids]
        if not prompts:
            print(f"No cases matched: {case_ids}")
            sys.exit(1)

    run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    print(f"Running {len(prompts)} case(s)...\n")

    case_results = []
    for p in prompts:
        print(f"=== [{p['id']}] {p['description']} ===")
        try:
            results = run_agent(p["query"])
        except Exception as e:
            print(f"  ERROR: {e}")
            results = []

        t1_passed, t1_detail = check_sources(results, p.get("expected_sources", []))
        t2_passed, t2_detail = check_parse(results)

        print(f"  [T1] Source routing: {'PASS' if t1_passed else 'FAIL'} — {t1_detail}")
        print(f"  [T2] Parse check:    {'PASS' if t2_passed else 'FAIL'} — {t2_detail}")
        print()

        case_results.append({
            "id": p["id"],
            "description": p["description"],
            "query": p["query"],
            "results": results,
            "t1_passed": t1_passed,
            "t1_detail": t1_detail,
            "t2_passed": t2_passed,
            "t2_detail": t2_detail,
            "notes": p.get("notes", ""),
        })

    report = generate_report(case_results, run_id)
    output_path = OUTPUT_DIR / f"mexican_eval_{run_id}.md"
    output_path.write_text(report, encoding="utf-8")

    failures = sum(1 for c in case_results if not c["t1_passed"] or not c["t2_passed"])
    print(f"Eval complete — {failures} automated failure(s).")
    print(f"Report: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", nargs="+", help="Run specific case IDs only")
    args = parser.parse_args()
    run_eval(args.case)
