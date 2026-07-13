#!/usr/bin/env python3
"""
asian_agent.py -- Find Asian recipes (Japanese, Korean, Thai, Vietnamese, Chinese) using Claude.

Sources:
  - justonecookbook.com  (Namiko Hirasawa Chen -- Japanese)
  - maangchi.com         (Maangchi -- Korean)
  - hot-thai-kitchen.com (Pailin Chongchitnant -- Thai)
  - vietworldkitchen.com (Andrea Nguyen -- Vietnamese)
  - thewoksoflife.com    (The Leung Family -- Chinese/Pan-Asian)

Usage:
  recipe "Japanese miso soup"
  recipe "Korean BBQ at home"
  recipe "quick Thai noodle dish"
  recipe "Vietnamese pho"
  recipe "Chinese stir-fry"
  recipe "Asian weeknight dinner"
"""

import difflib
import json
import os
import re
import sys
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

RESULTS_PATH = Path.home() / "Dropbox/LLMContext/cooking/agent_results/asian_agent_results.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

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
# Just One Cookbook -- Japanese (justonecookbook.com)
# WP REST API search; ld+json on recipe pages
# ---------------------------------------------------------------------------

def search_justonecookbook(query: str, max_results: int = 12) -> list[dict]:
    """Search justonecookbook.com via WordPress REST API."""
    url = f"https://www.justonecookbook.com/wp-json/wp/v2/posts?search={query.replace(' ', '+')}&per_page={max_results}"
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    results = []
    for post in resp.json():
        title = BeautifulSoup(post.get("title", {}).get("rendered", ""), "html.parser").get_text()
        link = post.get("link", "")
        if link and title:
            results.append({"title": title, "url": link})
    return results


# ---------------------------------------------------------------------------
# Maangchi -- Korean (maangchi.com)
# Search via /recipes?q=; recipe links at /recipe/<slug>
# ---------------------------------------------------------------------------

def search_maangchi(query: str, max_results: int = 20) -> list[dict]:
    """Search maangchi.com for Korean recipes via WordPress ?s= search."""
    url = f"https://www.maangchi.com/?s={query.replace(' ', '+')}"
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Only recipe pages; skip #comment anchors
        if "maangchi.com/recipe/" not in href or "#" in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        title_el = a.find(["h2", "h3", "h4"]) or a
        title = title_el.get_text(strip=True)
        if not title:
            parent = a.find_parent(["article", "div", "li"])
            if parent:
                h = parent.find(["h2", "h3", "h4"])
                title = h.get_text(strip=True) if h else ""
        if not title:
            title = href.rstrip("/").split("/")[-1].replace("-", " ").title()
        results.append({"title": title, "url": href})
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Hot Thai Kitchen -- Thai (hot-thai-kitchen.com)
# WordPress ?s= search; ld+json on recipe pages
# ---------------------------------------------------------------------------

# Non-recipe URL patterns to skip (checked against full href)
_HTK_SKIP = (
    "/category/", "/all-recipes-by-categories/", "shop-", "contact",
    "newsletter", "htk-cookbook", "locate-", "/about", "tutorial",
    "/tag/", "/page/", "/author/",
)


def search_hotthaikikitchen(query: str, max_results: int = 15) -> list[dict]:
    """Search hot-thai-kitchen.com for Thai recipes."""
    url = f"https://hot-thai-kitchen.com/?s={query.replace(' ', '+')}"
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "hot-thai-kitchen.com" not in href:
            continue
        if href in seen:
            continue
        # Skip the homepage and known non-recipe pages
        path = href.replace("https://hot-thai-kitchen.com", "").strip("/")
        if not path:
            continue
        if any(skip in href for skip in _HTK_SKIP):
            continue
        # Skip listicle pages ("12 Noodle Dishes...", "27 Authentic...")
        txt = a.get_text(strip=True)
        if re.match(r"^\d+\s", txt):
            continue
        seen.add(href)
        results.append({"title": txt or path.replace("-", " ").title(), "url": href})
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Viet World Kitchen -- Vietnamese (vietworldkitchen.com)
# WordPress ?s= search; recipe links follow /blog/YYYY/MM/slug.html pattern
# ld+json on recipe pages
# ---------------------------------------------------------------------------

def search_vietworldkitchen(query: str, max_results: int = 15) -> list[dict]:
    """Search vietworldkitchen.com for Vietnamese recipes."""
    url = f"https://www.vietworldkitchen.com/?s={query.replace(' ', '+')}"
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # VWK recipe pages are always under /blog/ with a year segment
        if "vietworldkitchen.com/blog/" not in href:
            continue
        if href in seen:
            continue
        txt = a.get_text(strip=True)
        if not txt or len(txt) < 5:
            continue
        seen.add(href)
        results.append({"title": txt, "url": href})
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Woks of Life -- Chinese/Pan-Asian (thewoksoflife.com)
# Cloudflare blocks httpx on direct URL fetches, but:
#   Search: WP REST API (/wp-json/wp/v2/wprm_recipe?search=) returns title + link
#   Fetch:  the linked page URL is accessible via httpx and has ld+json
# ---------------------------------------------------------------------------

def search_woksoflife(query: str, max_results: int = 12) -> list[dict]:
    """Search thewoksoflife.com via the WordPress wprm_recipe API."""
    url = (
        f"https://thewoksoflife.com/wp-json/wp/v2/wprm_recipe"
        f"?search={query.replace(' ', '+')}&per_page={max_results}&_fields=id,title,link"
    )
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    results = []
    for post in resp.json():
        title = BeautifulSoup(post.get("title", {}).get("rendered", ""), "html.parser").get_text()
        link = post.get("link", "")
        if title and link:
            results.append({"title": title, "url": link})
    return results


# ---------------------------------------------------------------------------
# Shared fetch -- ld+json extraction (all 5 sites confirmed)
# ---------------------------------------------------------------------------

def fetch_recipe(url: str) -> dict:
    """Fetch a recipe page and extract structured data from ld+json."""
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
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
                if isinstance(step, dict):
                    step_type = step.get("@type", "")
                    if step_type == "HowToSection":
                        # Flatten section → iterate its itemListElement sub-steps
                        for sub in step.get("itemListElement", []):
                            if isinstance(sub, dict):
                                text = sub.get("text", "").strip()
                                if text:
                                    instructions.append(text)
                            elif isinstance(sub, str) and sub.strip():
                                instructions.append(sub.strip())
                    else:
                        text = step.get("text", "").strip()
                        if text:
                            instructions.append(text)
                elif isinstance(step, str) and step.strip():
                    instructions.append(step.strip())

            cuisine = item.get("recipeCuisine", "")
            if isinstance(cuisine, list):
                cuisine = ", ".join(cuisine)

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
        "error": "No ld+json recipe schema found on this page",
        "image": _og_image(soup),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_yield(raw) -> str:
    """Normalize recipeYield to a clean string — handles str, int, and list."""
    if isinstance(raw, list):
        # Take the last element — it's usually the most descriptive ("52 Pieces" vs "52")
        raw = raw[-1] if raw else ""
    return str(raw).strip()


def _iso_to_minutes(iso: str) -> int:
    """Parse ISO 8601 duration (PT1H30M or P0DT1H30M0S) to minutes."""
    if not iso:
        return 0
    # Match optional days, hours, minutes in both PT... and P#DT... formats
    m = re.search(r"(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?", iso)
    if not m:
        return 0
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    mins = int(m.group(3) or 0)
    return days * 1440 + hours * 60 + mins


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
    if "justonecookbook.com" in url:
        return "Just One Cookbook"
    if "maangchi.com" in url:
        return "Maangchi"
    if "hot-thai-kitchen.com" in url:
        return "Hot Thai Kitchen"
    if "vietworldkitchen.com" in url:
        return "Viet World Kitchen"
    if "thewoksoflife.com" in url:
        return "The Woks of Life"
    return url


def _cuisine_from_url(url: str) -> str:
    if "justonecookbook.com" in url:
        return "Japanese"
    if "maangchi.com" in url:
        return "Korean"
    if "hot-thai-kitchen.com" in url:
        return "Thai"
    if "vietworldkitchen.com" in url:
        return "Vietnamese"
    if "thewoksoflife.com" in url:
        return "Chinese"
    return ""


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_justonecookbook",
        "description": (
            "Search justonecookbook.com for Japanese recipes. "
            "Use for any Japanese dish: ramen, miso soup, teriyaki, tonkatsu, gyoza, "
            "onigiri, sushi, udon, soba, tempura, sukiyaki, shabu-shabu, karaage, etc. "
            "Also search here for Japanese-style rice dishes, pickles, or soups."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (dish name or ingredient)"},
                "max_results": {"type": "integer", "description": "Max results (default 12)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_maangchi",
        "description": (
            "Search maangchi.com for Korean recipes. "
            "Use for any Korean dish: bulgogi, bibimbap, tteokbokki, kimchi jjigae, "
            "doenjang jjigae, japchae, galbi, dakgalbi, sundubu jjigae, haemul pajeon, "
            "gimbap, bossam, samgyeopsal, naengmyeon, kongnamul, and all banchan. "
            "Apply culinary knowledge — a dish you know to be Korean should go here."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (dish name or ingredient)"},
                "max_results": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_hotthaikikitchen",
        "description": (
            "Search hot-thai-kitchen.com for Thai recipes by Pailin Chongchitnant. "
            "Use for any Thai dish: pad thai, pad see ew, green curry, red curry, "
            "massaman curry, tom kha, tom yum, larb, som tum, khao pad, khao man gai, "
            "basil stir-fry (pad kra pao), mango sticky rice, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (dish name or ingredient)"},
                "max_results": {"type": "integer", "description": "Max results (default 15)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_vietworldkitchen",
        "description": (
            "Search vietworldkitchen.com for Vietnamese recipes by Andrea Nguyen. "
            "Use for any Vietnamese dish: pho, banh mi, bun bo hue, bun cha, "
            "com tam, goi cuon (fresh spring rolls), cha gio (fried spring rolls), "
            "bo kho, ca kho to, thit kho, canh chua, bun rieu, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (dish name or ingredient)"},
                "max_results": {"type": "integer", "description": "Max results (default 15)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_woksoflife",
        "description": (
            "Search thewoksoflife.com for Chinese and pan-Asian recipes by the Leung family. "
            "Use for any Chinese dish: stir-fries, braises, dim sum, noodles, fried rice, dumplings, "
            "Cantonese, Sichuan, Hunan, Shanghainese, and Chinese-American restaurant classics. "
            "Also covers broader Asian dishes like Vietnamese, Thai, and Korean that appear on the site. "
            "Examples: kung pao chicken, mapo tofu, char siu, beef and broccoli, dan dan noodles, "
            "hot and sour soup, General Tso's, dumplings, lo mein, congee, red braised pork belly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (dish name or ingredient)"},
                "max_results": {"type": "integer", "description": "Max results (default 12)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_recipe",
        "description": (
            "Fetch a recipe from a URL on any of the supported Asian recipe sites. "
            "Extracts title, ingredients, instructions, and timing via ld+json schema. "
            "Only fetch URLs returned by search tools — do not guess URLs."
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

SYSTEM = """You are an Asian recipe finder covering Japanese, Korean, Thai, Vietnamese, and Chinese cuisines. Your job is to search for recipes and return what you find — you do NOT save anything. The user decides what to keep.

Available sources:
- justonecookbook.com (Japanese) — use search_justonecookbook
- maangchi.com (Korean) — use search_maangchi
- hot-thai-kitchen.com (Thai) — use search_hotthaikikitchen
- vietworldkitchen.com (Vietnamese) — use search_vietworldkitchen
- thewoksoflife.com (Chinese/Pan-Asian) — use search_woksoflife

Routing rules:
- Apply culinary knowledge to route — do not rely on keywords alone.
- Japanese dish → search_justonecookbook
- Korean dish → search_maangchi
- Thai dish → search_hotthaikikitchen
- Vietnamese dish → search_vietworldkitchen
- Chinese dish (any region — Cantonese, Sichuan, Hunan, Shanghainese, Chinese-American) → search_woksoflife
- "Asian" or ambiguous query (e.g. "stir-fry", "noodles", "weeknight") → search 2-3 sources most likely to yield relevant results
- Multi-cuisine request → search all relevant sources

Cuisine reference (use your knowledge to extend this list):
- Japanese: ramen, miso soup, teriyaki, gyoza, karaage, tonkatsu, sushi, onigiri, udon, soba, tempura, okonomiyaki, oyakodon, yakitori, shabu-shabu, sukiyaki, agedashi tofu
- Korean: bulgogi, bibimbap, tteokbokki, kimchi jjigae, doenjang jjigae, japchae, galbi, dakgalbi, sundubu jjigae, haemul pajeon, gimbap, naengmyeon, banchan, gamjatang
- Thai: pad thai, pad see ew, green curry, red curry, massaman, panang, tom kha, tom yum, larb, som tum, khao pad, khao man gai, pad kra pao, mango sticky rice, khao soi
- Vietnamese: pho, banh mi, bun bo hue, bun cha, com tam, goi cuon, cha gio, bo kho, ca kho to, thit kho, canh chua, bun rieu, chao ga
- Chinese: kung pao chicken, mapo tofu, char siu, beef and broccoli, dan dan noodles, hot and sour soup, General Tso's, dumplings, lo mein, congee, red braised pork belly, twice-cooked pork, Peking duck, fried rice, egg drop soup, lion's head meatballs

Rules:
- Only fetch URLs returned by search tools. Never guess or construct URLs.
- Aim for 3-5 valid recipes (with ingredients and instructions) per request.
- Skip pages that return errors or have no ld+json schema.
- Search with specific dish names or ingredients — not vague terms like "Asian recipe."
- For general/ambiguous queries: search 2-3 sources using dish terms that would naturally appear in each cuisine.
- When extracting ingredients, mark optional items, garnishes, or "for serving" additions with an "(optional)" prefix — e.g. "(optional) raw egg yolk for dipping".
- At the end, print a brief plain-text summary of what you found."""

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

            if name == "search_justonecookbook":
                print(f"  [Just One Cookbook] Searching: {inp['query']!r}")
                result = search_justonecookbook(inp["query"], inp.get("max_results", 12))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_maangchi":
                print(f"  [Maangchi] Searching: {inp['query']!r}")
                result = search_maangchi(inp["query"], inp.get("max_results", 20))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_hotthaikikitchen":
                print(f"  [Hot Thai Kitchen] Searching: {inp['query']!r}")
                result = search_hotthaikikitchen(inp["query"], inp.get("max_results", 15))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_vietworldkitchen":
                print(f"  [Viet World Kitchen] Searching: {inp['query']!r}")
                result = search_vietworldkitchen(inp["query"], inp.get("max_results", 15))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_woksoflife":
                print(f"  [Woks of Life] Searching: {inp['query']!r}")
                result = search_woksoflife(inp["query"], inp.get("max_results", 12))
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
                        if not result.get("cuisine"):
                            result["cuisine"] = _cuisine_from_url(result["url"])
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
    seen_titles = [r.get("title", "").lower().strip() for r in existing]
    deduped: list[dict] = []
    for r in found_recipes:
        url = (r.get("url") or "").rstrip("/")
        if url and url in seen_urls:
            continue
        t = r.get("title", "").lower().strip()
        if any(difflib.SequenceMatcher(None, t, s).ratio() >= 0.85 for s in seen_titles):
            continue
        deduped.append(r)
        if url:
            seen_urls.add(url)
        seen_titles.append(t)
    merged = existing + deduped
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
            source = r.get("source", "").split(" - ")[0]
            time_str = r.get("time", "")
            cuisine = r.get("cuisine", "")
            if isinstance(cuisine, list):
                cuisine = ", ".join(cuisine)
            detail = " | ".join(filter(None, [source, cuisine, time_str]))
            print(f"  {i}. {r.get('title', '?')} ({detail})")
        print(f"\nResults saved to {RESULTS_PATH}")
    else:
        print("\nNo recipes found.")
