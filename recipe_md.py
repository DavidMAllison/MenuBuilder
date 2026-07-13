"""Shared .md file builder for every recipe intake path.

Canonical structure (Jun 25 2026): Title -> Notes (optional) -> Ingredients ->
Instructions. No metadata block -- time, servings, source, and video_url all
render from recipe_metadata.json via the GitHub Pages template's .meta bar
(see menubuilder-recipes/_layouts/default.html). Duplicating them here causes
double-rendering on the live page and, since nothing keeps two copies in
sync, silent staleness.

This is the single source of truth for the format. Every function that
writes a recipe .md file MUST call build_recipe_md() rather than hand-rolling
its own line-building logic -- that duplication is exactly how this format
drifted independently in seven different places (fixed Jul 12 2026; see
project_recipe_md_structure memory). If a new intake path needs to write a
recipe file, import this.
"""

import re


def build_recipe_md(
    title: str,
    ingredients: list,
    instructions: list,
    notes: str = "",
    needs_review: bool = False,
) -> str:
    """Build canonical recipe markdown.

    Args:
        title: Recipe title (used for the # heading).
        ingredients: Flat list of ingredient strings. Items prefixed
            "(optional)" (case-insensitive) are split into a separate
            "Optional / For Serving" section automatically.
        instructions: Ordered list of instruction step strings.
        notes: Family feedback / pre-cook tips, if any. Omitted entirely
            when blank -- empty Notes sections are noise.
        needs_review: Adds a review banner after the title for
            auto-generated content that hasn't been human-verified.
    """
    lines = [f"# {title}", ""]

    if needs_review:
        lines += [
            "> **Needs Review** — auto-generated content; verify formatting and completeness before first cook.",
            "",
        ]

    if notes.strip():
        lines += ["## Notes", "", notes.strip(), ""]

    required = [i for i in ingredients if not i.lower().startswith("(optional)")]
    optional = [i for i in ingredients if i.lower().startswith("(optional)")]

    lines += ["## Ingredients", ""]
    for ing in required:
        lines.append(f"- {ing}")
    if optional:
        lines += ["", "**Optional / For Serving:**", ""]
        for ing in optional:
            display = re.sub(r"^\(optional\)\s*", "", ing, flags=re.IGNORECASE)
            lines.append(f"- {display}")

    lines += ["", "## Instructions", ""]
    for i, step in enumerate(instructions, 1):
        lines.append(f"{i}. {step}")
    lines.append("")

    return "\n".join(lines)
