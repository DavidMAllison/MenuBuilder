#!/usr/bin/env python3
"""
chef_agent.py -- Find recipes from favorite chefs using Claude as the agent.

Chefs:
  - Alton Brown (altonbrown.com)
  - Deb Perelman / Smitten Kitchen (smittenkitchen.com)
  - Chetna Makan (chetnamakan.co.uk)
  - J. Kenji López-Alt (YouTube)

Usage:
  chef "find a braised short rib recipe"
  chef "find a cookie recipe from Alton Brown"
  chef "find an Indian dish from Chetna"
  chef "find a Kenji smash burger recipe"

Specify a chef by name to restrict to that source. Otherwise all sources are searched.
Results written to /tmp/chef_agent_results.json.
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
from yt_utils import enrich_recipe_from_transcript, parse_yt_description, fetch_yt_snippet

if not os.environ.get("ANTHROPIC_API_KEY"):
    env_path = Path.home() / "projects/personal/sms-assistant/.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                break

RESULTS_PATH = Path(f"/tmp/chef_agent_results_{os.getuid()}.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

_YT_API_BASE       = "https://www.googleapis.com/youtube/v3"
_CHETNA_CHANNEL_ID = "UC1VkNUPA6ieOuwXmk4SSJZw"
_KENJI_CHANNEL_ID  = "UCqqJQ_cXSat0KIAVfIfKkVA"
_YT_STOP_WORDS     = {"recipe", "recipes", "the", "a", "an", "with", "and", "for", "in",
                      "of", "easy", "quick", "simple", "how", "to", "make", "indian"}


def _load_yt_api_key() -> str:
    try:
        return json.loads((Path(__file__).parent / "config.json").read_text()).get("youtube_api_key", "")
    except Exception:
        return ""


def _search_chetna_youtube(title: str) -> str:
    """Search Chetna Makan's YouTube channel for a video matching the recipe title.

    Returns a watch URL on a close title match, or "" if nothing confident found.
    This is called automatically when fetching from chetnamakan.co.uk — no agent
    tool call needed; the video link is just attached to the recipe dict.
    """
    api_key = _load_yt_api_key()
    if not api_key:
        return ""

    words = [w for w in re.sub(r"[^a-zA-Z0-9 ]", " ", title).lower().split()
             if w not in _YT_STOP_WORDS][:6]
    if not words:
        return ""

    params = {
        "channelId": _CHETNA_CHANNEL_ID,
        "q": " ".join(words),
        "type": "video",
        "part": "snippet",
        "maxResults": 5,
        "key": api_key,
    }
    try:
        with httpx.Client(timeout=10) as http:
            r = http.get(f"{_YT_API_BASE}/search", params=params)
            r.raise_for_status()
    except Exception:
        return ""

    for item in r.json().get("items", []):
        vt = item["snippet"]["title"].lower()
        vid = item["id"]["videoId"]
        # Channel-scoped search: accept the top result if any key word matches.
        # Hindi spelling variants (gobhi/gobi, palak/saag) mean strict multi-word
        # matching misses valid hits — YouTube's ranking already handles relevance.
        if any(w in vt for w in words):
            return f"https://www.youtube.com/watch?v={vid}"

    return ""

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


# --- Alton Brown ---

def search_altonbrown(query: str, max_results: int = 10) -> list[dict]:
    """Search Alton Brown's site via WP REST API (recipes custom post type)."""
    url = f"https://altonbrown.com/wp-json/wp/v2/recipes?search={query.replace(' ', '+')}&per_page={max_results}"
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    results = []
    for post in resp.json():
        title = BeautifulSoup(post.get("title", {}).get("rendered", ""), "html.parser").get_text(strip=True)
        link = post.get("link", "")
        if "/recipes/" in link and title:
            results.append({"title": title, "url": link})
    return results


# --- Smitten Kitchen (Deb Perelman) ---

def search_smittenkitchen(query: str, max_results: int = 10) -> list[dict]:
    """Search Smitten Kitchen via WP REST API."""
    url = f"https://smittenkitchen.com/wp-json/wp/v2/posts?search={query.replace(' ', '+')}&per_page={max_results}"
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    results = []
    for post in resp.json():
        title = BeautifulSoup(post.get("title", {}).get("rendered", ""), "html.parser").get_text(strip=True)
        link = post.get("link", "")
        if title and link:
            results.append({"title": title, "url": link})
    return results


def _fetch_smittenkitchen(url: str, soup: BeautifulSoup) -> dict:
    """Parse a Smitten Kitchen recipe via Jetpack Recipe plugin classes."""
    recipe_block = soup.find(class_="jetpack-recipe")
    if not recipe_block:
        return {"error": "No Jetpack recipe block — may not be a recipe post", "url": url}

    title_el = recipe_block.find(class_="jetpack-recipe-title")
    if not title_el:
        title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else ""

    # Time: "Time:1 hour" or "Prep Time:30 minutes Total Time:1 hour" — grab last Time: value
    time_str = ""
    time_el = recipe_block.find(class_="jetpack-recipe-time")
    if time_el:
        raw = time_el.get_text(strip=True)
        match = re.search(r"(?:Total\s+)?Time:\s*(.+?)(?:\s+\w+\s+Time:|$)", raw, re.IGNORECASE)
        time_str = match.group(1).strip() if match else re.sub(r".*Time:", "", raw, flags=re.IGNORECASE).strip()

    ingredients = [
        el.get_text(strip=True)
        for el in recipe_block.find_all(class_="jetpack-recipe-ingredient")
        if el.get_text(strip=True)
    ]

    instructions = []
    dirs_div = recipe_block.find(class_="jetpack-recipe-directions")
    if dirs_div:
        for line in dirs_div.get_text(separator="\n", strip=True).splitlines():
            line = line.strip()
            if line:
                instructions.append(line)

    servings_el = recipe_block.find(class_="jetpack-recipe-servings")
    yield_str = servings_el.get_text(strip=True) if servings_el else ""

    if not ingredients:
        return {"error": "No ingredients found", "url": url}

    return {
        "url": url,
        "title": title,
        "description": "",
        "prep_time": "",
        "cook_time": "",
        "total_time": "",
        "yield": yield_str,
        "ingredients": ingredients,
        "instructions": instructions,
        "cuisine": "American",
        "image": _og_image(soup),
        "category": "",
        "time": time_str,
    }


# --- Chetna Makan ---

def search_chetnamakan(query: str, max_results: int = 10) -> list[dict]:
    """Search chetnamakan.co.uk via WordPress search."""
    url = f"https://chetnamakan.co.uk/?s={query.replace(' ', '+')}"
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
        if href in seen_urls or "chetnamakan.co.uk" not in href:
            continue
        seen_urls.add(href)
        results.append({"title": title_el.get_text(strip=True), "url": href})
        if len(results) >= max_results:
            break

    return results


def _fetch_chetnamakan(url: str, soup: BeautifulSoup) -> dict:
    """Parse a Chetna Makan recipe from entry-content.

    Structure: <p><strong>Ingredients</strong><br/>item<br/>item...</p>
               <p><strong>Method</strong><br/>- step<br/><br/>- step...</p>
    """
    title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else ""

    content = soup.find(class_="entry-content")
    if not content:
        return {"error": "No entry-content found", "url": url}

    ingredients = []
    instructions = []

    for p in content.find_all("p"):
        strong = p.find("strong")
        if not strong:
            continue
        section = strong.get_text(strip=True)
        raw = p.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in raw.splitlines() if l.strip()]

        if section == "Ingredients":
            for line in lines:
                if line != "Ingredients":
                    ingredients.append(line)
        elif section == "Method":
            for line in lines:
                line = line.lstrip("-").lstrip("–").strip()
                if line and line != "Method":
                    instructions.append(line)

    if not ingredients:
        return {"error": "No ingredients found", "url": url}

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
        "time": "",
        "image": _og_image(soup),
        "video_url": _search_chetna_youtube(title),
    }


# --- Generic fetch with ld+json ---

def _iso_to_minutes(iso: str) -> int:
    if not iso:
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", iso)
    if not m:
        return 0
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


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


def fetch_recipe(url: str) -> dict:
    """Fetch a recipe page and extract structured data."""
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return {"error": str(e), "url": url}

    soup = BeautifulSoup(resp.text, "html.parser")

    # Try ld+json first (Alton Brown has this)
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

            total_time = item.get("totalTime") or item.get("cookTime", "")
            return {
                "url": url,
                "title": item.get("name", "").strip(),
                "description": item.get("description", "").strip(),
                "prep_time": item.get("prepTime", ""),
                "cook_time": item.get("cookTime", ""),
                "total_time": total_time,
                "yield": str(item.get("recipeYield", "")),
                "ingredients": item.get("recipeIngredient", []),
                "instructions": instructions,
                "cuisine": item.get("recipeCuisine", ""),
                "category": item.get("recipeCategory", ""),
                "time": _iso_to_human(total_time),
                "image": _ld_image(item) or _og_image(soup),
                "video_url": _extract_video_url(item),
            }

    # Site-specific parsers
    if "smittenkitchen.com" in url:
        return _fetch_smittenkitchen(url, soup)
    if "chetnamakan.co.uk" in url:
        return _fetch_chetnamakan(url, soup)
    if "youtube.com" in url or "youtu.be" in url:
        return fetch_kenji(url)

    title_el = soup.find("h1")
    return {
        "url": url,
        "title": title_el.get_text(strip=True) if title_el else "Unknown",
        "error": "No ld+json recipe schema and no site-specific parser for this URL",
        "image": _og_image(soup),
    }


def _convert_measurements_batch(recipes: list[dict]) -> None:
    """Convert metric measurements to US equivalents via Haiku. Modifies in-place."""
    if not recipes:
        return
    metric_pat = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:g|gms?|ml|kg|l|cl)\b", re.IGNORECASE)
    to_convert = [r for r in recipes if any(
        metric_pat.search(ing) for ing in r.get("ingredients", []) if not ing.startswith("[")
    )]
    if not to_convert:
        return
    blocks = []
    for i, r in enumerate(to_convert):
        blocks.append(f"RECIPE {i + 1}: {r.get('title', '')}")
        blocks.append("Ingredients: " + " | ".join(r.get("ingredients", [])))
        blocks.append("")
    prompt = (
        "Convert all metric measurements in the following recipe ingredients to US measurements. "
        "If an ingredient already has both metric and US (e.g. '200 g 7 oz'), remove the metric part and keep only the US value. "
        "If it only has metric, convert it (g/gms/gm→oz or lbs, ml→cups/tbsp/tsp, kg→lbs). "
        "Keep all other text exactly as-is. "
        "Return a JSON array with one object per recipe in the same order:\n"
        '[{"ingredients": ["..."]}]\n\n'
        + "\n".join(blocks)
    )
    haiku = anthropic.Anthropic()
    resp = haiku.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    text = re.sub(r"```(?:json)?\s*", "", resp.content[0].text.strip()).strip().rstrip("`")
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        print("  [measurement conversion] Could not parse Haiku response — keeping original measurements")
        return
    converted = json.loads(m.group())
    for orig, conv in zip(to_convert, converted):
        orig["ingredients"] = conv.get("ingredients", orig["ingredients"])


def _source_label(url: str) -> str:
    if "altonbrown.com" in url:
        return "Alton Brown"
    if "smittenkitchen.com" in url:
        return "Deb Perelman (Smitten Kitchen)"
    if "chetnamakan.co.uk" in url:
        return "Chetna Makan"
    return url


def search_kenji(query: str, max_results: int = 8) -> list[dict]:
    """Search J. Kenji López-Alt's YouTube channel via YouTube Data API v3."""
    api_key = _load_yt_api_key()
    if not api_key:
        return [{"error": "youtube_api_key not set in config.json"}]
    params = {
        "channelId": _KENJI_CHANNEL_ID,
        "q": query,
        "type": "video",
        "part": "snippet",
        "maxResults": min(max_results * 2, 50),
        "key": api_key,
    }
    try:
        with httpx.Client(timeout=15) as http:
            r = http.get(f"{_YT_API_BASE}/search", params=params)
            r.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    results = []
    for item in r.json().get("items", []):
        title = item["snippet"]["title"]
        if "#short" in title.lower() or "shorts" in title.lower():
            continue
        video_id = item["id"]["videoId"]
        results.append({"title": title, "url": f"https://www.youtube.com/watch?v={video_id}"})
        if len(results) >= max_results:
            break
    return results


def fetch_kenji(url: str) -> dict:
    """Fetch a J. Kenji López-Alt YouTube video and extract recipe via description + transcript."""
    api_key = _load_yt_api_key()
    if not api_key:
        return {"error": "youtube_api_key not set in config.json", "url": url}

    m = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", url)
    if not m:
        return {"error": "Could not extract video ID from URL", "url": url}
    video_id = m.group(1)

    snippet = fetch_yt_snippet(video_id, api_key)
    if not snippet:
        return {"error": "Video not found", "url": url}

    ingredients, desc_instructions = parse_yt_description(snippet["description"])

    recipe = {
        "url": url,
        "video_url": url,
        "title": snippet["title"],
        "description": "",
        "prep_time": "",
        "cook_time": "",
        "total_time": "",
        "yield": "",
        "ingredients": ingredients,
        "instructions": desc_instructions,
        "cuisine": "American",
        "category": "",
        "image": snippet["image"],
        "source": "J. Kenji López-Alt",
    }

    enrich_recipe_from_transcript(recipe, video_id)

    if not recipe["ingredients"]:
        return {"error": "No ingredients found in description or transcript", "url": url, "title": snippet["title"]}
    if not recipe["instructions"]:
        recipe["instructions"] = [f"See video: {url}"]

    return recipe


def _cuisine_from_url(url: str) -> str:
    if "altonbrown.com" in url:
        return "American"
    if "smittenkitchen.com" in url:
        return "American"
    if "chetnamakan.co.uk" in url:
        return "Indian"
    return "American"


# --- Tool definitions for Claude ---

TOOLS = [
    {
        "name": "search_altonbrown",
        "description": (
            "Search Alton Brown's recipe site (altonbrown.com). "
            "Covers all cuisines with a focus on technique and American classics. "
            "Returns recipe titles and URLs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Dish name or ingredient to search"},
                "max_results": {"type": "integer", "description": "Max results to return (default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_smittenkitchen",
        "description": (
            "Search Smitten Kitchen (smittenkitchen.com) by Deb Perelman. "
            "Broad cuisines, home-cook focus, vegetable-forward. "
            "Returns post titles and URLs. Some results may be non-recipe posts — "
            "fetch to confirm recipe content before including."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Dish name or ingredient to search"},
                "max_results": {"type": "integer", "description": "Max results to return (default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_chetnamakan",
        "description": (
            "Search Chetna Makan's recipe site (chetnamakan.co.uk). "
            "Primarily Indian cuisine with British/Western fusion. "
            "Returns recipe titles and URLs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Dish name or ingredient to search"},
                "max_results": {"type": "integer", "description": "Max results to return (default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_kenji",
        "description": (
            "Search J. Kenji López-Alt's YouTube channel for recipes. "
            "Returns video titles and URLs. Strong on burgers, steaks, pasta, Asian-American, "
            "and technique-driven home cooking. Ingredients and steps extracted from transcript."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Dish name or ingredient to search"},
                "max_results": {"type": "integer", "description": "Max results to return (default 8)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_recipe",
        "description": (
            "Fetch and parse a recipe from a URL returned by a search tool. "
            "Extracts title, ingredients, instructions, and timing. "
            "Only use URLs returned by search tools — never construct or guess URLs."
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

SYSTEM = """You are a recipe finder covering favorite chefs and food writers. Your job is to search for recipes and return what you find — you do NOT save anything. The user decides what to keep.

Available sources:
- Alton Brown (altonbrown.com) — use search_altonbrown. All cuisines, strong on American classics and technique.
- Deb Perelman / Smitten Kitchen (smittenkitchen.com) — use search_smittenkitchen. Broad cuisines, home-cook focus.
- Chetna Makan (chetnamakan.co.uk) — use search_chetnamakan. Indian cuisine and British/Indian fusion.
- J. Kenji López-Alt (YouTube) — use search_kenji. Technique-driven home cooking; strong on burgers, steaks, pasta, stir-fries, Asian-American. Recipe content extracted from video transcript.

Rules:
- Only fetch URLs returned by search tools. Never guess or construct URLs.
- If the user names a specific chef (Alton, Alton Brown, Deb, Smitten Kitchen, Chetna, Chetna Makan, Kenji, Kenji Lopez-Alt), restrict to that source only. Otherwise search all sources.
- If the user asks for a specific cuisine, use your knowledge to search for dish names from that cuisine rather than searching the cuisine name itself. Example: for "Indian chicken dish" search for "butter chicken", "tikka masala", "chicken biryani", not "Indian chicken".
- Aim to find 3-5 valid recipes (with ingredients and instructions) per request. Skip results with errors or missing content.
- In your final summary, note the source chef and likely cuisine for each recipe found."""

_CACHED_SYSTEM = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
_CACHED_TOOLS = [*TOOLS[:-1], {**TOOLS[-1], "cache_control": {"type": "ephemeral"}}]


def run_agent(user_request: str) -> list[dict]:
    messages = [{"role": "user", "content": user_request}]
    print(f"\nSearching: {user_request}\n")

    found_recipes = []
    chetna_recipes: list[dict] = []  # Chetna Makan uses metric measurements

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

            if name == "search_altonbrown":
                print(f"  [Alton Brown] Searching: {inp['query']!r}")
                result = search_altonbrown(inp["query"], inp.get("max_results", 10))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_smittenkitchen":
                print(f"  [Smitten Kitchen] Searching: {inp['query']!r}")
                result = search_smittenkitchen(inp["query"], inp.get("max_results", 10))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_chetnamakan":
                print(f"  [Chetna Makan] Searching: {inp['query']!r}")
                result = search_chetnamakan(inp["query"], inp.get("max_results", 10))
                print(f"  Found {len(result)} result(s)")

            elif name == "search_kenji":
                print(f"  [Kenji] Searching: {inp['query']!r}")
                result = search_kenji(inp["query"], inp.get("max_results", 8))
                print(f"  Found {len(result)} result(s)")

            elif name == "fetch_recipe":
                print(f"  Fetching: {inp['url']}")
                result = fetch_recipe(inp["url"])
                if "error" in result:
                    print(f"  Error: {result['error']}")
                else:
                    time_str = result.get("time", "")
                    title = result.get("title", "unknown")
                    print(f"  Got: {title}" + (f" ({time_str})" if time_str else ""))
                    if result.get("ingredients") and result.get("instructions"):
                        if not result.get("source"):
                            result["source"] = _source_label(result["url"])
                        if not result.get("cuisine"):
                            result["cuisine"] = _cuisine_from_url(result["url"])
                        found_recipes.append(result)
                        if "chetnamakan.co.uk" in result["url"]:
                            chetna_recipes.append(result)

            else:
                result = {"error": f"Unknown tool: {name}"}

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        messages.append({"role": "user", "content": tool_results})

    if chetna_recipes:
        print(f"Converting measurements in {len(chetna_recipes)} Chetna Makan recipe(s)...")
        _convert_measurements_batch(chetna_recipes)

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
            detail = " | ".join(filter(None, [source, cuisine, time_str]))
            print(f"  {i}. {r.get('title', '?')} ({detail})")
        print(f"\nResults saved to {RESULTS_PATH}")
    else:
        print("\nNo recipes found.")
