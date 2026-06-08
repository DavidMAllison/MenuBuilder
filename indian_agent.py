#!/usr/bin/env python3
"""
indian_agent.py -- Find Indian recipes using Claude.

Sources:
  - indianhealthyrecipes.com  (Swasthi Shreekanth -- North/South Indian)
  - hebbarskitchen.com         (Hebbars Kitchen -- general Indian; note: some recipes
                                on Reddit have been flagged for sourcing issues)
  - chetnamakan.co.uk          (Chetna Makan -- British-Indian, GBBO Series 5)
  - kannammacooks.com          (Suguna Vinodh -- South Indian / Tamil)

Not included:
  - ranveerbrar.com       -- ld+json schema broken (ingredients = category headers only)
  - archanaskitchen.com   -- all URLs return 404

Usage:
  indian "butter chicken"
  indian "South Indian breakfast"
  indian "vegetarian dal"
  indian "chicken tikka masala"
  indian "Kerala fish curry"
"""

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

RESULTS_PATH = Path(f"/tmp/indian_agent_results_{os.getuid()}.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

client = anthropic.Anthropic()


# ---------------------------------------------------------------------------
# Indian Healthy Recipes -- North/South Indian (indianhealthyrecipes.com)
# WordPress ?s= search; ld+json on recipe pages
# ---------------------------------------------------------------------------

def search_indianhealthyrecipes(query: str, max_results: int = 12) -> list[dict]:
    """Search indianhealthyrecipes.com via WordPress ?s= search."""
    url = f"https://www.indianhealthyrecipes.com/?s={query.replace(' ', '+')}"
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
        if "indianhealthyrecipes.com" not in href:
            continue
        # Recipe URLs follow /recipe-name/ pattern (not category/tag/page)
        if any(skip in href for skip in ["/category/", "/tag/", "/page/", "/author/", "?s="]):
            continue
        path = href.rstrip("/").replace("https://www.indianhealthyrecipes.com", "").strip("/")
        if not path or path.count("/") > 0:
            continue
        if href in seen:
            continue
        seen.add(href)
        txt = a.get_text(strip=True)
        if not txt or len(txt) < 4:
            title_el = a.find_parent(["article", "div"])
            if title_el:
                h = title_el.find(["h2", "h3"])
                txt = h.get_text(strip=True) if h else ""
        if not txt:
            txt = path.replace("-", " ").title()
        results.append({"title": txt, "url": href})
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Hebbars Kitchen -- General Indian (hebbarskitchen.com)
# WordPress ?s= search; ld+json on recipe pages
# ---------------------------------------------------------------------------

def search_hebbarskitchen(query: str, max_results: int = 12) -> list[dict]:
    """Search hebbarskitchen.com via WordPress ?s= search."""
    url = f"https://hebbarskitchen.com/?s={query.replace(' ', '+')}"
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    seen = set()

    _HEBBARS_SKIP = ("/category/", "/tag/", "/page/", "/author/", "/recipes/", "?s=")

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "hebbarskitchen.com" not in href:
            continue
        if any(skip in href for skip in _HEBBARS_SKIP):
            continue
        path = href.rstrip("/").replace("https://hebbarskitchen.com", "").strip("/")
        if not path or path.count("/") > 0:
            continue
        if href in seen:
            continue
        seen.add(href)
        txt = a.get_text(strip=True)
        if not txt or len(txt) < 4:
            txt = path.replace("-", " ").title()
        results.append({"title": txt, "url": href})
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Chetna Makan -- British-Indian (chetnamakan.co.uk)
# WP REST API search; custom HTML scraper for recipe content
# ---------------------------------------------------------------------------

def search_chetnamakan(query: str, max_results: int = 10) -> list[dict]:
    """Search chetnamakan.co.uk via WordPress REST API."""
    url = (
        f"https://www.chetnamakan.co.uk/wp-json/wp/v2/posts"
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
# Kannamma Cooks -- South Indian / Tamil (kannammacooks.com)
# WordPress ?s= search; ld+json on recipe pages (via Tasty Recipes plugin)
# ---------------------------------------------------------------------------

def search_kannammacooks(query: str, max_results: int = 12) -> list[dict]:
    """Search kannammacooks.com via WordPress ?s= search."""
    url = f"https://www.kannammacooks.com/?s={query.replace(' ', '+')}"
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
        if "kannammacooks.com" not in href:
            continue
        if any(skip in href for skip in ["/category/", "/tag/", "/page/", "/author/", "?s="]):
            continue
        path = href.rstrip("/").replace("https://www.kannammacooks.com", "").strip("/")
        # Recipe URLs are flat slugs like /chicken-chettinad-recipe/
        if not path or path.count("/") > 0:
            continue
        if href in seen:
            continue
        seen.add(href)
        txt = a.get_text(strip=True)
        if not txt or len(txt) < 4:
            txt = path.replace("-", " ").title()
        results.append({"title": txt, "url": href})
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Shared fetch -- ld+json with Chetna Makan HTML fallback
# ---------------------------------------------------------------------------

def _fetch_chetna_html(soup: BeautifulSoup, url: str) -> dict:
    """
    Extract recipe from chetnamakan.co.uk page HTML.

    Chetna uses two different post formats:
    - Older posts: <ul class='wp-block-list'> for ingredients, <p> per step for instructions
    - Newer posts: <div class='gb-container'> with ingredient blob text, single <p> with
      '–' delimited steps

    Both are handled here.
    """
    entry = soup.find(class_="entry-content") or soup.find("article")
    if not entry:
        return {"url": url, "title": "", "error": "No entry-content found"}

    title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else "Unknown"

    ingredients = []
    instructions = []

    # --- Pattern 1: older posts with <ul class="wp-block-list"> ---
    wp_lists = entry.find_all("ul", class_="wp-block-list")
    if wp_lists:
        for lst in wp_lists:
            for li in lst.find_all("li"):
                text = li.get_text(strip=True)
                if text:
                    ingredients.append(text)

        # Instructions follow the last ingredient list as <p> tags
        found_ingredient_section = False
        for el in entry.find_all(["p", "ul"]):
            if el.name == "ul" and "wp-block-list" in " ".join(el.get("class", [])):
                found_ingredient_section = True
                continue
            if not found_ingredient_section:
                continue
            text = el.get_text(strip=True)
            if not text or len(text) < 20:
                continue
            if any(skip in text.lower() for skip in ["watch the step", "watch this video", "instagram", "pinterest"]):
                continue
            instructions.append(text)

    # --- Pattern 2: newer posts with <div class="gb-container"> for ingredients ---
    if not ingredients:
        gb_div = entry.find("div", class_="gb-container")
        if gb_div:
            # The div contains the ingredient block — split on newlines and commas
            raw = gb_div.get_text(separator="\n", strip=True)
            # Drop the header line if it's just "Ingredients"
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            lines = [ln for ln in lines if ln.lower() != "ingredients"]
            ingredients = lines

        # Instructions: look for <p> with '–' step separators (newer Chetna format)
        if not instructions:
            for p in entry.find_all("p"):
                text = p.get_text(strip=True)
                if "–" in text and len(text) > 50:
                    # Split on em dash step markers
                    steps = [s.strip().lstrip("–").strip() for s in text.split("–") if s.strip()]
                    if len(steps) >= 2:
                        instructions = [s for s in steps if len(s) > 10]
                        break
            # Fallback: plain method paragraphs after the gb-container
            if not instructions:
                in_method = False
                for el in entry.find_all(["div", "p"]):
                    if el.name == "div" and "gb-container" in " ".join(el.get("class", [])):
                        in_method = True
                        continue
                    if not in_method or el.name != "p":
                        continue
                    text = el.get_text(strip=True)
                    if not text or len(text) < 20:
                        continue
                    if any(skip in text.lower() for skip in ["watch the step", "watch this video", "instagram", "pinterest"]):
                        continue
                    instructions.append(text)

    if not ingredients and not instructions:
        return {"url": url, "title": title, "error": "Could not extract recipe content from HTML"}

    return {
        "url": url,
        "title": title,
        "description": "",
        "prep_time": "",
        "cook_time": "",
        "total_time": "",
        "yield": "",
        "ingredients": ingredients,
        "instructions": instructions,
        "cuisine": "Indian",
        "category": "",
    }


def fetch_recipe(url: str) -> dict:
    """
    Fetch a recipe from any supported Indian recipe site.
    Tries ld+json first; falls back to HTML scraping for chetnamakan.co.uk.
    """
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return {"error": str(e), "url": url}

    soup = BeautifulSoup(resp.text, "html.parser")

    # Try ld+json first (works for indianhealthyrecipes, hebbarskitchen, kannammacooks)
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

            ingredients = item.get("recipeIngredient", [])
            # Skip if ingredients is just group-header labels (short strings, no numbers)
            real_ingredients = [i for i in ingredients if any(c.isnumeric() for c in i) or len(i) > 15]
            if not real_ingredients:
                break  # ld+json unreliable for this page, fall through to HTML

            instructions = []
            for step in item.get("recipeInstructions", []):
                if isinstance(step, dict):
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
                "yield": str(item.get("recipeYield", "")),
                "ingredients": ingredients,
                "instructions": instructions,
                "cuisine": cuisine or "Indian",
                "category": item.get("recipeCategory", ""),
            }

    # Fallback: Chetna Makan HTML scraper
    if "chetnamakan.co.uk" in url:
        return _fetch_chetna_html(soup, url)

    title_el = soup.find("h1")
    return {
        "url": url,
        "title": title_el.get_text(strip=True) if title_el else "Unknown",
        "error": "No ld+json recipe schema found on this page",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_to_minutes(iso: str) -> int:
    """Parse ISO 8601 duration (PT1H30M or P0DT1H30M0S) to minutes."""
    if not iso:
        return 0
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
    if "indianhealthyrecipes.com" in url:
        return f"Indian Healthy Recipes - {url}"
    if "hebbarskitchen.com" in url:
        return f"Hebbars Kitchen - {url}"
    if "chetnamakan.co.uk" in url:
        return f"Chetna Makan - {url}"
    if "kannammacooks.com" in url:
        return f"Kannamma Cooks - {url}"
    return url


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_indianhealthyrecipes",
        "description": (
            "Search indianhealthyrecipes.com for Indian recipes by Swasthi Shreekanth. "
            "Covers a wide range of North and South Indian dishes with healthy adaptations. "
            "Use for: butter chicken, dal makhani, palak paneer, biryani, samosas, chana masala, "
            "aloo gobi, rajma, chole, korma, tikka masala, pulao, poha, upma, idli, dosa, rasam, "
            "sambar, and most mainstream Indian restaurant-style dishes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (dish name or main ingredient)"},
                "max_results": {"type": "integer", "description": "Max results (default 12)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_hebbarskitchen",
        "description": (
            "Search hebbarskitchen.com for Indian recipes. "
            "Strong on street food, snacks, sweets, and quick weeknight Indian dishes. "
            "Use for: pav bhaji, vada pav, chole bhature, paneer recipes, sabzi dishes, "
            "Indian breakfast (poha, upma, rava idli), sweets (halwa, kheer, ladoo), "
            "pakora, bhaji, chutney, and fusion Indian recipes. "
            "Note: some recipes on this site have been flagged on Reddit for attribution concerns."
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
        "name": "search_chetnamakan",
        "description": (
            "Search chetnamakan.co.uk for British-Indian recipes by Chetna Makan "
            "(GBBO Series 5, author of multiple Indian cookbooks). "
            "Her style is lighter, family-friendly Indian food adapted for a British kitchen. "
            "Use for: accessible Indian curries, bakes, one-pot dishes, lighter versions of "
            "classics, and recipes using easy-to-find UK/US ingredients. "
            "Also good for: chicken curries, fish curries, vegetarian dishes, breads, and desserts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (dish name or ingredient)"},
                "max_results": {"type": "integer", "description": "Max results (default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_kannammacooks",
        "description": (
            "Search kannammacooks.com for South Indian and Tamil recipes by Suguna Vinodh. "
            "Specializes in: Chettinad cuisine, Tamil Nadu dishes, Kongunad recipes, "
            "rice dishes (biryani, pulao, pongal, kozhukattai), South Indian breakfast "
            "(idli, dosa, appam, puttu, rava idli), rasam, sambar, kootu, poriyal, "
            "Chettinad chicken/mutton, fish curries, and Tamil Brahmin cooking. "
            "Also search here for: tamarind-based curries, coconut-heavy dishes, "
            "curry leaves and mustard seed tempering dishes."
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
            "Fetch a recipe from a URL on any of the supported Indian recipe sites. "
            "For indianhealthyrecipes.com, hebbarskitchen.com, and kannammacooks.com: "
            "extracts structured data via ld+json schema. "
            "For chetnamakan.co.uk: uses HTML scraping (WordPress block content). "
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

SYSTEM = """You are an Indian recipe finder covering North Indian, South Indian, British-Indian, and Tamil cuisines. Your job is to search for recipes and return what you find — you do NOT save anything. The user decides what to keep.

Available sources:
- indianhealthyrecipes.com (North/South Indian, mainstream restaurant dishes) — use search_indianhealthyrecipes
- hebbarskitchen.com (general Indian, street food, sweets, snacks) — use search_hebbarskitchen
- chetnamakan.co.uk (British-Indian, lighter family cooking, Chetna Makan) — use search_chetnamakan
- kannammacooks.com (South Indian / Tamil / Chettinad) — use search_kannammacooks

Routing rules:
- Apply culinary knowledge to route — do not rely on keywords alone.
- North Indian / Mughlai / restaurant-style (butter chicken, dal makhani, korma, biryani, naan, paneer tikka, kebabs) → search_indianhealthyrecipes, optionally also search_hebbarskitchen
- South Indian / Tamil / Chettinad (rasam, sambar, dosa, idli, chettinad chicken, poriyal, kootu) → search_kannammacooks, optionally also search_indianhealthyrecipes
- British-Indian / lighter / accessible (family curry, easy Indian weeknight) → search_chetnamakan
- Street food / snacks / sweets (pav bhaji, vada, pakora, halwa, kheer, ladoo) → search_hebbarskitchen
- Kerala / Goan / fish curry → search_indianhealthyrecipes + search_kannammacooks
- "Indian" or general query → search 2-3 sources most likely to yield relevant results
- Multi-style request → search all relevant sources

Cuisine reference (use your knowledge to extend):
- North Indian: butter chicken (murgh makhani), dal makhani, palak paneer, biryani, chole, rajma, aloo gobi, korma, rogan josh, tikka masala, sarson ka saag, pav bhaji
- South Indian: rasam, sambar, idli, dosa, appam, uttapam, pongal, chettinad chicken, kootu, poriyal, avial, bisi bele bath, lemon rice
- Tamil/Chettinad: chettinad curry, kuzhambu, kozhukattai, puttu, tamarind rice, kavuni arisi
- Mughlai: biryani, kebab, nihari, haleem, shahi tukda, sheermal
- Snacks/street food: samosa, pakora, chaat, vada, bhel puri, kachori, jalebi
- Sweets: gulab jamun, halwa, kheer, barfi, ladoo, rasgulla, payasam

Rules:
- Only fetch URLs returned by search tools. Never guess or construct URLs.
- Aim for 3-5 valid recipes (with ingredients and instructions) per request.
- Skip pages that return errors or have no extractable content.
- Search with specific dish names or ingredients — not vague terms like "Indian recipe."
- For general/ambiguous queries: search 2-3 sources using dish terms natural to each source.
- At the end, print a brief plain-text summary of what you found."""


def run_agent(user_request: str) -> list[dict]:
    messages = [{"role": "user", "content": user_request}]
    print(f"\nSearching: {user_request}\n")

    found_recipes = []

    while True:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM,
            tools=TOOLS,
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

            if name == "search_indianhealthyrecipes":
                print(f"  [Indian Healthy Recipes] Searching: {inp['query']!r}")
                result = search_indianhealthyrecipes(inp["query"], inp.get("max_results", 12))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_hebbarskitchen":
                print(f"  [Hebbars Kitchen] Searching: {inp['query']!r}")
                result = search_hebbarskitchen(inp["query"], inp.get("max_results", 12))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_chetnamakan":
                print(f"  [Chetna Makan] Searching: {inp['query']!r}")
                result = search_chetnamakan(inp["query"], inp.get("max_results", 10))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_kannammacooks":
                print(f"  [Kannamma Cooks] Searching: {inp['query']!r}")
                result = search_kannammacooks(inp["query"], inp.get("max_results", 12))
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

    RESULTS_PATH.write_text(json.dumps(found_recipes, indent=2), encoding="utf-8")
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
