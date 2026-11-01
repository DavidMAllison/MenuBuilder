"""Fetch a recipe page and read its ld+json Recipe.

Shared by every intake path: the MCP server (URL intake, lunch intake),
meal_swap.py (Keanu's swaps, which can't import the MCP server) and
intake_checks.py (sub-recipe pages). Three copies of this used to drift -- a
fix like HTML-decoding titles (Nov 1 2026) had to land in each.
"""

import html
import json
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def fetch_html(url: str) -> Optional[str]:
    """Page HTML, or None on any failure."""
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def find_ld_recipe(page: str) -> Optional[dict]:
    """The page's raw ld+json Recipe object, or None."""
    soup = BeautifulSoup(page, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in (data if isinstance(data, list) else data.get("@graph", [data])):
            t = item.get("@type", "") if isinstance(item, dict) else ""
            if "Recipe" in (t if isinstance(t, list) else [t]):
                return item
    return None


def parse_duration(iso: str) -> str:
    """ISO 8601 "PT1H15M" -> "1 hour 15 minutes"; "" if unparseable."""
    if not iso:
        return ""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", iso)
    if not m:
        return ""
    h, mins = int(m.group(1) or 0), int(m.group(2) or 0)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h > 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins > 1 else ''}")
    return " ".join(parts)


def normalize_ld_recipe(item: dict) -> dict:
    """{title, time, servings, ingredients, instructions, cuisine} from an ld+json
    Recipe. Text is HTML-decoded (sites ship "&amp;" in their JSON) and empty
    steps are dropped."""
    instructions = [
        html.unescape(text) for text in (
            (s.get("text", "") if isinstance(s, dict) else str(s)).strip()
            for s in item.get("recipeInstructions", [])
        ) if text
    ]
    recipe_yield = item.get("recipeYield", "")
    if isinstance(recipe_yield, list):
        recipe_yield = recipe_yield[-1] if recipe_yield else ""
    return {
        "title": html.unescape(item.get("name", "")).strip(),
        "time": parse_duration(item.get("totalTime") or item.get("cookTime", "")),
        "servings": str(recipe_yield).strip(),
        "ingredients": [html.unescape(i) if isinstance(i, str) else i
                        for i in item.get("recipeIngredient", [])],
        "instructions": instructions,
        "cuisine": item.get("recipeCuisine", ""),
    }


def fetch_recipe(url: str) -> Optional[dict]:
    """Fetch url and return its normalized recipe, or None."""
    page = fetch_html(url)
    item = find_ld_recipe(page) if page else None
    return normalize_ld_recipe(item) if item else None
