#!/usr/bin/env python3
"""
italian_agent.py -- Find Italian recipes from authoritative Italian cooking sites.

Sources:
  - giallozafferano.com   (English, Playwright, ld+json)
  - cucchiaio.it          (Italian content → Haiku translation, Playwright fetch, sitemap search)
  - memoriediangelina.com (English, httpx, WPRM HTML, Roman/Southern Italian focus)

Usage:
  python3 italian_agent.py "pasta carbonara"
  python3 italian_agent.py "Italian fish dinner weeknight"
  python3 italian_agent.py "braised Italian pork"
"""

import html as html_lib
import json
import os
import re
import sys
from pathlib import Path

import anthropic
import httpx
from bs4 import BeautifulSoup

if not os.environ.get("ANTHROPIC_API_KEY"):
    env_path = Path.home() / "projects/personal/sms-assistant/.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                break

RESULTS_PATH = Path(f"/tmp/italian_agent_results_{os.getuid()}.json")
CUCCHIAIO_SITEMAP_CACHE = Path(f"/tmp/cucchiaio_sitemap_{os.getuid()}.txt")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

client = anthropic.Anthropic()

# Shared Playwright page — set inside run_agent, used by search/fetch functions
_pw_page = None


# ---------------------------------------------------------------------------
# GialloZafferano (giallozafferano.com) — Playwright, English, ld+json
# ---------------------------------------------------------------------------

def search_giallozafferano(query: str, max_results: int = 8) -> list[dict]:
    """Search giallozafferano.com via Playwright. Returns list of {title, url}."""
    search_url = f"https://www.giallozafferano.com/recipes-search/{query.replace(' ', '+')}/"
    try:
        _pw_page.goto(search_url)
        _pw_page.wait_for_load_state("domcontentloaded")
        links = _pw_page.evaluate(f"""() => {{
            const seen = new Set();
            const results = [];
            for (const a of document.querySelectorAll('a[href]')) {{
                const href = a.href;
                if (!href.includes('/recipes/') || !href.endsWith('.html')) continue;
                const text = a.innerText.trim();
                if (!text || seen.has(href)) continue;
                seen.add(href);
                results.push({{ title: text, url: href }});
                if (results.length >= {max_results}) break;
            }}
            return results;
        }}""")
        return links or []
    except Exception as e:
        return [{"error": str(e)}]


_CROSS_REF_INSTRUCTIONS = re.compile(
    r'\bsame (mixture|filling|dough|batter|sauce)\b'
    r'|\bleftover \w+'
    r'|\bsee (the|my) .{0,30} recipe\b'
    r'|\bas described in\b'
    r'|\bfrom (the|my|this) .{0,20} recipe\b'
    r'|\bfollow the .{0,30} recipe\b',
    re.IGNORECASE,
)

_GZF_STEP_REF = re.compile(
    r'\s*\(\d{1,2}[-–]\d{1,2}\)'      # ranges: (14-15), (2-3)
    r'|\s*\(\d{1,2}\)'                  # singles: (1), (9)
    # Standalone 1-2 digit number after a word, before punctuation or certain followers
    r'|(?<=\w)\s+\d{1,2}\s*(?=[,;:.])'  # before , ; : .
    r'|(?<=\w)\s+\d{1,2}\s*$'           # bare trailing at end of string
    r'|(?<=\w)\s+\d{1,2}(?=\s+(?:and|then|or|but|so|now|also|next|finally|once|when|while|until|as|at|to|the|a|an|in|on|of|for|with|from|your|them|it|this|they|you|let|place|add|pour|cook|remove|stir|cover|serve|bake|grill|heat)\b)',
)


def _strip_gzf_step_refs(text: str) -> str:
    """Remove GialloZafferano inline photo-step reference numbers from instruction text."""
    return _GZF_STEP_REF.sub("", text).strip()


def fetch_giallozafferano(url: str) -> dict:
    """Fetch a GialloZafferano recipe page and extract ld+json data."""
    try:
        _pw_page.goto(url)
        _pw_page.wait_for_load_state("domcontentloaded")
        data = _pw_page.evaluate("""() => {
            for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                try {
                    const d = JSON.parse(s.textContent);
                    const arr = Array.isArray(d) ? d : (d['@graph'] ? d['@graph'] : [d]);
                    const r = arr.find(x => x && x['@type'] === 'Recipe');
                    if (!r) continue;
                    const vidF = r.video;
                    let videoUrl = '';
                    if (vidF) {
                        const v = Array.isArray(vidF) ? vidF[0] : vidF;
                        if (typeof v === 'string') videoUrl = v;
                        else if (v) videoUrl = v.contentUrl || v.embedUrl || v.url || '';
                        const ytM = videoUrl.match(/youtube\.com\/embed\/([A-Za-z0-9_-]+)/);
                        if (ytM) videoUrl = 'https://www.youtube.com/watch?v=' + ytM[1];
                    }
                    return {
                        name: r.name || '',
                        totalTime: r.totalTime || r.cookTime || '',
                        recipeYield: String(r.recipeYield || ''),
                        recipeIngredient: r.recipeIngredient || [],
                        recipeInstructions: (r.recipeInstructions || []).map(s =>
                            typeof s === 'string' ? s : (s.text || '')),
                        recipeCuisine: r.recipeCuisine || 'Italian',
                        image: r.image ? (Array.isArray(r.image) ? r.image[0] : (typeof r.image === 'object' ? r.image.url : r.image)) : (document.querySelector('meta[property="og:image"]')?.content || ''),
                        video_url: videoUrl,
                    };
                } catch(e) {}
            }
            return null;
        }""")
    except Exception as e:
        return {"error": str(e), "url": url}

    if not data:
        return {"error": "No ld+json Recipe schema found", "url": url}

    ingredients = [html_lib.unescape(i).strip() for i in data.get("recipeIngredient", []) if i]
    instructions = [_strip_gzf_step_refs(s) for s in data.get("recipeInstructions", []) if s and len(s.strip()) > 10]

    if not ingredients or not instructions:
        return {"error": "Incomplete recipe data in ld+json", "url": url}

    return {
        "url": url,
        "title": html_lib.unescape(data.get("name", "").strip()),
        "total_time": data.get("totalTime", ""),
        "yield": data.get("recipeYield", ""),
        "ingredients": ingredients,
        "instructions": instructions,
        "cuisine": data.get("recipeCuisine", "Italian"),
        "image": data.get("image", ""),
        "video_url": data.get("video_url", ""),
    }


# ---------------------------------------------------------------------------
# Cucchiaio d'Argento (cucchiaio.it) — sitemap search, Playwright fetch, Italian → translated
# ---------------------------------------------------------------------------

def _get_cucchiaio_urls() -> list[str]:
    """Return all recipe URLs from cucchiaio.it sitemap, cached locally."""
    if CUCCHIAIO_SITEMAP_CACHE.exists():
        return CUCCHIAIO_SITEMAP_CACHE.read_text().splitlines()
    r = httpx.get(
        "https://www.cucchiaio.it/Sitemap-content-RICETTE.xml",
        headers=HEADERS, timeout=30, follow_redirects=True,
    )
    urls = re.findall(r"<loc>(https://www\.cucchiaio\.it/ricetta/[^<]+)</loc>", r.text)
    CUCCHIAIO_SITEMAP_CACHE.write_text("\n".join(urls))
    return urls


def search_cucchiaio(query: str, max_results: int = 8) -> list[dict]:
    """Search cucchiaio.it recipe sitemap by Italian keyword(s) in the URL slug."""
    urls = _get_cucchiaio_urls()
    keywords = [k.lower() for k in query.split() if len(k) > 2]
    results = []
    seen = set()
    for url in urls:
        slug = url.rstrip("/").split("/")[-1]
        slug_words = slug.replace("-", " ")
        slug_words = re.sub(r"^ricetta ", "", slug_words)
        if any(kw in slug_words for kw in keywords) and url not in seen:
            seen.add(url)
            title = slug_words.title()
            results.append({"title": title, "url": url})
            if len(results) >= max_results:
                break
    return results


def fetch_cucchiaio(url: str) -> dict:
    """Fetch a cucchiaio.it recipe page via Playwright and extract Italian content."""
    try:
        _pw_page.goto(url)
        _pw_page.wait_for_load_state("domcontentloaded")
        data = _pw_page.evaluate("""() => {
            const title = (document.querySelector('h1') || {}).textContent?.trim() || '';
            const ings = [...document.querySelectorAll('.c-recipe__list2 li')]
                .map(el => el.textContent.trim()).filter(Boolean);
            const steps = [...document.querySelectorAll('.recipe_procedures')]
                .map(el => el.textContent.trim())
                .filter(s => !s.includes('{') && !s.includes('position:') && s.length > 20)
                .map(s => s.replace(/^\\d+\\s*/, '').trim());
            const image = document.querySelector('meta[property="og:image"]')?.content || '';
            return { title, ings, steps, image };
        }""")
    except Exception as e:
        return {"error": str(e), "url": url}

    if not data.get("ings") or not data.get("steps"):
        return {"error": "Could not extract recipe content from cucchiaio.it", "url": url}

    return {
        "url": url,
        "title": data["title"],
        "ingredients": data["ings"],
        "instructions": data["steps"],
        "cuisine": "Italian",
        "language": "it",  # flagged for batch translation after agent loop
        "image": data.get("image", ""),
    }


# ---------------------------------------------------------------------------
# Memorie di Angelina (memoriediangelina.com) — httpx, WPRM HTML, English
# ---------------------------------------------------------------------------

def search_memoriediangelina(query: str, max_results: int = 8) -> list[dict]:
    """Search memoriediangelina.com via WordPress site search."""
    try:
        r = httpx.get(
            f"https://memoriediangelina.com/?s={query.replace(' ', '+')}",
            headers=HEADERS, timeout=15, follow_redirects=True,
        )
        r.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not re.search(r"/\d{4}/\d{2}/\d{2}/", href):
            continue
        if href in seen:
            continue
        title = a.get_text(strip=True)
        if title and "memoriediangelina.com" in href:
            seen.add(href)
            results.append({"title": title, "url": href})
            if len(results) >= max_results:
                break
    return results


def _parse_wprm_ingredients(soup: BeautifulSoup) -> list[str]:
    """Extract WPRM ingredients, preserving group headers (e.g. 'Optional')."""
    ingredients = []

    def _parse_li(li) -> str | None:
        parts = []
        amt  = li.select_one(".wprm-recipe-ingredient-amount")
        unit = li.select_one(".wprm-recipe-ingredient-unit")
        name = li.select_one(".wprm-recipe-ingredient-name")
        note = li.select_one(".wprm-recipe-ingredient-notes")
        if amt:  parts.append(amt.text.strip())
        if unit: parts.append(unit.text.strip())
        if name: parts.append(name.text.strip())
        if note: parts.append(f"({note.text.strip()})")
        return " ".join(parts) if parts else None

    groups = soup.select(".wprm-recipe-ingredient-group")
    if groups:
        for group in groups:
            header = group.select_one(".wprm-recipe-ingredient-group-name")
            if header and header.text.strip():
                ingredients.append(f"[{header.text.strip()}]")
            for li in group.select(".wprm-recipe-ingredient"):
                line = _parse_li(li)
                if line:
                    ingredients.append(line)
    else:
        for li in soup.select(".wprm-recipe-ingredient"):
            line = _parse_li(li)
            if line:
                ingredients.append(line)

    return ingredients


def fetch_memoriediangelina(url: str) -> dict:
    """Fetch a memoriediangelina.com recipe using WPRM HTML selectors."""
    try:
        r = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        return {"error": str(e), "url": url}

    soup = BeautifulSoup(r.text, "html.parser")

    name_el = soup.select_one(".wprm-recipe-name")
    if not name_el:
        return {"error": "No WPRM recipe found on this page", "url": url}

    ingredients = _parse_wprm_ingredients(soup)

    instructions = [
        el.get_text(strip=True)
        for el in soup.select(".wprm-recipe-instruction-text")
        if el.get_text(strip=True)
    ]

    if not ingredients or not instructions:
        return {"error": "WPRM selectors returned empty content", "url": url}

    # Reject recipes that reference other recipes rather than standing alone
    combined = " ".join(instructions).lower()
    if _CROSS_REF_INSTRUCTIONS.search(combined):
        return {"error": "Recipe references other recipes and is not self-contained", "url": url}

    time_mins = soup.select_one(".wprm-recipe-total_time-minutes")
    time_str = f"{time_mins.text.strip()} minutes" if time_mins else ""
    servings = soup.select_one(".wprm-recipe-servings")
    og_image = soup.find("meta", property="og:image")

    return {
        "url": url,
        "title": name_el.get_text(strip=True),
        "ingredients": ingredients,
        "instructions": instructions,
        "time": time_str,
        "yield": servings.text.strip() if servings else "",
        "cuisine": "Italian",
        "image": og_image["content"] if og_image else "",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_to_human(iso: str) -> str:
    """Convert ISO 8601 duration (PT1H30M) to human string."""
    if not iso:
        return ""
    m = re.search(r"(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?", iso)
    if not m:
        return ""
    days, hours, mins = (int(x or 0) for x in m.groups())
    hours += days * 24
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins > 1 else ''}")
    return " ".join(parts)


def _translate_cucchiaio_batch(recipes: list[dict]) -> None:
    """Translate Italian title/ingredients/instructions to English via Haiku. Modifies in-place."""
    if not recipes:
        return

    blocks = []
    for i, r in enumerate(recipes):
        blocks.append(f"RECIPE {i + 1}: {r.get('title', '')}")
        blocks.append("Ingredients: " + " | ".join(r.get("ingredients", [])))
        blocks.append("Steps: " + " | ".join(r.get("instructions", [])))
        blocks.append("")

    prompt = (
        "Translate the following Italian recipes to English. "
        "Return a JSON array with one object per recipe in the same order:\n"
        '[{"title": "...", "ingredients": ["..."], "instructions": ["..."]}]\n\n'
        + "\n".join(blocks)
    )

    haiku = anthropic.Anthropic()
    resp = haiku.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = re.sub(r"```(?:json)?\s*", "", resp.content[0].text.strip()).strip().rstrip("`")
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        print("  [translation] Could not parse Haiku response — keeping Italian content")
        return

    translated = json.loads(m.group())
    for orig, trans in zip(recipes, translated):
        orig["title"] = trans.get("title", orig["title"])
        orig["ingredients"] = trans.get("ingredients", orig["ingredients"])
        orig["instructions"] = trans.get("instructions", orig["instructions"])
        orig.pop("language", None)


def _convert_measurements_batch(recipes: list[dict]) -> None:
    """Convert metric measurements to US equivalents via Haiku. Modifies in-place.

    Handles dual-unit strings (e.g. '200 g 7 oz') by stripping the metric prefix,
    and converts metric-only values (g→oz/lbs, ml→cups/tbsp/tsp, kg→lbs).
    """
    if not recipes:
        return

    metric_pat = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:g|ml|kg|l|cl)\b", re.IGNORECASE)
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
        "If it only has metric, convert it (g→oz or lbs, ml→cups/tbsp/tsp, kg→lbs). "
        "Group header lines like '[Optional]' must be kept exactly as-is. "
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


# ---------------------------------------------------------------------------
# Claude tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_giallozafferano",
        "description": (
            "Search giallozafferano.com for Italian recipes. Use English recipe names — "
            "the site is in English. Good for: pasta, risotto, pizza, soups, secondi, "
            "contorni, Italian desserts. Examples: 'carbonara', 'risotto porcini', "
            "'ossobuco', 'tiramisu', 'pasta e fagioli', 'saltimbocca', 'branzino'."
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
        "name": "fetch_giallozafferano",
        "description": "Fetch a recipe from a giallozafferano.com URL. Only fetch URLs returned by search_giallozafferano.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full giallozafferano.com recipe URL"}},
            "required": ["url"],
        },
    },
    {
        "name": "search_cucchiaio",
        "description": (
            "Search cucchiaio.it (Italy's #1 cooking site) by Italian keyword(s). "
            "IMPORTANT: Always use Italian food terms — the recipe URLs are in Italian. "
            "Good Italian terms: 'carbonara', 'amatriciana', 'ragù', 'polpette', 'branzino', "
            "'risotto', 'ossobuco', 'cotoletta', 'bistecca', 'baccalà', 'cacio pepe', "
            "'trippa', 'abbacchio', 'pollo cacciatore', 'melanzane', 'zucchine'. "
            "English terms will mostly not match — use the Italian dish name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Italian food term(s) to search (e.g. 'carbonara', 'pollo cacciatore')"},
                "max_results": {"type": "integer", "description": "Max results to return (default 8)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_cucchiaio",
        "description": "Fetch a recipe from a cucchiaio.it URL. Content will be translated from Italian to English automatically. Only fetch URLs returned by search_cucchiaio.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full cucchiaio.it recipe URL"}},
            "required": ["url"],
        },
    },
    {
        "name": "search_memoriediangelina",
        "description": (
            "Search memoriediangelina.com by Frank Fariello — Roman and Southern Italian home cooking in English. "
            "Excellent for: pasta e fagioli, cacio e pepe, amatriciana, bucatini, pasta al forno, "
            "saltimbocca, abbacchio, Roman offal, ribollita, braised meats, Southern Italian seafood, "
            "regional Italian dishes that aren't well-known outside Italy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (English or Italian dish name)"},
                "max_results": {"type": "integer", "description": "Max results to return (default 8)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_memoriediangelina",
        "description": "Fetch a recipe from a memoriediangelina.com URL. Only fetch URLs returned by search_memoriediangelina.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full memoriediangelina.com recipe URL"}},
            "required": ["url"],
        },
    },
]

SYSTEM = """You are an Italian recipe finder. Your job is to search all three sources and return the best results.

Sources:
- giallozafferano.com — largest Italian cooking site, English, use search_giallozafferano
- cucchiaio.it — Italy's most-used cooking site, Italian content (auto-translated), use search_cucchiaio with ITALIAN terms
- memoriediangelina.com — Frank Fariello's Roman and Southern Italian site, English, use search_memoriediangelina

Routing:
- Always search all 3 sources for each request.
- For cucchiaio.it: use the Italian name of the dish or Italian ingredient terms. English terms won't match.
- Fetch 2-3 promising results per source. Prefer weeknight-friendly recipes (≤60 min) unless the query is clearly a weekend dish.
- Skip results with errors or missing content.

Italian cuisine reference:
- Pasta: carbonara, amatriciana, cacio e pepe, arrabbiata, puttanesca, ragù bolognese, pasta e fagioli, pasta e ceci, bucatini, rigatoni all'amatriciana, pasta al forno, lasagna, gnocchi
- Meat: saltimbocca, ossobuco, abbacchio, polpette, pollo cacciatore, cotoletta, bistecca, involtini, braciole, spezzatino, scaloppine, arrosto
- Fish: branzino, baccalà, triglie, tonno, sarde, spigola, fritto misto, acqua pazza
- Vegetarian: parmigiana di melanzane, ribollita, minestrone, caponata, peperonata, frittata, risotto
- Roman: cacio e pepe, carbonara, amatriciana, gricia, pasta al tonno, fiori di zucca, carciofi alla romana, saltimbocca, abbacchio a scottadito

Rules:
- Only fetch URLs returned by the search tools. Do not construct or guess URLs.
- Aim for 6-8 valid recipes total.
- At the end, print a brief summary of what you found."""


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent(user_request: str) -> list[dict]:
    global _pw_page
    messages = [{"role": "user", "content": user_request}]
    print(f"\nSearching: {user_request}\n")

    found_recipes: list[dict] = []
    cucchiaio_results: list[dict] = []       # track for batch translation
    memoriediangelina_results: list[dict] = []  # track for measurement conversion

    def _run_loop() -> None:
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

                if name == "search_giallozafferano":
                    print(f"  [GialloZafferano] Searching: {inp['query']!r}")
                    result = search_giallozafferano(inp["query"], inp.get("max_results", 8))
                    print(f"  Found {len(result)} result(s)")

                elif name == "search_cucchiaio":
                    print(f"  [Cucchiaio d'Argento] Searching: {inp['query']!r}")
                    result = search_cucchiaio(inp["query"], inp.get("max_results", 8))
                    print(f"  Found {len(result)} result(s)")

                elif name == "search_memoriediangelina":
                    print(f"  [Memorie di Angelina] Searching: {inp['query']!r}")
                    result = search_memoriediangelina(inp["query"], inp.get("max_results", 8))
                    print(f"  Found {len(result)} result(s)")

                elif name == "fetch_giallozafferano":
                    print(f"  Fetching: {inp['url']}")
                    result = fetch_giallozafferano(inp["url"])
                    if "error" in result:
                        print(f"  Error: {result['error']}")
                    else:
                        result["time"] = _iso_to_human(result.pop("total_time", ""))
                        result["source"] = "GialloZafferano"
                        print(f"  Got: {result.get('title', '?')}" + (f" ({result['time']})" if result.get("time") else ""))
                        if result.get("ingredients") and result.get("instructions"):
                            found_recipes.append(result)

                elif name == "fetch_cucchiaio":
                    print(f"  Fetching: {inp['url']}")
                    result = fetch_cucchiaio(inp["url"])
                    if "error" in result:
                        print(f"  Error: {result['error']}")
                    else:
                        result["source"] = "Cucchiaio d'Argento"
                        print(f"  Got: {result.get('title', '?')} [IT — will translate]")
                        if result.get("ingredients") and result.get("instructions"):
                            found_recipes.append(result)
                            cucchiaio_results.append(result)

                elif name == "fetch_memoriediangelina":
                    print(f"  Fetching: {inp['url']}")
                    result = fetch_memoriediangelina(inp["url"])
                    if "error" in result:
                        print(f"  Error: {result['error']}")
                    else:
                        result["source"] = "Memorie di Angelina"
                        print(f"  Got: {result.get('title', '?')}" + (f" ({result.get('time', '')})" if result.get("time") else ""))
                        if result.get("ingredients") and result.get("instructions"):
                            found_recipes.append(result)
                            memoriediangelina_results.append(result)

                else:
                    result = {"error": f"Unknown tool: {name}"}

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

            messages.append({"role": "user", "content": tool_results})

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        _pw_page = browser.new_page()
        try:
            _run_loop()
        finally:
            browser.close()
            _pw_page = None

    # Translate cucchiaio.it content from Italian to English, then normalize measurements
    if cucchiaio_results:
        print(f"\nTranslating {len(cucchiaio_results)} Cucchiaio d'Argento recipe(s) via Haiku...")
        _translate_cucchiaio_batch(cucchiaio_results)
        _convert_measurements_batch(cucchiaio_results)

    # Convert metric measurements in Memorie di Angelina recipes
    if memoriediangelina_results:
        print(f"Converting measurements in {len(memoriediangelina_results)} Memorie di Angelina recipe(s)...")
        _convert_measurements_batch(memoriediangelina_results)

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
