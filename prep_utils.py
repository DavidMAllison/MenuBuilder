#!/usr/bin/env python3
"""
prep_utils.py — shared prep-component classification helpers.

Used by fill_menu_ideas.py (at intake) and menu_server.py (at activation
and on-demand via get_prep_guide).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------

def _make_client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        env_path = Path.home() / "projects/personal/sms-assistant/.env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                    break
    return anthropic.Anthropic()


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PREP_PROMPT = """For each recipe below, identify steps that can be done ahead of time to save effort on cook day.

Return ONLY a JSON array — no prose, no markdown.

Rules:
- prep_components: short action phrases for tasks that can be done in advance
  (e.g. "make the sauce", "marinate chicken", "chop onions and garlic").
  Omit things that must happen during cooking (sauteing, simmering, frying).
  If nothing can be done ahead, return an empty list.
- prep_notes: ONE sentence covering timing constraints — shelf life, marination
  windows, or food-safety limits (e.g. "sauce keeps 5 days refrigerated",
  "marinate 2-24 hours", "don't marinate more than 2 hours — lime breaks down
  chicken"). Leave as empty string if no constraints apply.

Recipes:
{recipes}

Reply format:
[{{"title": "...", "prep_components": ["...", "..."], "prep_notes": "..."}}, ...]"""


# ---------------------------------------------------------------------------
# Markdown instruction parser
# ---------------------------------------------------------------------------

def parse_md_instructions(md_text: str) -> list[str]:
    """Extract numbered instruction steps from a recipe .md file."""
    steps = []
    in_instructions = False
    for line in md_text.splitlines():
        if re.match(r"^#{1,3}\s*instructions", line, re.IGNORECASE):
            in_instructions = True
            continue
        if in_instructions and re.match(r"^#{1,3}\s", line):
            break  # hit next section header
        if in_instructions:
            m = re.match(r"^\d+\.\s+(.+)", line.strip())
            if m:
                steps.append(m.group(1))
    return steps


# ---------------------------------------------------------------------------
# Classify prep — main entry point
# ---------------------------------------------------------------------------

def classify_prep(recipes: list[dict], client: anthropic.Anthropic | None = None) -> dict[str, dict]:
    """
    Batch-classify prep components for a list of recipe dicts.

    Each dict should have:
        title       (str)
        ingredients (list[str])   — names or raw strings
        instructions (list[str])  — cooking steps

    Returns {title: {"prep_components": [...], "prep_notes": "..."}}.
    Falls back to empty prep if the API call fails.
    """
    if not recipes:
        return {}

    if client is None:
        client = _make_client()

    recipe_lines = []
    for r in recipes:
        ingr_preview  = ", ".join(r.get("ingredients", [])[:8])
        steps_preview = " → ".join(r.get("instructions", [])[:5])
        recipe_lines.append(
            f'- {r["title"]}\n'
            f'  Ingredients: {ingr_preview}\n'
            f'  Steps: {steps_preview}'
        )

    prompt = _PREP_PROMPT.format(recipes="\n".join(recipe_lines))

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            classified = json.loads(m.group())
            return {
                item["title"]: {
                    "prep_components": item.get("prep_components", []),
                    "prep_notes":      item.get("prep_notes", ""),
                }
                for item in classified
            }
    except Exception as e:
        print(f"  [!] Prep classification error: {e}")

    return {}


# ---------------------------------------------------------------------------
# Convenience: classify a single recipe
# ---------------------------------------------------------------------------

def classify_prep_single(title: str, ingredients: list[str], instructions: list[str],
                         client: anthropic.Anthropic | None = None) -> dict:
    """
    Classify prep for one recipe. Returns {"prep_components": [...], "prep_notes": "..."}.
    Returns empty dict on failure.
    """
    result = classify_prep(
        [{"title": title, "ingredients": ingredients, "instructions": instructions}],
        client=client,
    )
    return result.get(title, {"prep_components": [], "prep_notes": ""})
