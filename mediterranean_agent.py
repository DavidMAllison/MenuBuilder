#!/usr/bin/env python3
"""
mediterranean_agent.py -- Find Mediterranean recipes from authoritative sites.

Sources:
  - themediterraneandish.com (Suzy Kazan) — search-based, ld+json
  - mygreekdish.com           — sitemap-based, ld+json
  - feastingathome.com        — search-based, ld+json (Levantine / Middle Eastern)

Usage:
  python3 mediterranean_agent.py "Greek lemon chicken"
  python3 mediterranean_agent.py "Moroccan lamb tagine"
  python3 mediterranean_agent.py "Lebanese vegetarian"

Results written to Dropbox/LLMContext/cooking/agent_results/mediterranean_agent_results.json.
"""

import json
import os
import re
import sys
import time as time_mod
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
import anthropic

if not os.environ.get("ANTHROPIC_API_KEY"):
    env_path = Path.home() / "projects/personal/sms-assistant/.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                break

RESULTS_PATH = Path.home() / "Dropbox/LLMContext/cooking/agent_results/mediterranean_agent_results.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

_MGD_CACHE = Path(f"/tmp/mygreekdish_urls_{os.getuid()}.json")
_MGD_TTL   = 86400  # 24 hours
_MGD_SKIP  = re.compile(r"(dessert|cake|cookie|bread|pastry|baklava|pie|tart|biscuit|muffin|semolina)")

client = anthropic.Anthropic()


def _og_image(soup: BeautifulSoup) -> str:
    tag = soup.find("meta", property="og:image")
    return (tag.get("content", "") if tag else "") or ""


def _ld_image(item: dict) -> str:
    img = item.get("image", "")
    if isinstance(img, str):
        return img
    if isinstance(img, dict):
        return img.get("url", "") or img.get("contentUrl", "")
    if isinstance(img, list) and img:
        first = img[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("url", "") or first.get("contentUrl", "")
    return ""


def _extract_video_url(item: dict) -> str:
    video = item.get("video")
    if not video:
        return ""
    if isinstance(video, list):
        video = video[0] if video else None
    if not video:
        return ""
    if isinstance(video, str):
        url = video
    elif isinstance(video, dict):
        url = video.get("contentUrl") or video.get("embedUrl") or video.get("url") or ""
    else:
        return ""
    if not url:
        return ""
    m = re.match(r"https?://(?:www\.)?youtube\.com/embed/([A-Za-z0-9_-]+)", url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    return url


# ---------------------------------------------------------------------------
# The Mediterranean Dish (themediterraneandish.com)
# ---------------------------------------------------------------------------

def search_mediterranean_dish(query: str, max_results: int = 10) -> list[dict]:
    """Search themediterraneandish.com for Mediterranean recipes."""
    url = f"https://www.themediterraneandish.com/?s={query.replace(' ', '+')}"
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    seen_urls = set()

    for article in soup.find_all("article"):
        link = article.find("a", href=True)
        title_el = article.find(["h2", "h3", "h4"])
        if not link or not title_el:
            continue
        href = link["href"]
        if href in seen_urls or "themediterraneandish.com" not in href:
            continue
        if any(p in href for p in ("/category/", "/tag/", "/author/", "/page/")):
            continue
        seen_urls.add(href)
        results.append({"title": title_el.get_text(strip=True), "url": href})
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# My Greek Dish (mygreekdish.com) — sitemap-based
# ---------------------------------------------------------------------------

def _load_mgd_urls() -> list[str]:
    """Load My Greek Dish recipe URLs from cache or sitemap."""
    if _MGD_CACHE.exists() and time_mod.time() - _MGD_CACHE.stat().st_mtime < _MGD_TTL:
        return json.loads(_MGD_CACHE.read_text(encoding="utf-8"))
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as http:
            r = http.get("https://www.mygreekdish.com/post-sitemap.xml", headers=HEADERS)
            r.raise_for_status()
    except Exception:
        return []
    urls = re.findall(r"<loc>(https://www\.mygreekdish\.com/recipe/[^<]+)</loc>", r.text)
    recipe_urls = [u for u in urls if not _MGD_SKIP.search(u)]
    _MGD_CACHE.write_text(json.dumps(recipe_urls), encoding="utf-8")
    return recipe_urls


def search_mygreekdish(query: str, max_results: int = 10) -> list[dict]:
    """Search mygreekdish.com recipe sitemap by keyword matching on URL slugs."""
    urls = _load_mgd_urls()
    terms = query.lower().split()
    results = []
    for url in urls:
        slug = url.rstrip("/").split("/")[-1].replace("-", " ")
        if all(t in slug for t in terms):
            title = slug.replace(" recipe", "").title()
            results.append({"title": title, "url": url})
            if len(results) >= max_results:
                break
    return results


# ---------------------------------------------------------------------------
# Feasting at Home (feastingathome.com)
# ---------------------------------------------------------------------------

def search_feastingathome(query: str, max_results: int = 10) -> list[dict]:
    """Search feastingathome.com for Mediterranean and Middle Eastern recipes."""
    url = f"https://www.feastingathome.com/?s={query.replace(' ', '+')}"
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    seen_urls = set()

    for article in soup.find_all("article"):
        link = article.find("a", href=True)
        title_el = article.find(["h2", "h3", "h4"])
        if not link or not title_el:
            continue
        href = link["href"]
        if href in seen_urls or "feastingathome.com" not in href:
            continue
        if any(p in href for p in ("/category/", "/tag/", "/author/", "/page/")):
            continue
        seen_urls.add(href)
        results.append({"title": title_el.get_text(strip=True), "url": href})
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Generic ld+json recipe fetcher
# ---------------------------------------------------------------------------

def fetch_recipe(url: str) -> dict:
    """Fetch a recipe page and extract structured data via ld+json."""
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
        if not resp.text:
            return {"error": "empty response", "url": url}
    except Exception as e:
        return {"error": str(e), "url": url}

    soup = BeautifulSoup(resp.text, "html.parser")

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, AttributeError):
            continue

        candidates = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict) and "@graph" in data:
            candidates = data["@graph"]
        else:
            candidates = [data]

        for item in candidates:
            if not isinstance(item, dict):
                continue
            t = item.get("@type", "")
            types = t if isinstance(t, list) else [t]
            if "Recipe" not in types:
                continue

            instructions = []
            for step in item.get("recipeInstructions", []):
                if isinstance(step, str):
                    instructions.append(step.strip())
                elif isinstance(step, dict):
                    if step.get("@type") == "HowToSection":
                        for substep in step.get("itemListElement", []):
                            if isinstance(substep, dict):
                                text = substep.get("text", "").strip()
                                if text:
                                    instructions.append(text)
                    else:
                        text = step.get("text", "").strip()
                        if text:
                            instructions.append(text)

            cuisine = item.get("recipeCuisine", "Mediterranean")
            if isinstance(cuisine, list):
                cuisine = cuisine[0] if cuisine else "Mediterranean"
            if not cuisine:
                cuisine = "Mediterranean"

            return {
                "url": url,
                "title": item.get("name", "").strip(),
                "description": item.get("description", "").strip(),
                "prep_time": item.get("prepTime", ""),
                "cook_time": item.get("cookTime", ""),
                "total_time": item.get("totalTime", ""),
                "yield": _parse_yield(item.get("recipeYield", "")),
                "ingredients": item.get("recipeIngredient", []),
                "instructions": instructions,
                "cuisine": cuisine,
                "category": item.get("recipeCategory", ""),
                "image": _ld_image(item) or _og_image(soup),
                "video_url": _extract_video_url(item),
            }

    title_el = soup.find("h1")
    return {
        "url": url,
        "title": title_el.get_text(strip=True) if title_el else "Unknown",
        "error": "No ld+json recipe schema found",
        "image": _og_image(soup),
    }


def _parse_yield(raw) -> str:
    if isinstance(raw, list):
        raw = raw[-1] if raw else ""
    return str(raw).strip()


def _iso_to_minutes(iso: str) -> int:
    if not iso:
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", iso)
    if not m:
        return 0
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    return h * 60 + mins


def _iso_to_human(iso: str) -> str:
    mins = _iso_to_minutes(iso)
    if not mins:
        return ""
    h, m = divmod(mins, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h > 1 else ''}")
    if m:
        parts.append(f"{m} minute{'s' if m > 1 else ''}")
    return " ".join(parts)


def _source_label(url: str) -> str:
    if "themediterraneandish.com" in url:
        return "The Mediterranean Dish"
    if "mygreekdish.com" in url:
        return "My Greek Dish"
    if "feastingathome.com" in url:
        return "Feasting at Home"
    return url


# ---------------------------------------------------------------------------
# Tool definitions for Claude
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_mediterranean_dish",
        "description": (
            "Search themediterraneandish.com (Suzy Kazan) for Mediterranean recipes. "
            "Covers Greek, Lebanese, Turkish, Moroccan, Spanish, and general Mediterranean dishes. "
            "Use specific dish or ingredient names for best results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Dish name or main ingredient"},
                "max_results": {"type": "integer", "description": "Max results (default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_mygreekdish",
        "description": (
            "Search mygreekdish.com for authentic Greek recipes by keyword matching on URL slugs. "
            "Best for specific Greek dishes: lamb kleftiko, spanakopita, pastitsio, moussaka, souvlaki. "
            "Use specific dish or ingredient names — single words work best."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Dish name or ingredient (e.g. 'lamb', 'chicken lemon', 'chickpea')"},
                "max_results": {"type": "integer", "description": "Max results (default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_feastingathome",
        "description": (
            "Search feastingathome.com for Mediterranean and Middle Eastern recipes. "
            "Strong on Lebanese, Israeli, Persian, Turkish, and vegetarian Mediterranean dishes. "
            "Use specific dish or ingredient names."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Dish name or ingredient"},
                "max_results": {"type": "integer", "description": "Max results (default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_recipe",
        "description": (
            "Fetch a recipe page and extract title, ingredients, instructions, and timing. "
            "Only fetch URLs returned by search tools — do not guess or construct URLs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL of the recipe page"},
            },
            "required": ["url"],
        },
    },
]

SYSTEM = """You are a Mediterranean recipe finder. Search for recipes and return what you find — you do NOT save anything.

Available sources:
- themediterraneandish.com (Suzy Kazan) — use search_mediterranean_dish; broad Mediterranean coverage
- mygreekdish.com — use search_mygreekdish; Greek-specific (lamb, seafood, lemon dishes, spanakopita, etc.)
- feastingathome.com — use search_feastingathome; strong on Levantine, Lebanese, Middle Eastern, vegetarian

Rules:
- Only fetch URLs returned by search tools. Never guess or construct URLs.
- When asked for a specific sub-cuisine, translate to specific dish names before searching:
  - Greek → souvlaki, kleftiko, pastitsio, moussaka, spanakopita, avgolemono, stifado
  - Lebanese → kibbeh, kafta, shawarma, fattoush, muujaddara, fatayer
  - Turkish → kofta, imam bayildi, dolma, mercimek corbasi, menemen
  - Moroccan → chicken tagine, lamb tagine, chermoula fish, harira
  - Israeli/Middle Eastern → shakshuka, sabich, Jerusalem-style chicken
  - Provençal → bouillabaisse, ratatouille, daube, salade niçoise
- Aim for 3-5 valid recipes with full ingredients and instructions per request.
- Skip pages that return errors or have no ingredients/instructions.
- When extracting ingredients, mark optional items, garnishes, or "for serving" additions with an "(optional)" prefix — e.g. "(optional) fresh parsley for garnish".
- At the end, write a brief plain-text summary of what you found."""

_CACHED_SYSTEM = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
_CACHED_TOOLS = [*TOOLS[:-1], {**TOOLS[-1], "cache_control": {"type": "ephemeral"}}]


def run_agent(user_request: str) -> list[dict]:
    messages = [{"role": "user", "content": user_request}]
    print(f"\nSearching: {user_request}\n")

    found_recipes = []

    while True:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=_CACHED_SYSTEM,
            tools=_CACHED_TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    print(block.text)
            break

        if response.stop_reason != "tool_use":
            print(f"Unexpected stop reason: {response.stop_reason}")
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []

        for block in response.content:
            if block.type != "tool_use":
                continue

            name, inp = block.name, block.input

            if name == "search_mediterranean_dish":
                print(f"  [Mediterranean Dish] Searching: {inp['query']!r}")
                result = search_mediterranean_dish(inp["query"], inp.get("max_results", 10))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_mygreekdish":
                print(f"  [My Greek Dish] Searching: {inp['query']!r}")
                result = search_mygreekdish(inp["query"], inp.get("max_results", 10))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_feastingathome":
                print(f"  [Feasting at Home] Searching: {inp['query']!r}")
                result = search_feastingathome(inp["query"], inp.get("max_results", 10))
                print(f"  Found {len(result)} result(s)")

            elif name == "fetch_recipe":
                print(f"  Fetching: {inp['url']}")
                result = fetch_recipe(inp["url"])
                if "error" in result:
                    print(f"  Error: {result['error']}")
                else:
                    time_str = _iso_to_human(result.get("total_time") or result.get("cook_time", ""))
                    result["time"] = time_str
                    title = result.get("title", "unknown")
                    print(f"  Got: {title}" + (f" ({time_str})" if time_str else ""))
                    if result.get("ingredients") and result.get("instructions"):
                        result["source"] = _source_label(result["url"])
                        found_recipes.append(result)

            else:
                result = {"error": f"Unknown tool: {name}"}

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        messages.append({"role": "user", "content": tool_results})

    existing = []
    if RESULTS_PATH.exists():
        try:
            existing = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    seen_urls = {(r.get("url") or "").rstrip("/") for r in existing}
    merged = existing + [r for r in found_recipes if (r.get("url") or "").rstrip("/") not in seen_urls]
    RESULTS_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return found_recipes


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    results = run_agent(" ".join(sys.argv[1:]))
    if results:
        print(f"\nFound {len(results)} recipe(s):")
        for i, r in enumerate(results, 1):
            source = r.get("source", "")
            time_str = r.get("time", "")
            detail = " | ".join(filter(None, [source, time_str]))
            print(f"  {i}. {r.get('title', '?')} ({detail})")
        print(f"\nResults saved to {RESULTS_PATH}")
    else:
        print("\nNo recipes found.")
