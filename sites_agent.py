#!/usr/bin/env python3
"""
sites_agent.py -- Find recipes from cross-cuisine recipe sites.

Current sites:
  - Serious Eats (seriouseats.com)

Adding a new site: add one entry to SITES. No other code changes needed
for sites with standard ld+json schema. Add a custom parser only if the
site has no ld+json.

Usage:
  sites "find a braised short rib recipe"
  sites "find a weeknight pasta from Serious Eats"
  sites "find an Indian dish"

Specify a site by name to restrict to that source. Otherwise all sites
are searched. Results written to /tmp/sites_agent_results.json.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

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

RESULTS_PATH = Path(f"/tmp/sites_agent_results_{os.getuid()}.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# Site registry — add a new site here, no other code changes needed for
# standard ld+json sites.
#
# Fields:
#   name          display name shown in results and used to match user requests
#   domain        used to route fetch calls to the right method
#   search_url    {query} is replaced with the URL-encoded search term
#   access        "playwright" for sites that block httpx; "httpx" otherwise
#   result_filter URL substring that identifies recipe pages vs articles/guides
#   search_wait   seconds to wait after page load for JS-rendered results (playwright only)
# ---------------------------------------------------------------------------
SITES = [
    {
        "name": "Serious Eats",
        "domain": "seriouseats.com",
        "search_url": "https://www.seriouseats.com/search?q={query}",
        "access": "playwright",
        "result_filter": "-recipe",
        "search_wait": 3,
        # NOTE: appending ?print= gives a clean printer-friendly layout good for
        # manual copy-paste. Cloudflare still blocks both httpx and headless
        # Playwright (as of Jun 2026) — even the print URL. Use for manual only.
    },
]

# Shared Playwright page — set at the start of run_agent, used by all tool calls
_pw_page = None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _search_playwright(site: dict, query: str, max_results: int) -> List[dict]:
    global _pw_page
    search_url = site["search_url"].replace("{query}", query.replace(" ", "+"))
    try:
        _pw_page.goto(search_url)
        time.sleep(site.get("search_wait", 2))
        result_filter = site.get("result_filter", "")
        links = _pw_page.evaluate(f'''() => {{
            const main = document.querySelector('main') || document.body;
            return Array.from(main.querySelectorAll('a[href]'))
                .map(a => ({{ text: a.innerText.trim().slice(0, 120), href: a.href }}))
                .filter(l => l.href.includes('{result_filter}') && l.text)
                .filter((l, i, arr) => arr.findIndex(x => x.href === l.href) === i)
                .slice(0, {max_results * 2});
        }}''')
        results = []
        seen = set()
        for l in links:
            if l["href"] not in seen and l["text"]:
                seen.add(l["href"])
                results.append({"title": l["text"], "url": l["href"]})
                if len(results) >= max_results:
                    break
        return results
    except Exception as e:
        return [{"error": str(e)}]


def _search_httpx(site: dict, query: str, max_results: int) -> List[dict]:
    search_url = site["search_url"].replace("{query}", query.replace(" ", "+"))
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(search_url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]

    soup = BeautifulSoup(resp.text, "html.parser")
    result_filter = site.get("result_filter", "")
    results = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if result_filter and result_filter not in href:
            continue
        if href in seen:
            continue
        title = a.get_text(strip=True)
        if title and site["domain"] in href:
            seen.add(href)
            results.append({"title": title, "url": href})
            if len(results) >= max_results:
                break
    return results


def search_sites(query: str, site_name: Optional[str] = None, max_results: int = 8) -> List[dict]:
    """Search one site by name or all sites. Returns list of {title, url, site}."""
    targets = SITES
    if site_name:
        targets = [s for s in SITES if site_name.lower() in s["name"].lower()]
        if not targets:
            return [{"error": f"Unknown site: {site_name}. Available: {[s['name'] for s in SITES]}"}]

    all_results = []
    for site in targets:
        if site["access"] == "playwright":
            results = _search_playwright(site, query, max_results)
        else:
            results = _search_httpx(site, query, max_results)
        for r in results:
            r["site"] = site["name"]
        all_results.extend(results)
    return all_results


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

_LD_JSON_JS = '''() => {
    const scripts = document.querySelectorAll('script');
    for (const s of scripts) {
        if (s.type !== 'application/ld+json') continue;
        try {
            const d = JSON.parse(s.textContent);
            const candidates = Array.isArray(d) ? d : (d['@graph'] || [d]);
            for (const item of candidates) {
                const t = item['@type'];
                const types = Array.isArray(t) ? t : [t];
                if (types.includes('Recipe')) return {
                    title: item.name || '',
                    description: item.description || '',
                    prepTime: item.prepTime || '',
                    cookTime: item.cookTime || '',
                    totalTime: item.totalTime || '',
                    recipeYield: String(item.recipeYield || ''),
                    ingredients: item.recipeIngredient || [],
                    instructions: (item.recipeInstructions || []).map(s =>
                        typeof s === 'string' ? s : (s.text || '')
                    ),
                    cuisine: item.recipeCuisine || '',
                    category: item.recipeCategory || '',
                };
            }
        } catch(e) {}
    }
    return null;
}'''


def _fetch_playwright(url: str) -> dict:
    global _pw_page
    try:
        _pw_page.goto(url)
        _pw_page.wait_for_load_state("domcontentloaded")
        result = _pw_page.evaluate(_LD_JSON_JS)
        if result:
            result["url"] = url
            return result
        return {"error": "No ld+json Recipe schema found", "url": url}
    except Exception as e:
        return {"error": str(e), "url": url}


def _fetch_httpx(url: str) -> dict:
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
        candidates = data if isinstance(data, list) else data.get("@graph", [data])
        for item in candidates:
            if not isinstance(item, dict):
                continue
            t = item.get("@type", "")
            if "Recipe" not in (t if isinstance(t, list) else [t]):
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
                "prepTime": item.get("prepTime", ""),
                "cookTime": item.get("cookTime", ""),
                "totalTime": total_time,
                "recipeYield": str(item.get("recipeYield", "")),
                "ingredients": item.get("recipeIngredient", []),
                "instructions": instructions,
                "cuisine": item.get("recipeCuisine", ""),
                "category": item.get("recipeCategory", ""),
            }
    return {"error": "No ld+json Recipe schema found", "url": url}


def fetch_recipe(url: str) -> dict:
    """Fetch a recipe from a URL, routing to Playwright or httpx by domain."""
    site = next((s for s in SITES if s["domain"] in url), None)
    if site and site["access"] == "playwright":
        return _fetch_playwright(url)
    return _fetch_httpx(url)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_to_human(iso: str) -> str:
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


def _source_label(url: str, site_name: str = "") -> str:
    name = site_name or next((s["name"] for s in SITES if s["domain"] in url), "")
    return f"{name} - {url}" if name else url


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_site_list = "\n".join(f"- {s['name']} ({s['domain']})" for s in SITES)

TOOLS = [
    {
        "name": "search_sites",
        "description": (
            "Search one or all recipe sites for recipes matching a query. "
            f"Available sites:\n{_site_list}\n"
            "Pass site_name to restrict to one source; omit to search all. "
            "Returns recipe titles and URLs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Dish name, ingredient, or technique to search for"},
                "site_name": {"type": "string", "description": "Restrict to this site name (optional)"},
                "max_results": {"type": "integer", "description": "Max results per site (default 8)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_recipe",
        "description": (
            "Fetch and parse a recipe from a URL returned by search_sites. "
            "Extracts title, ingredients, instructions, and timing. "
            "Only use URLs returned by search_sites — never construct or guess URLs."
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

SYSTEM = f"""You are a recipe finder covering high-quality cross-cuisine recipe sites. Your job is to search for recipes and return what you find — you do NOT save anything. The user decides what to keep.

Available sites:
{_site_list}

Rules:
- Only fetch URLs returned by search_sites. Never guess or construct URLs.
- If the user names a specific site, pass it as site_name to restrict the search. Otherwise search all sites.
- If the user asks for a specific cuisine, use your knowledge to search for dish names from that cuisine rather than the cuisine name itself. Example: for "Italian pasta" search for "cacio e pepe", "carbonara", "amatriciana", not "Italian pasta".
- Aim to find 3-5 valid recipes (with ingredients and instructions) per request. Skip results with errors or missing content.
- Note the site source and likely cuisine for each recipe in your final summary."""


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent(user_request: str) -> List[dict]:
    global _pw_page

    messages = [{"role": "user", "content": user_request}]
    print(f"\nSearching: {user_request}\n")

    found_recipes = []
    needs_playwright = any(s["access"] == "playwright" for s in SITES)

    def _run_loop():
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

                if name == "search_sites":
                    site_name = inp.get("site_name")
                    label = f"[{site_name}]" if site_name else "[all sites]"
                    print(f"  {label} Searching: {inp['query']!r}")
                    result = search_sites(inp["query"], site_name, inp.get("max_results", 8))
                    print(f"  Found {len(result)} result(s)")

                elif name == "fetch_recipe":
                    print(f"  Fetching: {inp['url']}")
                    result = fetch_recipe(inp["url"])
                    if "error" in result:
                        print(f"  Error: {result['error']}")
                    else:
                        time_str = _iso_to_human(result.get("totalTime", ""))
                        result["time"] = time_str
                        title = result.get("title", "unknown")
                        print(f"  Got: {title}" + (f" ({time_str})" if time_str else ""))
                        if result.get("ingredients") and result.get("instructions"):
                            result["source"] = _source_label(result["url"], result.get("site", ""))
                            found_recipes.append(result)

                else:
                    result = {"error": f"Unknown tool: {name}"}

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

            messages.append({"role": "user", "content": tool_results})

    if needs_playwright:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            _pw_page = browser.new_page()
            try:
                _run_loop()
            finally:
                browser.close()
                _pw_page = None
    else:
        _run_loop()

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
