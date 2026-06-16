#!/usr/bin/env python3
"""
mexican_agent.py -- Find Mexican recipes using Claude as the agent.

Sources:
  - patijinich.com (Pati Jinich)

Usage:
  mexican "find recipes from Oaxaca"
  mexican "find a chicken mole recipe"
  mexican "get some weeknight dishes from Yucatan"

The agent searches and fetches recipes, then writes results to
/tmp/mexican_agent_results.json for the caller to review and save.
"""

import json
import os
import re
import sys
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
import anthropic

# Load API key from sms-assistant .env if not already in environment
if not os.environ.get("ANTHROPIC_API_KEY"):
    env_path = Path.home() / "projects/personal/sms-assistant/.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                break

RESULTS_PATH = Path(f"/tmp/mexican_agent_results_{os.getuid()}.json")
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


# --- Tool implementations ---

_SKIP_URL_PATTERNS = ("episode", "/season-", "book", "event", "award", "nominated", "james-beard")
_CLAUDIA_CHANNEL_ID = "UC0tVWRw4aNoVqXaobA8E_Ig"
_MEXICAN_KEYWORDS = (
    "mexican", "receta", "tacos", "tamales", "enchiladas", "mole", "salsa",
    "pozole", "carnitas", "chile", "tortilla", "frijoles", "arroz", "birria",
    "menudo", "barbacoa", "caldo", "sopa", "tinga", "chorizo", "nopal",
)


def search_claudia(query: str, max_results: int = 8) -> list[dict]:
    """Search Cooking con Claudia's YouTube channel for Mexican recipes."""
    import yt_dlp
    import warnings
    warnings.filterwarnings("ignore")

    search_url = f"https://www.youtube.com/channel/{_CLAUDIA_CHANNEL_ID}/search?query={query.replace(' ', '+')}"
    ydl_opts = {"quiet": True, "extract_flat": True, "playlist_items": f"1:{max_results * 2}"}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_url, download=False)
    except Exception as e:
        return [{"error": str(e)}]

    results = []
    for entry in (info.get("entries") or []):
        title = entry.get("title", "")
        video_id = entry.get("id") or entry.get("url", "")
        if not video_id:
            continue
        url = f"https://www.youtube.com/watch?v={video_id}" if not video_id.startswith("http") else video_id
        # Filter out non-Mexican content — skip if no Mexican keyword in title
        title_lower = title.lower()
        if not any(k in title_lower for k in _MEXICAN_KEYWORDS):
            continue
        # Skip Shorts (usually under 60s, less useful for full recipes)
        if "#short" in title_lower or "shorts" in title_lower:
            continue
        results.append({"title": title, "url": url})
        if len(results) >= max_results:
            break

    return results


def _parse_claudia_description(description: str) -> list[str]:
    """Extract ingredient lines from a Cooking con Claudia video description."""
    lines = description.splitlines()
    ingredients = []
    in_ingredients = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower() == "ingredients":
            in_ingredients = True
            continue
        if in_ingredients:
            # Stop at blank line after ingredients or a new section heading
            if not stripped:
                if ingredients:
                    break
                continue
            # Stop if it looks like a URL or social link
            if stripped.startswith("http") or stripped.startswith("@") or stripped.startswith("#"):
                break
            ingredients.append(stripped)
    return ingredients


def fetch_claudia(url: str) -> dict:
    """Fetch a Cooking con Claudia YouTube video and extract recipe from description."""
    import yt_dlp
    import warnings
    warnings.filterwarnings("ignore")

    ydl_opts = {"quiet": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return {"error": str(e), "url": url}

    title = info.get("title", "").strip()
    description = info.get("description", "")
    ingredients = _parse_claudia_description(description)

    if not ingredients:
        return {"error": "No ingredients found in description", "url": url, "title": title}

    return {
        "url": url,
        "title": title,
        "description": "",
        "prep_time": "",
        "cook_time": "",
        "total_time": "",
        "yield": "",
        "ingredients": ingredients,
        "instructions": [f"See video: {url}"],
        "cuisine": "Mexican",
        "category": "",
        "image": info.get("thumbnail", ""),
    }


def search_rickbayless(query: str, max_results: int = 20) -> list[dict]:
    """Search rickbayless.com and return matching recipe titles + URLs."""
    url = f"https://www.rickbayless.com/recipes-from-chef-rick-bayless/?q={query.replace(' ', '+')}"
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("https://www.rickbayless.com/recipe/"):
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)
        # Title from link text; strip leading category prefix (e.g. "PorkCarnitas" -> "Carnitas")
        raw = a.get_text(strip=True)
        # The slug is more reliable than the link text
        slug = href.rstrip("/").split("/")[-1].replace("-", " ").title()
        results.append({"title": raw or slug, "url": href})
        if len(results) >= max_results:
            break

    return results


def _fetch_rickbayless(url: str, soup: BeautifulSoup) -> dict:
    """Parse a Rick Bayless recipe page from HTML (no ld+json schema)."""
    title_el = soup.find("h1") or soup.find("h2", class_="recipe-title")
    title = title_el.get_text(strip=True) if title_el else ""

    ing_div = soup.find(class_="recipe-ingredients")
    ingredients = []
    if ing_div:
        for li in ing_div.find_all("li", itemprop="ingredients"):
            text = li.get_text(" ", strip=True)
            if text:
                ingredients.append(text)

    ins_div = soup.find(class_="recipe-instructions")
    instructions = []
    if ins_div:
        for p in ins_div.find_all("p"):
            text = p.get_text(strip=True)
            if text:
                instructions.append(text)

    servings_el = soup.find(class_="recipe-servings")
    yield_str = servings_el.get_text(strip=True).replace("Servings:", "").strip() if servings_el else ""

    desc_el = soup.find(class_="recipe-description")
    description = desc_el.get_text(strip=True) if desc_el else ""

    return {
        "url": url,
        "title": title,
        "description": description,
        "prep_time": "",
        "cook_time": "",
        "total_time": "",
        "yield": yield_str,
        "ingredients": ingredients,
        "instructions": instructions,
        "cuisine": "Mexican",
        "category": "",
        "image": _og_image(soup),
    }

def search_patijinich(query: str, max_results: int = 20) -> list[dict]:
    """Search patijinich.com and return matching recipe titles + URLs."""
    url = f"https://patijinich.com/?s={query.replace(' ', '+')}"
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
        if href in seen_urls or "patijinich.com" not in href:
            continue
        if any(p in href for p in _SKIP_URL_PATTERNS):
            continue
        seen_urls.add(href)
        results.append({"title": title_el.get_text(strip=True), "url": href})
        if len(results) >= max_results:
            break

    return results


def _fetch_patijinich_wprm(url: str) -> dict:
    """Fetch a Pati Jinich recipe using the WPRM print URL (more reliable than ld+json).

    Converts e.g. https://patijinich.com/lime-rubbed-chicken-tacos-with-corn-guacamole/
    to           https://patijinich.com/wprm_print/lime-rubbed-chicken-tacos-with-corn-guacamole
    """
    slug = url.rstrip("/").split("/")[-1]
    print_url = f"https://patijinich.com/wprm_print/{slug}"
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as http:
            resp = http.get(print_url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return {"error": str(e), "url": url}

    soup = BeautifulSoup(resp.text, "html.parser")

    title_el = soup.find(class_="wprm-recipe-name")
    title = title_el.get_text(strip=True) if title_el else ""

    ingredients = [
        el.get_text(separator=" ", strip=True)
        for el in soup.find_all(class_="wprm-recipe-ingredient")
        if el.get_text(strip=True)
    ]

    instructions = [
        el.get_text(separator=" ", strip=True)
        for el in soup.find_all(class_="wprm-recipe-instruction-text")
        if el.get_text(strip=True)
    ]

    if not ingredients and not instructions:
        return {"error": "No WPRM recipe content found on print page", "url": url}

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
        "cuisine": "Mexican",
        "category": "",
        "image": _og_image(soup),
    }


def fetch_recipe(url: str) -> dict:
    """Fetch a recipe page and extract structured data.

    patijinich.com: uses the WPRM print URL (wprm_print/<slug>) for reliable extraction.
    rickbayless.com: site-specific HTML parser.
    YouTube: delegates to fetch_claudia.
    All others: ld+json schema.
    """
    # Pati Jinich — WPRM print URL is more reliable than ld+json
    if "patijinich.com" in url:
        return _fetch_patijinich_wprm(url)

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
                    instructions.append(step.get("text", "").strip())
                elif isinstance(step, str):
                    instructions.append(step.strip())

            return {
                "url": url,
                "title": item.get("name", "").strip(),
                "description": item.get("description", "").strip(),
                "prep_time": item.get("prepTime", ""),
                "cook_time": item.get("cookTime", ""),
                "total_time": item.get("totalTime", ""),
                "yield": str(item.get("recipeYield", "")),
                "ingredients": item.get("recipeIngredient", []),
                "instructions": instructions,
                "cuisine": item.get("recipeCuisine", "Mexican"),
                "category": item.get("recipeCategory", ""),
                "image": _ld_image(item) or _og_image(soup),
            }

    # No ld+json — try site-specific parsers
    if "rickbayless.com" in url:
        return _fetch_rickbayless(url, soup)
    if "youtube.com" in url or "youtu.be" in url:
        return fetch_claudia(url)

    title_el = soup.find("h1")
    return {
        "url": url,
        "title": title_el.get_text(strip=True) if title_el else "Unknown",
        "error": "No ld+json recipe schema found on this page",
        "image": _og_image(soup),
    }


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
    if "patijinich.com" in url:
        return "Pati Jinich"
    if "rickbayless.com" in url:
        return "Rick Bayless"
    if "youtube.com" in url or "youtu.be" in url:
        return "Cooking con Claudia"
    return url


# --- Tool definitions for Claude ---

TOOLS = [
    {
        "name": "search_patijinich",
        "description": (
            "Search patijinich.com for Mexican recipes matching a query. "
            "Returns a list of recipe titles and URLs. "
            "Use specific dish/ingredient names as queries, not region names."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term"},
                "max_results": {"type": "integer", "description": "Max results to return (default 20)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_claudia",
        "description": (
            "Search Cooking con Claudia's YouTube channel for Mexican recipes. "
            "Returns video titles and URLs. Only returns Mexican dishes — non-Mexican videos are filtered out. "
            "Ingredients are in the video description; instructions require watching the video."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (dish name or ingredient)"},
                "max_results": {"type": "integer", "description": "Max results to return (default 8)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_rickbayless",
        "description": (
            "Search rickbayless.com for Mexican recipes matching a query. "
            "Returns a list of recipe titles and URLs. "
            "Use specific dish/ingredient names as queries."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term"},
                "max_results": {"type": "integer", "description": "Max results to return (default 20)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_recipe",
        "description": (
            "Fetch a recipe from a URL on a supported Mexican recipe site. "
            "Extracts title, ingredients, instructions, and timing. "
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

SYSTEM = """You are a Mexican recipe finder. Your job is to search for recipes and return what you find — you do NOT save anything. The user will decide what to keep.

Available sources:
- patijinich.com (Pati Jinich) — use search_patijinich
- rickbayless.com (Rick Bayless) — use search_rickbayless
- Cooking con Claudia (YouTube) — use search_claudia; ingredients from description, instructions link to video

Rules:
- Only fetch URLs returned by search tools. Never guess or construct URLs.
- When the user asks for recipes from a specific region, use your knowledge of that region's cuisine to search for specific dishes and ingredients — do NOT search the region name directly. Examples:
  - Oaxaca → mole negro, tlayuda, tasajo, memelas, enfrijoladas, hoja santa, chapulines, quesillo
  - Yucatan → cochinita pibil, sopa de lima, papadzules, poc chuc, panuchos
  - Veracruz → huachinango, picadillo, arroz a la tumbada, enchiladas veracruzanas
  - Puebla → mole poblano, chiles en nogada, cemita, tinga
  - Mexico City → tacos de canasta, chilaquiles, barbacoa, gorditas
  - Chihuahua → machaca, chile colorado, caldillo, asado de bodas, discada, carne seca
  Try 3-4 specific queries before giving up on a region.
- Aim to find 3-5 valid recipes (with ingredients and instructions) per request.
- Skip pages that return errors or have no ingredients/instructions.
- At the end, write a brief plain-text summary of what you found."""


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

            if name == "search_patijinich":
                print(f"  [Pati Jinich] Searching: {inp['query']!r}")
                result = search_patijinich(inp["query"], inp.get("max_results", 20))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_claudia":
                print(f"  [Cooking con Claudia] Searching: {inp['query']!r}")
                result = search_claudia(inp["query"], inp.get("max_results", 8))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_rickbayless":
                print(f"  [Rick Bayless] Searching: {inp['query']!r}")
                result = search_rickbayless(inp["query"], inp.get("max_results", 20))
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
                    # Collect valid recipes (must have ingredients + instructions)
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
            source = r.get("source", "").split(" - ")[0]  # "Pati Jinich" from "Pati Jinich - https://..."
            time_str = r.get("time", "")
            detail = " | ".join(filter(None, [source, time_str]))
            print(f"  {i}. {r.get('title', '?')} ({detail})")
        print(f"\nResults saved to {RESULTS_PATH}")
    else:
        print("\nNo recipes found.")
