#!/usr/bin/env python3
"""
backfill_idea_recipes.py -- Fetch missing ingredients/instructions for status="idea" entries.

Iterates over all idea entries in recipe_metadata.json that lack ingredients_raw
or instructions, fetches them from their source URLs using the existing agent
fetch_recipe() functions, and writes the results back.

Entries that can't be fetched (ATK/NYT paywall, no URL) are flagged in the output
but left unchanged.

Usage:
  python3 backfill_idea_recipes.py           # fetch and write
  python3 backfill_idea_recipes.py --dry-run # show what would be done, no write
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"
MENUBUILDER = Path(__file__).parent
sys.path.insert(0, str(MENUBUILDER))

# Sites we can't fetch without auth
BLOCKED_DOMAINS = {
    "www.americastestkitchen.com",
    "cooking.nytimes.com",
}

# Domain → agent module name
DOMAIN_AGENT = {
    "patijinich.com":              "mexican_agent",
    "www.rickbayless.com":         "mexican_agent",
    "thewoksoflife.com":           "asian_agent",
    "www.justonecookbook.com":     "asian_agent",
    "www.maangchi.com":            "asian_agent",
    "smittenkitchen.com":          "chef_agent",
    "chetnamakan.co.uk":           "chef_agent",
    "www.chetnamakan.co.uk":       "chef_agent",  # alias
    "www.indianhealthyrecipes.com":"indian_agent",
    "www.seriouseats.com":         "sites_agent",
    "www.washingtontimes.com":     None,  # generic ld+json
}

_agent_cache = {}

def _get_fetch_fn(domain: str):
    """Return the fetch_recipe function for a domain, or None if blocked/unknown."""
    if domain in BLOCKED_DOMAINS:
        return None
    agent_name = DOMAIN_AGENT.get(domain)
    if agent_name is None:
        # Try generic ld+json via a simple import fallback
        return _generic_fetch
    if agent_name not in _agent_cache:
        mod = __import__(agent_name)
        _agent_cache[agent_name] = mod.fetch_recipe
    return _agent_cache[agent_name]


def _generic_fetch(url: str) -> dict:
    """Generic ld+json fetch for sites without a dedicated agent."""
    import httpx
    from bs4 import BeautifulSoup
    import html as html_mod

    HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
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
            ingredients = item.get("recipeIngredient", [])
            instructions = []
            for step in item.get("recipeInstructions", []):
                if isinstance(step, dict):
                    if step.get("@type") == "HowToSection":
                        for sub in step.get("itemListElement", []):
                            if isinstance(sub, dict):
                                text = html_mod.unescape(sub.get("text", "").strip())
                                if text:
                                    instructions.append(text)
                    else:
                        text = html_mod.unescape(step.get("text", "").strip())
                        if text:
                            instructions.append(text)
                elif isinstance(step, str) and step.strip():
                    instructions.append(html_mod.unescape(step.strip()))
            if ingredients or instructions:
                return {
                    "url": url,
                    "title": item.get("name", "").strip(),
                    "ingredients": ingredients,
                    "instructions": instructions,
                }
    return {"error": "No ld+json Recipe found", "url": url}


def main():
    parser = argparse.ArgumentParser(description="Backfill ingredients/instructions for idea entries.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated, no write")
    args = parser.parse_args()

    meta = json.loads(METADATA_PATH.read_text())
    recipes = meta["recipes"]

    ideas_missing = {
        k: v for k, v in recipes.items()
        if v.get("status") == "idea"
        and not v.get("ingredients_raw")
        and not v.get("instructions")
    }

    print(f"Ideas missing content: {len(ideas_missing)}")
    print()

    updated = 0
    skipped_blocked = []
    skipped_no_url = []
    skipped_fetch_fail = []

    for name, entry in ideas_missing.items():
        url = entry.get("url") or entry.get("source_url") or ""
        source = entry.get("source", "")

        if not url:
            print(f"  SKIP (no URL): {name!r}  source={source!r}")
            skipped_no_url.append(name)
            continue

        domain = urlparse(url).netloc
        if domain in BLOCKED_DOMAINS:
            print(f"  SKIP (paywall): {name!r}  [{domain}]")
            skipped_blocked.append(name)
            continue

        fetch_fn = _get_fetch_fn(domain)
        print(f"  Fetching: {name!r}")
        print(f"    URL: {url}")

        try:
            result = fetch_fn(url)
        except Exception as e:
            print(f"    ERROR: {e}")
            skipped_fetch_fail.append(name)
            continue

        if "error" in result:
            print(f"    FAIL: {result['error']}")
            skipped_fetch_fail.append(name)
            continue

        ingr = result.get("ingredients", [])
        instr = result.get("instructions", [])

        if not ingr and not instr:
            print("    FAIL: got empty ingredients and instructions")
            skipped_fetch_fail.append(name)
            continue

        print(f"    OK: {len(ingr)} ingredients, {len(instr)} steps")

        if not args.dry_run:
            recipes[name]["ingredients_raw"] = ingr
            recipes[name]["instructions"] = instr

        updated += 1

    print()
    print("=" * 60)
    print(f"Would update:      {updated}" if args.dry_run else f"Updated:           {updated}")
    print(f"Skipped (paywall): {len(skipped_blocked)}  — {skipped_blocked}")
    print(f"Skipped (no URL):  {len(skipped_no_url)}  — {skipped_no_url}")
    print(f"Fetch failed:      {len(skipped_fetch_fail)}  — {skipped_fetch_fail}")

    if not args.dry_run and updated > 0:
        METADATA_PATH.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote {updated} updated entries to {METADATA_PATH}")


if __name__ == "__main__":
    main()
