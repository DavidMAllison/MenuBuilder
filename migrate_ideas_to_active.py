#!/usr/bin/env python3
"""
migrate_ideas_to_active.py

One-shot migration: converts all status="idea" entries in recipe_metadata.json
to status="active", creates .md files for those that don't have one, and flags
low-quality auto-generated content with needs_review=true.

Safe to re-run: skips entries already "active"; overwrites only garbage .md files
(detected by size < 500 chars or known-bad filenames).

Usage:
    python3 migrate_ideas_to_active.py          # live run
    python3 migrate_ideas_to_active.py --dry-run
"""

import argparse
import json
import re
from pathlib import Path

METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"
RECIPES_DIR   = Path.home() / "Dropbox/LLMContext/cooking/recipes"

# Quality thresholds for needs_review flag
MIN_STEPS       = 3
MIN_STEP_LEN    = 30
MIN_INGREDIENTS = 4
HTML_RE         = re.compile(r"<[^>]+>")

# Known-garbage .md files to regenerate (scraped HTML dumps)
FORCE_REGEN = {"Cajun_Meatball_Fricassee.md"}


def _quality_issues(ingredients_raw: list, instructions: list) -> list[str]:
    issues = []
    if len(instructions) < MIN_STEPS:
        issues.append(f"only {len(instructions)} step(s)")
    if any(len(s) < MIN_STEP_LEN for s in instructions):
        issues.append("short step(s)")
    if len(ingredients_raw) < MIN_INGREDIENTS:
        issues.append(f"only {len(ingredients_raw)} ingredient(s)")
    if any(HTML_RE.search(s) for s in instructions + ingredients_raw):
        issues.append("HTML artifacts")
    return issues


def _title_to_filename(title: str) -> str:
    safe = re.sub(r"[^\w\s\-]", "", title).strip().replace(" ", "_")
    return safe + ".md"


def _build_md(title: str, entry: dict, needs_review: bool) -> str:
    """Thin wrapper — see recipe_md.py for the canonical builder every
    intake path shares."""
    from recipe_md import build_recipe_md
    return build_recipe_md(
        title=title,
        ingredients=entry.get("ingredients_raw", []),
        instructions=entry.get("instructions", []),
        needs_review=needs_review,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    recipes  = metadata["recipes"]

    ideas = {k: v for k, v in recipes.items() if v.get("status") == "idea"}
    print(f"Found {len(ideas)} idea entries to migrate.\n")

    created   = []
    regenerated = []
    kept_existing = []
    flagged   = []
    url_fixed = []

    for title, entry in ideas.items():
        # Fix source_url: copy from url alias if missing
        if not entry.get("source_url") and entry.get("url"):
            if not args.dry_run:
                recipes[title]["source_url"] = entry["url"]
            url_fixed.append(title)

        # Determine filename — fix .txt → .md
        existing_filename = entry.get("filename", "")
        if existing_filename.endswith(".txt"):
            new_filename = existing_filename[:-4] + ".md"
        elif existing_filename.endswith(".md"):
            new_filename = existing_filename
        else:
            new_filename = _title_to_filename(title)

        md_path = RECIPES_DIR / new_filename

        # Quality check
        issues = _quality_issues(
            entry.get("ingredients_raw", []),
            entry.get("instructions", []),
        )
        needs_review = bool(issues)
        if needs_review:
            flagged.append((title, issues))

        # Decide whether to write .md
        should_write = True
        if md_path.exists() and new_filename not in FORCE_REGEN:
            # Keep clean existing file; still flag for needs_review if warranted
            kept_existing.append(title)
            should_write = False
        elif md_path.exists() and new_filename in FORCE_REGEN:
            regenerated.append(title)
        else:
            created.append(title)

        if should_write and not args.dry_run:
            RECIPES_DIR.mkdir(exist_ok=True)
            md_content = _build_md(title, recipes[title], needs_review)
            md_path.write_text(md_content, encoding="utf-8")

        if not args.dry_run:
            recipes[title]["status"]       = "active"
            recipes[title]["filename"]     = new_filename
            recipes[title]["needs_review"] = needs_review

    # Summary
    prefix = "[DRY RUN] " if args.dry_run else ""

    print(f"{prefix}Results:")
    print(f"  .md created:       {len(created)}")
    print(f"  .md regenerated:   {len(regenerated)}")
    print(f"  .md kept existing: {len(kept_existing)}")
    print(f"  source_url fixed:  {len(url_fixed)}")
    print(f"  needs_review=true: {len(flagged)}")
    print()

    if url_fixed:
        print("source_url fixed (copied from url):")
        for t in url_fixed:
            print(f"  {t}")
        print()

    if flagged:
        print("Flagged needs_review=true:")
        for t, issues in flagged:
            print(f"  {t}: {issues}")
        print()

    if created:
        print(".md created:")
        for t in created:
            print(f"  + {t}")
        print()

    if args.dry_run:
        print("[DRY RUN] No files or metadata written.")
        return

    metadata["last_updated"] = __import__("datetime").date.today().isoformat()
    METADATA_PATH.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote updated metadata to {METADATA_PATH}")
    print(f"Migration complete. {len(ideas)} entries now active.")


if __name__ == "__main__":
    main()
