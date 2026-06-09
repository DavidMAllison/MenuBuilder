#!/usr/bin/env python3
"""
atk_agent.py -- Sync ATK favorite collections into recipe_metadata.json.

Pull priority:
  1. Named collections from config.json atk.collection_name
     ("Try Out", "Sunday Dinner", "Dinners")
  2. User's top-rated recipes (fallback when collections yield < target new)

Auth: Playwright login on first run (or when cookies expire) → cookies cached
      in config.json. httpx + cookies for all API and recipe page fetches.
      Cookies refreshed after 20 hours.

Usage:
  python3 atk_agent.py                   # sync, add up to 5 new recipes
  python3 atk_agent.py --target 10       # add up to 10
  python3 atk_agent.py --dry-run         # preview without writing
  python3 atk_agent.py --force-login     # re-login even if cookies are fresh
  python3 atk_agent.py --collection "Try Out"  # single collection only
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import anthropic

PROJECT_ROOT = Path(__file__).parent
CONFIG_PATH  = PROJECT_ROOT / "config.json"
ATK_BASE     = "https://www.americastestkitchen.com"
COOKIE_TTL_H = 20   # hours before requiring Playwright re-login
BATCH_SIZE   = 6    # recipes per Haiku call


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

def _save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _cookies_fresh(cfg):
    atk = cfg.get("atk", {})
    fetched = atk.get("cookies_fetched_at", "")
    if not fetched or not atk.get("cookies"):
        return False
    try:
        dt = datetime.fromisoformat(fetched)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return age_h < COOKIE_TTL_H
    except Exception:
        return False


def _login_playwright(cfg):
    """Playwright login → save cookies to config → return cookie dict."""
    from playwright.sync_api import sync_playwright

    atk = cfg["atk"]
    email, password = atk["email"], atk["password"]
    print("  [auth] Opening browser for ATK login...")

    cookies = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        page.goto(f"{ATK_BASE}/sign_in")

        # Dismiss cookie consent banner
        try:
            page.get_by_role("button", name="Close this dialog").click(timeout=3000)
        except Exception:
            pass

        page.get_by_role("textbox", name="Email Address").fill(email)
        page.get_by_role("textbox", name="Password").fill(password)
        page.get_by_test_id("Button-primary").click()

        # Wait for redirect away from /sign_in
        page.wait_for_url(lambda url: "sign_in" not in url, timeout=12000)

        # Capture auth cookies
        for c in ctx.cookies():
            if c["name"] in ("user_token", "refresh_token", "atk_anonymous_user_token", "anonymous"):
                cookies[c["name"]] = c["value"]

        browser.close()

    if not cookies.get("user_token"):
        raise RuntimeError("ATK login failed: no user_token in cookies after login")

    cfg["atk"]["cookies"] = cookies
    cfg["atk"]["cookies_fetched_at"] = datetime.now(timezone.utc).isoformat()
    _save_config(cfg)
    print("  [auth] Login successful, cookies cached.")
    return cookies


def _ensure_auth(cfg, force=False):
    """Return (cfg, cookie_dict). Re-logins via Playwright if needed."""
    if not force and _cookies_fresh(cfg):
        return cfg, cfg["atk"]["cookies"]
    print("  [auth] Cookies missing or expired — logging in via Playwright...")
    cookies = _login_playwright(cfg)
    return cfg, cookies


def _make_client(cookies):
    """Return an httpx.Client pre-loaded with ATK session cookies."""
    return httpx.Client(
        base_url=ATK_BASE,
        cookies=cookies,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        timeout=20,
        follow_redirects=True,
    )


def _verify_auth(http):
    """Quick check that cookies are valid. Returns True if authenticated."""
    try:
        resp = http.get("/api/v6/user_favorites_meta_data?site_key=atk")
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Collection / top-rated fetching
# ---------------------------------------------------------------------------

def _collection_slug(name):
    return name.lower().strip().replace(" ", "-")


def _get_collection(http, slug):
    """Fetch all recipe entries from one collection. Returns [{title, url}]."""
    results = []
    page = 1
    while True:
        resp = http.get(f"/api/v6/favorite_collections?collection_slug={slug}&page={page}&site_key=atk")
        if resp.status_code != 200:
            print(f"  [!] Collection {slug!r} returned {resp.status_code}")
            break
        data = resp.json()
        for item in data.get("results", []):
            if item.get("document_klass") == "recipe" and item.get("document_url"):
                results.append({
                    "title": item["document_title"].strip(),
                    "url": ATK_BASE + item["document_url"],
                })
        if data.get("pagination", {}).get("last_page", True):
            break
        page += 1
    return results


def _get_top_rated(http):
    """Fetch user's top-rated ATK recipes. Returns [{title, url}]."""
    try:
        resp = http.get("/api/v6/user_favorites/top_rated")
        if resp.status_code != 200:
            return []
        data = resp.json().get("data", {})
        results = []
        for item in data.get("results", []):
            link = item.get("links", {}).get("self", "")
            if link:
                results.append({
                    "title": item["title"].strip(),
                    "url": ATK_BASE + "/" + link.lstrip("/"),
                })
        return results
    except Exception as e:
        print(f"  [!] top_rated fetch error: {e}")
        return []


# ---------------------------------------------------------------------------
# Recipe page parsing
# ---------------------------------------------------------------------------

def _parse_iso_duration(iso):
    """PT3H30M → ('3 hours 30 minutes', 210). Returns ('', 0) if unparseable."""
    if not iso:
        return "", 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", iso)
    if not m:
        return "", 0
    h    = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    total = h * 60 + mins
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h > 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins > 1 else ''}")
    return " ".join(parts), total


def _infer_method(title, keywords, instructions):
    text = " ".join([title] + keywords + instructions[:2]).lower()
    if any(w in text for w in ("grill", "bbq", "barbecue", "charcoal", "smoker")):
        return "grill"
    if any(w in text for w in ("slow cooker", "slow-cooker", "crockpot")):
        return "slow_cooker"
    if any(w in text for w in ("instant pot", "pressure cook", "multicooker")):
        return "multi"
    if any(w in text for w in ("roast", "bake", "baked", "oven", "braise", "braised")):
        return "oven"
    return "stovetop"


def _fetch_recipe(http, url):
    """
    Fetch an ATK recipe page and extract structured data via ld+json.
    Returns a dict or None if extraction fails.
    """
    try:
        resp = http.get(url)
        if resp.status_code != 200:
            print(f"  [!] {resp.status_code} fetching {url}")
            return None
    except Exception as e:
        print(f"  [!] Fetch error {url}: {e}")
        return None

    html = resp.text
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    ):
        try:
            data = json.loads(match.group(1))
        except Exception:
            continue
        candidates = data if isinstance(data, list) else data.get("@graph", [data])
        for item in candidates:
            if not isinstance(item, dict):
                continue
            t = item.get("@type", "")
            types = t if isinstance(t, list) else [t]
            if "Recipe" not in types:
                continue

            # Instructions
            instructions = []
            for step in item.get("recipeInstructions", []):
                if isinstance(step, dict):
                    text = step.get("text", "").strip()
                    if text:
                        instructions.append(text)
                elif isinstance(step, str) and step.strip():
                    instructions.append(step.strip())

            # Ingredients (raw strings)
            ingredients_raw = [s.strip() for s in item.get("recipeIngredient", []) if s.strip()]

            # Time
            time_str, total_min = _parse_iso_duration(
                item.get("totalTime") or item.get("cookTime", "")
            )

            # Servings — recipeYield can be [4, "Serves 4"] or "4 servings" or int
            yield_val = item.get("recipeYield", "")
            if isinstance(yield_val, list):
                yield_val = next((v for v in yield_val if isinstance(v, str)), "")
            servings = str(yield_val).strip()

            # Keywords for method inference
            keywords = re.findall(r'\w+', item.get("keywords", ""))

            return {
                "title":           item.get("name", "").strip(),
                "url":             url,
                "ingredients_raw": ingredients_raw,
                "instructions":    instructions,
                "cook_time":       time_str,
                "total_min":       total_min,
                "servings":        servings,
                "keywords":        keywords,
            }
    return None


# ---------------------------------------------------------------------------
# Haiku classification
# ---------------------------------------------------------------------------

_CLASSIFY_PROMPT = """\
Classify each recipe. Family context: managing cholesterol + blood pressure.

Health levels:
- Heart-Healthy: lean protein, vegetables, low sat fat, DASH-friendly
- Moderate: occasional, moderate fat/sodium, can be modified
- Indulgent: rich, high sat fat or sodium, special occasion

meal_type: "weeknight" if total cook time <= 60 min, else "weekend"

cuisine: the primary cuisine (American, Italian, Mexican, Thai, Indian,
         Chinese, Korean, Japanese, Mediterranean, French, etc.)

Recipes:
{recipes}

Return ONLY a JSON array, no commentary:
[{{"title": "...", "health": "...", "cuisine": "...", "meal_type": "..."}}]
"""

def _classify_batch(client, batch):
    """Returns {title: {health, cuisine, meal_type}}."""
    lines = []
    for r in batch:
        lines.append(
            f"Title: {r['title']}\n"
            f"Cook time: {r.get('cook_time', 'unknown')}, {r.get('total_min', 0)} min\n"
            f"Ingredients (first 8): {', '.join(r.get('ingredients_raw', [])[:8])}"
        )
    prompt = _CLASSIFY_PROMPT.format(recipes="\n\n".join(lines))
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            return {r["title"]: r for r in json.loads(m.group())}
    except Exception as e:
        print(f"  [!] Classification error: {e}")
    return {}


_INGREDIENTS_PROMPT = """\
Parse each recipe's ingredient list into structured JSON.

For each string extract:
- name: food item only, lowercase (e.g. "chicken thighs", "garlic")
- quantity: numeric amount as string (e.g. "1.5", "2"), or ""
- unit: measurement (e.g. "lbs", "cup", "tbsp"), or ""
- category: one of Proteins | Produce | Dairy | Pantry/Asian | Dry Goods | Spices/Herbs

Recipes:
{recipes}

Return ONLY a JSON array, no commentary:
[{{"title": "...", "ingredients": [{{"name":"...","quantity":"...","unit":"...","category":"..."}}]}}]
"""

def _safe_title(t):
    return (t.replace('“', "'").replace('”', "'")
             .replace('‘', "'").replace('’', "'")
             .replace('"', "'"))

def _parse_ingredients_batch(client, batch):
    """Returns {title: [structured_ingredient, ...]}."""
    safe_map = {_safe_title(r["title"]): r["title"] for r in batch}
    lines = []
    for r in batch:
        ing_lines = "\n".join(f"  - {i}" for i in r["ingredients_raw"])
        lines.append(f"Title: {_safe_title(r['title'])}\nIngredients:\n{ing_lines}")
    prompt = _INGREDIENTS_PROMPT.format(recipes="\n\n".join(lines))
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            results = json.loads(m.group())
            return {safe_map.get(r["title"], r["title"]): r["ingredients"] for r in results}
    except Exception as e:
        print(f"  [!] Ingredient parse error: {e}")
    return {}


# ---------------------------------------------------------------------------
# Quality check + markdown generation
# ---------------------------------------------------------------------------

def _quality_issues(recipe):
    issues = []
    if len(recipe.get("instructions", [])) < 2:
        issues.append("too few steps")
    if len(recipe.get("ingredients_raw", [])) < 3:
        issues.append("too few ingredients")
    if any(re.search(r"<[a-z]", s) for s in recipe.get("instructions", [])):
        issues.append("HTML artifacts in instructions")
    return issues


def _slug_filename(title):
    return re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_") + ".md"


def _build_md(title, recipe, needs_review):
    lines = []
    if needs_review:
        lines += ["> **NEEDS REVIEW**: Auto-generated — verify before cooking.", ""]
    lines += [f"# {title}", ""]
    if recipe.get("cook_time"):
        lines.append(f"**Time:** {recipe['cook_time']}")
    if recipe.get("servings"):
        lines.append(f"**Servings:** {recipe['servings']}")
    url = recipe.get("url", "")
    lines += ["", f"*Adapted from [America's Test Kitchen]({url})*", "", "## Ingredients", ""]
    for ing in recipe.get("ingredients_raw", []):
        lines.append(f"- {ing}")
    lines += ["", "## Instructions", ""]
    for i, step in enumerate(recipe.get("instructions", []), 1):
        lines.append(f"{i}. {step}")
    lines += ["", "## Notes", "", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Anthropic client helper
# ---------------------------------------------------------------------------

def _get_anthropic_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        env_path = Path.home() / "projects/personal/sms-assistant/.env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                    break
    return anthropic.Anthropic()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def sync_atk(target=5, dry_run=False, force_login=False, collection_filter=None):
    """
    Core sync logic. Can be called from CLI or imported by other scripts.
    Returns list of recipe titles added to metadata.
    """
    cfg = _load_config()

    if "atk" not in cfg or not cfg["atk"].get("email"):
        raise RuntimeError("ATK credentials not configured in config.json")

    metadata_path = Path(cfg["metadata_path"])
    metadata      = json.loads(metadata_path.read_text(encoding="utf-8"))
    recipes       = metadata["recipes"]
    recipes_dir   = metadata_path.parent / "recipes"

    # Build dedup sets
    known_urls   = {e.get("source_url", "").rstrip("/") for e in recipes.values()}
    known_titles = {t.lower() for t in recipes}

    # Auth
    cfg, cookies = _ensure_auth(cfg, force=force_login)
    http = _make_client(cookies)

    if not _verify_auth(http):
        print("  [auth] Cookies invalid — forcing re-login...")
        cfg, cookies = _ensure_auth(cfg, force=True)
        http = _make_client(cookies)

    # Determine which collections to pull
    all_collections = cfg["atk"].get("collection_name", [])
    if collection_filter:
        collections = [c for c in all_collections if collection_filter.lower() in c.lower()]
        if not collections:
            print(f"No collection matching {collection_filter!r}. Available: {all_collections}")
            return []
    else:
        collections = all_collections

    # Gather candidates from collections
    candidates = []
    seen_urls  = set()

    print(f"\nFetching from {len(collections)} collection(s)...")
    for name in collections:
        slug  = _collection_slug(name)
        items = _get_collection(http, slug)
        new   = 0
        for item in items:
            url = item["url"].rstrip("/")
            if url not in seen_urls:
                seen_urls.add(url)
                candidates.append(item)
                new += 1
        print(f"  [{name}] {len(items)} recipes ({new} unique)")

    # Filter out already-known recipes
    new_candidates = [
        c for c in candidates
        if c["url"].rstrip("/") not in known_urls
        and c["title"].lower() not in known_titles
    ]
    print(f"  {len(candidates)} total across collections, {len(new_candidates)} not yet in metadata")

    # Top-rated fallback if needed
    if len(new_candidates) < target:
        needed = target - len(new_candidates)
        print(f"\nFetching top-rated fallback (need {needed} more)...")
        for item in _get_top_rated(http):
            url = item["url"].rstrip("/")
            if (url not in seen_urls
                    and url not in known_urls
                    and item["title"].lower() not in known_titles):
                seen_urls.add(url)
                new_candidates.append(item)
                print(f"  + {item['title']}")
                if len(new_candidates) >= target:
                    break

    to_process = new_candidates[:target]

    if not to_process:
        print("\nNo new recipes to import.")
        return []

    if dry_run:
        print(f"\n[DRY RUN] Would fetch and import up to {len(to_process)} recipe(s):")
        for c in to_process:
            print(f"  - {c['title']}")
        return []

    # Fetch full recipe pages
    print(f"\nFetching {len(to_process)} recipe page(s)...")
    fetched = []
    for item in to_process:
        print(f"  Fetching: {item['title']}")
        recipe = _fetch_recipe(http, item["url"])
        if not recipe or not recipe.get("ingredients_raw") or not recipe.get("instructions"):
            print(f"  [skip] Could not parse: {item['title']}")
            continue
        fetched.append(recipe)
        print(f"  OK: {len(recipe['ingredients_raw'])} ingredients, {len(recipe['instructions'])} steps")

    if not fetched:
        print("No recipes successfully fetched.")
        return []

    # Classify + parse ingredients via Haiku
    print(f"\nClassifying {len(fetched)} recipe(s) with Haiku...")
    ai_client         = _get_anthropic_client()
    classifications   = {}
    parsed_ingredients = {}

    for i in range(0, len(fetched), BATCH_SIZE):
        batch = fetched[i:i + BATCH_SIZE]
        classifications.update(_classify_batch(ai_client, batch))
        parsed_ingredients.update(_parse_ingredients_batch(ai_client, batch))

    # Write metadata + .md files
    added = []
    for recipe in fetched:
        title  = recipe["title"]
        cls    = classifications.get(title, {})
        struct = parsed_ingredients.get(title, [])
        issues = _quality_issues(recipe)
        needs_review = bool(issues)

        if issues:
            print(f"  [needs_review] {title}: {', '.join(issues)}")

        filename   = _slug_filename(title)
        total_min  = recipe.get("total_min", 0)
        meal_type  = cls.get("meal_type") or ("weekend" if total_min > 60 else "weeknight")
        method     = _infer_method(title, recipe.get("keywords", []), recipe.get("instructions", []))

        # Write .md
        md_path = recipes_dir / filename
        md_path.write_text(_build_md(title, recipe, needs_review), encoding="utf-8")

        # Metadata entry
        recipes[title] = {
            "filename":              filename,
            "source":                "America's Test Kitchen",
            "source_url":            recipe["url"],
            "cuisine_type":          cls.get("cuisine", "American"),
            "meal_type":             meal_type,
            "health_classification": cls.get("health", "Moderate"),
            "times_cooked":          0,
            "last_cooked_date":      None,
            "cooking_method":        method,
            "status":                "active",
            "ingredients_raw":       recipe["ingredients_raw"],
            "ingredients":           struct,
            "instructions":          recipe["instructions"],
            "cook_time":             recipe.get("cook_time", ""),
            "servings":              recipe.get("servings", ""),
            "needs_review":          needs_review,
            "prep_components":       [],
            "prep_notes":            "",
        }
        added.append(title)
        h = cls.get("health", "?")
        c = cls.get("cuisine", "?")
        t = recipe.get("cook_time", "")
        print(f"  ✓ {title} [{h}] [{c}]{' ' + t if t else ''}")

    if added:
        metadata_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nDone. {len(added)} recipe(s) added to metadata.")
        print(f"Remember to run generate_github_pages_data.py and push.")

    return added


def main():
    parser = argparse.ArgumentParser(description="Sync ATK favorites into recipe_metadata.json")
    parser.add_argument("--target",     type=int,  default=5,  help="Max new recipes to import (default 5)")
    parser.add_argument("--dry-run",    action="store_true",   help="Preview without writing")
    parser.add_argument("--force-login",action="store_true",   help="Re-login even if cookies are fresh")
    parser.add_argument("--collection", type=str,  default="", help="Restrict to one collection name")
    args = parser.parse_args()

    added = sync_atk(
        target=args.target,
        dry_run=args.dry_run,
        force_login=args.force_login,
        collection_filter=args.collection or None,
    )
    if added:
        print("\nAdded:")
        for t in added:
            print(f"  - {t}")


if __name__ == "__main__":
    main()
