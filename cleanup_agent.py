#!/usr/bin/env python3
"""
cleanup_agent.py — Recipe collection health check and repair agent.

Phases:
  Scan (default):         check everything, write cleanup_report.json, print summary
  --fix-safe:             apply images + instructions found during scan (no model)
  --fix-classify:         run Haiku on missing health/time, write cleanup_classify_preview.json
  --fix-classify --apply: write Haiku classifications to recipe_metadata.json

Individual check flags (scan phase, combine freely):
  --check-urls            HTTP status check on all source_urls
  --check-images          find missing og:image
  --check-instructions    try print-page fallbacks for missing instructions
  --check-semantic        flag stub/incomplete instructions, orphan .md files, needs_review entries
  (default: all checks)

Other flags:
  --dry-run               scan only; never write state or metadata
  --stale-days N          re-check state entries older than N days (default: 7)
  --limit N               cap HTTP requests at N (for testing)
  --workers N             parallel HTTP threads (default: 12)
"""

from __future__ import annotations

import argparse
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import httpx
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

MENUBUILDER_DIR  = Path(__file__).parent
METADATA_PATH    = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"
CONDIMENTS_PATH  = Path.home() / "Dropbox/LLMContext/cooking/condiments.json"
RECIPES_DIR      = Path.home() / "Dropbox/LLMContext/cooking/recipes"
STATE_PATH      = MENUBUILDER_DIR / "cleanup_state.json"
REPORT_PATH     = MENUBUILDER_DIR / "cleanup_report.json"
CLASSIFY_PATH   = MENUBUILDER_DIR / "cleanup_classify_preview.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# ---------------------------------------------------------------------------
# Cuisine / metadata constants
# ---------------------------------------------------------------------------

CUISINE_VARIANT_MAP = {
    "Italian-American":                              "Italian",
    "Chinese-American":                              "Chinese",
    "Vietnamese-American":                           "Vietnamese",
    "Chinese home-style":                            "Chinese",
    "Cantonese Chinese":                             "Chinese",
    "Chinese - Uyghur":                             "Chinese",
    "Moroccan / North African":                      "Moroccan",
    "Peruvian / Latin American":                     "Peruvian",
    "Middle Eastern / Levantine":                    "Middle Eastern",
    "French / European bistro":                      "French",
    "American / technique":                          "American",
    "Mexican (Baja California)":                     "Mexican",
    "Mexican (Central Mexico / mole-adjacent)":      "Mexican",
    "Mexican (Mexico City classic)":                 "Mexican",
    "Mexican (Northern coastal / Sonora)":           "Mexican",
    "Goan":                                          "Indian",
    "Goan Recipes":                                  "Indian",
}

_DOMAIN_CUISINE = {
    "seriouseats.com":           "American",
    "maangchi.com":              "Korean",
    "justonecookbook.com":       "Japanese",
    "hotthaikitchen.com":        "Thai",
    "vietworldkitchen.com":      "Vietnamese",
    "thewoksoflife.com":         "Chinese",
    "mexicoinmykitchen.com":     "Mexican",
    "patijinich.com":            "Mexican",
    "indianhealthyrecipes.com":  "Indian",
    "hebbarskitchen.com":        "Indian",
    "chetnamakan.co.uk":         "Indian",
    "kannammacooks.com":         "Indian",
    "ranveerbrar.com":           "Indian",
    "archanaskitchen.com":       "Indian",
    "olivetomato.com":           "Mediterranean",
    "themediterraneandish.com":  "Mediterranean",
    "smittenkitchen.com":        "American",
    "italianfoodforever.com":    "Italian",
    "skinnytaste.com":           "American",
    "recipetineats.com":         "American",
}

_DOMAIN_SOURCE = {
    "seriouseats.com":           "Serious Eats",
    "maangchi.com":              "Maangchi",
    "justonecookbook.com":       "Just One Cookbook",
    "hotthaikitchen.com":        "Hot Thai Kitchen",
    "vietworldkitchen.com":      "Viet World Kitchen",
    "thewoksoflife.com":         "Woks of Life",
    "mexicoinmykitchen.com":     "Mexico in My Kitchen",
    "patijinich.com":            "Pati Jinich",
    "indianhealthyrecipes.com":  "Indian Healthy Recipes",
    "hebbarskitchen.com":        "Hebbars Kitchen",
    "chetnamakan.co.uk":         "Chetna Makan",
    "kannammacooks.com":         "Kannamma Cooks",
    "ranveerbrar.com":           "Ranveer Brar",
    "archanaskitchen.com":       "Archana's Kitchen",
    "olivetomato.com":           "Olive Tomato",
    "themediterraneandish.com":  "The Mediterranean Dish",
    "smittenkitchen.com":        "Smitten Kitchen",
    "americastestkitchen.com":   "America's Test Kitchen",
    "cookscountry.com":          "Cook's Country",
    "italianfoodforever.com":    "Italian Food Forever",
    "skinnytaste.com":           "Skinnytaste",
    "recipetineats.com":         "RecipeTin Eats",
}


def _url_domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1).lower() if m else ""

# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------

def _load_api_key() -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    env_path = Path.home() / "projects/personal/sms-assistant/.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                return

# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"url_checks": {}, "image_attempts": {}, "instruction_attempts": {}}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _is_fresh(entry: dict, stale_days: int) -> bool:
    """Return True if the state entry was recorded within stale_days."""
    ts = entry.get("checked_at") or entry.get("tried_at", "")
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt < timedelta(days=stale_days)
    except Exception:
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

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
        return first if isinstance(first, str) else first.get("url", "")
    return ""


def _extract_instructions_ld(soup: BeautifulSoup) -> list[str]:
    """Parse JSON-LD Recipe schema for recipeInstructions."""
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
            types = t if isinstance(t, list) else [t]
            if not any("Recipe" in str(tp) for tp in types):
                continue
            steps = []
            for step in item.get("recipeInstructions", []):
                if isinstance(step, dict):
                    # HowToSection contains itemListElement
                    if step.get("@type") == "HowToSection":
                        for sub in step.get("itemListElement", []):
                            text = sub.get("text", "").strip() if isinstance(sub, dict) else str(sub).strip()
                            if text:
                                steps.append(text)
                    else:
                        text = step.get("text", "").strip()
                        if text:
                            steps.append(text)
                elif isinstance(step, str):
                    text = step.strip()
                    if text:
                        steps.append(text)
            if steps:
                return steps
    return []


def _extract_instructions_wprm(soup: BeautifulSoup) -> list[str]:
    """Fallback: WP Recipe Maker plugin instruction spans."""
    steps = []
    for el in soup.select(".wprm-recipe-instruction-text"):
        text = el.get_text(" ", strip=True)
        if text:
            steps.append(text)
    return steps


def _extract_time_ld(soup: BeautifulSoup) -> str:
    """Extract totalTime / cookTime from JSON-LD and convert to human string."""
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
            types = t if isinstance(t, list) else [t]
            if not any("Recipe" in str(tp) for tp in types):
                continue
            iso = item.get("totalTime") or item.get("cookTime", "")
            if iso:
                return _iso_to_human(iso)
    return ""


def _iso_to_human(iso: str) -> str:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", iso)
    if not m:
        return ""
    h, mins = int(m.group(1) or 0), int(m.group(2) or 0)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h > 1 else ''}")
    if mins:
        parts.append(f"{mins} min")
    return " ".join(parts)


def _fetch_page(url: str, timeout: int = 15) -> tuple[int, BeautifulSoup | None]:
    """Fetch a URL; return (status_code, soup). soup is None on error."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            soup = BeautifulSoup(resp.text, "html.parser")
            return resp.status_code, soup
    except Exception:
        return 0, None


def _print_page_variants(url: str) -> list[str]:
    """Return URL candidates to try for a print-friendly page."""
    base = url.rstrip("/")
    candidates = [
        f"{base}/print",
        f"{base}/print/",
        f"{base}?print=1",
        f"{base}?print=true",
        f"{base}?wprm-print=recipe",
        f"{base}?mode=print",
    ]
    # Remove duplicates while preserving order
    seen: set[str] = {url}
    return [u for u in candidates if u not in seen]

# ---------------------------------------------------------------------------
# Check: URL validation
# ---------------------------------------------------------------------------

def check_urls(
    active: list[tuple[str, dict]],
    state: dict,
    stale_days: int,
    workers: int,
    limit: int,
    dry_run: bool,
) -> list[dict]:
    """HTTP HEAD/GET all source_urls. Returns list of dead-url dicts."""
    url_state = state.setdefault("url_checks", {})

    to_check: list[tuple[str, dict]] = []
    for key, r in active:
        url = (r.get("source_url") or r.get("url") or "").strip()
        if not url:
            continue
        entry = url_state.get(key, {})
        if _is_fresh(entry, stale_days):
            continue
        to_check.append((key, r))

    if limit:
        to_check = to_check[:limit]

    print(f"  URL check: {len(to_check)} to fetch ({len(active) - len(to_check)} cached)")

    def _check(item: tuple[str, dict]) -> tuple[str, dict, int, str]:
        key, r = item
        url = (r.get("source_url") or r.get("url") or "").strip()
        try:
            with httpx.Client(timeout=12, follow_redirects=True) as http:
                resp = http.get(url, headers=HEADERS)
                return key, r, resp.status_code, str(resp.url)
        except Exception as e:
            return key, r, 0, str(e)

    results: list[tuple[str, dict, int, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_check, item): item for item in to_check}
        done = 0
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            if done % 20 == 0:
                print(f"    {done}/{len(to_check)} URLs checked...")

    if not dry_run:
        for key, r, status, final_url in results:
            url_state[key] = {"status": status, "checked_at": _now(), "final_url": final_url}

    dead = []
    for key, r, status, final_url in results:
        if status not in (200, 201, 301, 302, 303, 307, 308) or status == 0:
            dead.append({
                "key": key,
                "title": r.get("title", key),
                "url": (r.get("source_url") or r.get("url") or ""),
                "status": status,
                "detail": final_url,
            })
    # Also flag cached dead ones
    for key, r in active:
        if key in url_state and key not in {k for k, _, _, _ in results}:
            entry = url_state[key]
            status = entry.get("status", 0)
            if status not in (200, 201) and status != 0:
                dead.append({
                    "key": key,
                    "title": r.get("title", key),
                    "url": (r.get("source_url") or r.get("url") or ""),
                    "status": status,
                    "detail": entry.get("final_url", ""),
                    "cached": True,
                })
    return dead

# ---------------------------------------------------------------------------
# Check: missing images
# ---------------------------------------------------------------------------

def check_images(
    active: list[tuple[str, dict]],
    state: dict,
    stale_days: int,
    workers: int,
    limit: int,
    dry_run: bool,
) -> dict:
    """
    For recipes missing `image`, try og:image from source_url.
    Returns {"found": [...], "fetch_failed": [...], "no_source_url": [...]}.
    """
    img_state = state.setdefault("image_attempts", {})

    needs_image = [(k, r) for k, r in active if not r.get("image")]
    to_fetch: list[tuple[str, dict]] = []
    for key, r in needs_image:
        entry = img_state.get(key, {})
        if _is_fresh(entry, stale_days):
            continue
        url = (r.get("source_url") or r.get("url") or "").strip()
        if not url:
            continue
        to_fetch.append((key, r))

    if limit:
        to_fetch = to_fetch[:limit]

    print(f"  Image check: {len(to_fetch)} to fetch ({len(needs_image) - len(to_fetch)} cached or no URL)")

    def _fetch_image(item: tuple[str, dict]) -> tuple[str, dict, str]:
        key, r = item
        url = (r.get("source_url") or r.get("url") or "").strip()
        status, soup = _fetch_page(url)
        if soup:
            # Try JSON-LD image first, then og:image
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                except (json.JSONDecodeError, AttributeError):
                    continue
                candidates = data if isinstance(data, list) else data.get("@graph", [data])
                for item_data in candidates:
                    if not isinstance(item_data, dict):
                        continue
                    t = item_data.get("@type", "")
                    types = t if isinstance(t, list) else [t]
                    if any("Recipe" in str(tp) for tp in types):
                        img = _ld_image(item_data) or _og_image(soup)
                        if img:
                            return key, r, img
            img = _og_image(soup)
            if img:
                return key, r, img
        return key, r, ""

    raw_results: list[tuple[str, dict, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_image, item): item for item in to_fetch}
        done = 0
        for fut in as_completed(futures):
            raw_results.append(fut.result())
            done += 1
            if done % 20 == 0:
                print(f"    {done}/{len(to_fetch)} images fetched...")

    if not dry_run:
        for key, r, image in raw_results:
            url = (r.get("source_url") or r.get("url") or "").strip()
            img_state[key] = {"tried_at": _now(), "image": image, "source_url": url}

    found, fetch_failed, no_source = [], [], []

    # Incorporate cached results
    for key, r in needs_image:
        url = (r.get("source_url") or r.get("url") or "").strip()
        if not url:
            no_source.append({"key": key, "title": r.get("title", key)})
            continue
        # Find in fresh results
        res = next((x for x in raw_results if x[0] == key), None)
        if res:
            _, _, image = res
        else:
            entry = img_state.get(key, {})
            image = entry.get("image", "")
        if image:
            found.append({"key": key, "title": r.get("title", key), "image": image})
        else:
            fetch_failed.append({"key": key, "title": r.get("title", key), "source_url": url})

    return {"found": found, "fetch_failed": fetch_failed, "no_source_url": no_source}

# ---------------------------------------------------------------------------
# Check: missing instructions
# ---------------------------------------------------------------------------

def _is_stub_instructions(instructions) -> bool:
    """True if instructions are missing or too thin to be useful (< 3 steps with real content)."""
    if not instructions:
        return True
    if isinstance(instructions, list):
        meaningful = [s for s in instructions if isinstance(s, str) and len(s.strip()) > 20]
        return len(meaningful) < 3
    return False


def check_instructions(
    active: list[tuple[str, dict]],
    state: dict,
    stale_days: int,
    workers: int,
    limit: int,
    dry_run: bool,
) -> dict:
    """
    For active recipes with missing or stub instructions (<3 meaningful steps),
    try source URL + print-page variants.
    Returns {"found": [...], "failed": [...]}.
    """
    instr_state = state.setdefault("instruction_attempts", {})

    needs_instr = [(k, r) for k, r in active if _is_stub_instructions(r.get("instructions"))]
    to_try: list[tuple[str, dict]] = []
    for key, r in needs_instr:
        entry = instr_state.get(key, {})
        if _is_fresh(entry, stale_days) and not entry.get("instructions"):
            # Already tried recently, didn't find anything — skip
            continue
        if _is_fresh(entry, stale_days) and entry.get("instructions"):
            # Already found — skip re-fetch
            continue
        url = (r.get("source_url") or r.get("url") or "").strip()
        if not url:
            continue
        to_try.append((key, r))

    if limit:
        to_try = to_try[:limit]

    print(f"  Instruction check: {len(needs_instr)} missing, {len(to_try)} to attempt")

    def _try_fetch_instructions(item: tuple[str, dict]) -> tuple[str, dict, list[str], list[str]]:
        key, r = item
        base_url = (r.get("source_url") or r.get("url") or "").strip()
        urls_to_try = [base_url] + _print_page_variants(base_url)
        tried = []
        for url in urls_to_try:
            tried.append(url)
            status, soup = _fetch_page(url)
            if soup:
                steps = _extract_instructions_ld(soup)
                if not steps:
                    steps = _extract_instructions_wprm(soup)
                if steps:
                    return key, r, steps, tried
        return key, r, [], tried

    raw_results: list[tuple[str, dict, list[str], list[str]]] = []
    with ThreadPoolExecutor(max_workers=min(workers, 4)) as pool:  # gentler for instructions
        futures = {pool.submit(_try_fetch_instructions, item): item for item in to_try}
        for fut in as_completed(futures):
            raw_results.append(fut.result())

    if not dry_run:
        for key, r, steps, tried in raw_results:
            instr_state[key] = {
                "tried_at": _now(),
                "instructions": steps,
                "tried_urls": tried,
            }

    found, failed_no_url, failed_fetch = [], [], []

    for key, r in needs_instr:
        url = (r.get("source_url") or r.get("url") or "").strip()
        if not url:
            failed_no_url.append({"key": key, "title": r.get("title", key)})
            continue
        res = next((x for x in raw_results if x[0] == key), None)
        if res:
            _, _, steps, tried = res
        else:
            entry = instr_state.get(key, {})
            steps = entry.get("instructions", [])
            tried = entry.get("tried_urls", [])
        if steps:
            found.append({"key": key, "title": r.get("title", key), "instructions": steps})
        else:
            failed_fetch.append({"key": key, "title": r.get("title", key), "source_url": url, "tried": tried})

    return {"found": found, "failed_no_url": failed_no_url, "failed_fetch": failed_fetch}

# ---------------------------------------------------------------------------
# Check: semantic / structural issues
# ---------------------------------------------------------------------------

_STUB_PATTERNS = re.compile(
    r"(see full recipe|visit website|click here|get the recipe|view recipe|full recipe at)",
    re.IGNORECASE,
)

def check_semantic(
    active: list[tuple[str, dict]],
    recipes_dir: Path,
) -> dict:
    """
    Flag:
      - stub instructions (redirect-only, too short to be real)
      - needs_review entries
      - orphan .md files (file exists but no metadata entry)
      - metadata entries with filename set but .md missing (shouldn't happen, but check)
    """
    stub_instructions = []
    needs_review_entries = []
    orphan_md = []
    missing_md = []

    active_keys = {k for k, _ in active}
    active_filenames = {v.get("filename", "") for _, v in active if v.get("filename")}

    for key, r in active:
        # Stub instructions
        instr = r.get("instructions")
        if instr:
            if isinstance(instr, list):
                text = " ".join(instr)
            else:
                text = str(instr)
            if _STUB_PATTERNS.search(text):
                stub_instructions.append({"key": key, "title": r.get("title", key), "snippet": text[:120]})
            elif len(text) < 80:
                stub_instructions.append({"key": key, "title": r.get("title", key), "snippet": text[:120]})

        # needs_review flag
        if r.get("needs_review"):
            stub_instructions  # not stub, handled below
            needs_review_entries.append({
                "key": key,
                "title": r.get("title", key),
                "source": r.get("source", ""),
                "times_cooked": r.get("times_cooked", 0),
            })

        # .md file missing
        fn = r.get("filename", "")
        if fn and not (recipes_dir / fn).exists():
            missing_md.append({"key": key, "title": r.get("title", key), "filename": fn})

    # Orphan .md files
    if recipes_dir.exists():
        for md_file in recipes_dir.glob("*.md"):
            if md_file.name not in active_filenames:
                orphan_md.append(str(md_file.name))

    return {
        "stub_instructions": stub_instructions,
        "needs_review": needs_review_entries,
        "orphan_md": orphan_md,
        "missing_md": missing_md,
    }

# ---------------------------------------------------------------------------
# Fix: safe (no model)
# ---------------------------------------------------------------------------

def _rebuild_md_instructions(md_path: Path, steps: list[str]) -> None:
    """Replace the ## Instructions section in an existing .md file and strip any needs_review banner."""
    if not md_path.exists():
        return
    text = md_path.read_text(encoding="utf-8")
    # Strip needs_review banner if present
    text = re.sub(
        r'\n?> \*\*Needs Review\*\*.*?before first cook\.\n\n',
        '\n',
        text,
        flags=re.DOTALL,
    )
    # Replace Instructions section
    instr_block = "\n## Instructions\n\n"
    instr_block += "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
    instr_block += "\n"
    text = re.sub(r'\n## Instructions\n.*', instr_block, text, flags=re.DOTALL)
    md_path.write_text(text, encoding="utf-8")


def fix_safe(state: dict, dry_run: bool) -> None:
    """Apply cached images and instructions from state to recipe_metadata.json + regenerate .md."""
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    recipes = metadata["recipes"]

    img_state   = state.get("image_attempts", {})
    instr_state = state.get("instruction_attempts", {})

    images_applied       = 0
    instructions_applied = 0

    for key, r in recipes.items():
        if r.get("status") not in ("active",):
            continue

        # Image
        if not r.get("image") and key in img_state:
            image = img_state[key].get("image", "")
            if image:
                if not dry_run:
                    recipes[key]["image"] = image
                print(f"  [image] {r.get('title', key)[:55]}")
                images_applied += 1

        # Instructions — write to metadata AND regenerate .md
        if _is_stub_instructions(r.get("instructions")) and key in instr_state:
            steps = instr_state[key].get("instructions", [])
            if steps and not _is_stub_instructions(steps):
                if not dry_run:
                    recipes[key]["instructions"] = steps
                    fn = r.get("filename", "")
                    if fn:
                        _rebuild_md_instructions(RECIPES_DIR / fn, steps)
                print(f"  [instructions+md] {r.get('title', key)[:55]}")
                instructions_applied += 1

    prefix = "DRY RUN — " if dry_run else ""
    print(f"\n{prefix}Applied: {images_applied} images, {instructions_applied} instruction sets")

    if not dry_run and (images_applied or instructions_applied):
        METADATA_PATH.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved to {METADATA_PATH}")

# ---------------------------------------------------------------------------
# Fix: Haiku classify (health + time)
# ---------------------------------------------------------------------------

_HEALTH_PROMPT = """\
Classify each recipe as Heart-Healthy, Moderate, or Indulgent based on the recipe name and ingredients.
Reply with a JSON array only — no prose.

Definitions:
- Heart-Healthy: lean protein (chicken breast, fish, legumes), low saturated fat, vegetable-heavy
- Moderate: regular chicken or pork, some cream/cheese, restaurant-style but not excessive
- Indulgent: fried foods, red meat, heavy cream/butter sauces, high sodium, treat-meal territory

Recipes:
{recipes}

Reply format: [{{"index": 1, "health": "Heart-Healthy"}}]"""

_TIME_PROMPT = """\
Estimate total cook + prep time in minutes for each recipe based on its name, ingredients, and instructions snippet.
Reply with a JSON array only — no prose.

Guidelines:
- Simple stir-fry or sauté: 20-35 min
- One-pot weeknight meals: 30-45 min
- Braises, slow-cooked: 90-180 min
- Marinating time: count it if overnight would be unusual; skip "marinate up to X hours" padding
- Provide a single integer (minutes) — no ranges

Recipes:
{recipes}

Reply format: [{{"index": 1, "time_minutes": 35}}]"""


def fix_classify(apply: bool) -> None:
    """Run Haiku to fill missing health and time. Preview to file; write to metadata if --apply."""
    _load_api_key()
    client = anthropic.Anthropic()

    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    recipes  = metadata["recipes"]

    need_health = [(k, v) for k, v in recipes.items() if v.get("status") == "active" and not v.get("health")]
    need_time   = [(k, v) for k, v in recipes.items() if v.get("status") == "active" and not v.get("time")]

    print(f"  Missing health: {len(need_health)}")
    print(f"  Missing time:   {len(need_time)}")

    health_results: dict[str, str] = {}
    time_results:   dict[str, str] = {}

    BATCH = 8

    # Classify health
    if need_health:
        print("\nClassifying health...")
        for i in range(0, len(need_health), BATCH):
            batch = need_health[i:i + BATCH]
            # Use 1-based indices so titles with quotes don't break JSON parsing
            index_map: dict[int, str] = {}
            lines = []
            for j, (key, r) in enumerate(batch, 1):
                index_map[j] = key
                ingredients = r.get("ingredients", [])
                if ingredients and isinstance(ingredients[0], dict):
                    ingr_names = [x.get("name", "") for x in ingredients[:6]]
                else:
                    ingr_names = [str(x) for x in ingredients[:6]]
                ingr_preview = ", ".join(ingr_names) or (r.get("ingredients_raw", [""])[0] if r.get("ingredients_raw") else "")
                lines.append(f'{j}. {r.get("title", key)} | Ingredients: {ingr_preview}')
            try:
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1024,
                    messages=[{"role": "user", "content": _HEALTH_PROMPT.format(recipes="\n".join(lines))}],
                )
                text = resp.content[0].text.strip()
                m = re.search(r"\[.*\]", text, re.DOTALL)
                if m:
                    for item in json.loads(m.group()):
                        key = index_map.get(item.get("index", -1))
                        if key:
                            health_results[key] = item["health"]
            except Exception as e:
                print(f"  [!] Health batch error: {e}")

    # Classify time
    if need_time:
        print("Classifying time...")
        for i in range(0, len(need_time), BATCH):
            batch = need_time[i:i + BATCH]
            index_map = {}
            lines = []
            for j, (key, r) in enumerate(batch, 1):
                index_map[j] = key
                instr = r.get("instructions", [])
                snippet = ""
                if isinstance(instr, list) and instr:
                    snippet = " → ".join(str(s) for s in instr[:3])[:200]
                elif isinstance(instr, str):
                    snippet = instr[:200]
                lines.append(f'{j}. {r.get("title", key)} | Method: {r.get("cooking_method","?")} | Instructions: {snippet}')
            try:
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1024,
                    messages=[{"role": "user", "content": _TIME_PROMPT.format(recipes="\n".join(lines))}],
                )
                text = resp.content[0].text.strip()
                m = re.search(r"\[.*\]", text, re.DOTALL)
                if m:
                    for item in json.loads(m.group()):
                        key = index_map.get(item.get("index", -1))
                        mins = item.get("time_minutes")
                        if key and mins:
                            h, mn = divmod(int(mins), 60)
                            if h and mn:
                                label = f"{h} hour{'s' if h > 1 else ''} {mn} min"
                            elif h:
                                label = f"{h} hour{'s' if h > 1 else ''}"
                            else:
                                label = f"{mn} min"
                            time_results[key] = label
            except Exception as e:
                print(f"  [!] Time batch error: {e}")

    # Build preview (keyed by recipe key, not title)
    preview: list[dict] = []
    for key, r in need_health:
        preview.append({
            "key": key, "title": r.get("title", key), "field": "health",
            "value": health_results.get(key, "NOT_CLASSIFIED"),
        })
    for key, r in need_time:
        preview.append({
            "key": key, "title": r.get("title", key), "field": "time",
            "value": time_results.get(key, "NOT_CLASSIFIED"),
        })

    CLASSIFY_PATH.write_text(json.dumps(preview, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nPreview written to {CLASSIFY_PATH}")
    print(f"  Health classified: {len(health_results)}/{len(need_health)}")
    print(f"  Time classified:   {len(time_results)}/{len(need_time)}")

    # Print table
    print("\n--- PREVIEW (first 30) ---")
    for row in preview[:30]:
        status = "" if row["value"] not in ("NOT_CLASSIFIED",) else " [!]"
        print(f"  {row['field']:<8} {row['title'][:50]:<50}  {row['value']}{status}")
    if len(preview) > 30:
        print(f"  ... and {len(preview) - 30} more in {CLASSIFY_PATH}")

    if apply:
        applied = 0
        for row in preview:
            if row["value"] == "NOT_CLASSIFIED":
                continue
            if row["field"] == "health":
                recipes[row["key"]]["health"] = row["value"]
            elif row["field"] == "time":
                recipes[row["key"]]["time"] = row["value"]
            applied += 1
        METADATA_PATH.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nApplied {applied} classifications to {METADATA_PATH}")
    else:
        print("\nRun with --fix-classify --apply to write these to recipe_metadata.json")

# ---------------------------------------------------------------------------
# Fix: metadata (cuisine, source, meal_type, needs_review) — no HTTP, no model
# ---------------------------------------------------------------------------

def fix_metadata(dry_run: bool) -> None:
    """Auto-fix cuisine normalization, source inference, meal_type mismatches, needs_review clearing."""
    config    = json.loads((MENUBUILDER_DIR / "config.json").read_text(encoding="utf-8"))
    canonical = set(config.get("cuisine_family_map", {}).keys())

    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    recipes  = metadata["recipes"]

    cuisine_fixed        = 0
    source_fixed         = 0
    meal_type_fixed      = 0
    needs_review_cleared = 0

    for key, r in recipes.items():
        if r.get("status") not in ("active",):
            continue

        url    = (r.get("source_url") or r.get("url") or "").strip()
        domain = _url_domain(url) if url else ""

        # 1. Cuisine normalization — variant → canonical
        cuisine = r.get("cuisine", "")
        if cuisine and cuisine not in canonical:
            normalized = CUISINE_VARIANT_MAP.get(cuisine)
            if normalized:
                if not dry_run:
                    recipes[key]["cuisine"] = normalized
                print(f"  [cuisine] {r.get('title', key)[:55]}: {cuisine!r} → {normalized!r}")
                cuisine_fixed += 1
                cuisine = normalized

        # 2. Missing cuisine — infer from source domain
        if not cuisine and domain:
            inferred = _DOMAIN_CUISINE.get(domain)
            if inferred:
                if not dry_run:
                    recipes[key]["cuisine"] = inferred
                print(f"  [cuisine-infer] {r.get('title', key)[:50]}: → {inferred!r} ({domain})")
                cuisine_fixed += 1

        # 3. Missing source — infer from domain
        if not r.get("source") and domain:
            inferred_src = _DOMAIN_SOURCE.get(domain)
            if inferred_src:
                if not dry_run:
                    recipes[key]["source"] = inferred_src
                print(f"  [source-infer] {r.get('title', key)[:50]}: → {inferred_src!r}")
                source_fixed += 1

        # 4. meal_type mismatch — time > 60 min tagged Weeknight
        # Skip slow_cooker (hands-off, weeknight-safe by design)
        if r.get("meal_type") == "Weeknight" and r.get("cooking_method") != "slow_cooker":
            time_str = r.get("time", "")
            # Strip passive time (marinating, resting) so "20 min active + 4 hr marinating"
            # doesn't incorrectly flip to Weekend
            active_time = re.sub(
                r"\+?\s*\d+\s*(?:hours?|h|minutes?|min)\s*(?:marinating|marinate|resting|rest|chilling|overnight)",
                "", time_str, flags=re.IGNORECASE,
            ).strip()
            mins = 0
            mh = re.search(r"(\d+)\s*hour", active_time, re.IGNORECASE)
            if mh:
                mins = int(mh.group(1)) * 60
            mm = re.search(r"(\d+)\s*min", active_time, re.IGNORECASE)
            if mm:
                mins += int(mm.group(1))
            if mins > 60:
                if not dry_run:
                    recipes[key]["meal_type"] = "Weekend"
                print(f"  [meal_type] {r.get('title', key)[:50]}: Weeknight → Weekend ({time_str})")
                meal_type_fixed += 1

        # 5. needs_review — clear if instructions pass quality check
        if r.get("needs_review"):
            if not _is_stub_instructions(r.get("instructions")):
                if not dry_run:
                    recipes[key]["needs_review"] = False
                print(f"  [needs_review] cleared: {r.get('title', key)[:55]}")
                needs_review_cleared += 1

    prefix = "DRY RUN — " if dry_run else ""
    print(
        f"\n{prefix}Metadata fixes: {cuisine_fixed} cuisine, {source_fixed} source, "
        f"{meal_type_fixed} meal_type, {needs_review_cleared} needs_review cleared"
    )

    if not dry_run and (cuisine_fixed or source_fixed or meal_type_fixed or needs_review_cleared):
        METADATA_PATH.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved to {METADATA_PATH}")


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def run_scan(args: argparse.Namespace) -> None:
    state = _load_state()

    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    recipes  = metadata["recipes"]

    active = [(k, v) for k, v in recipes.items() if v.get("status") == "active"]
    print(f"Scanning {len(active)} active recipes...\n")

    run_all = not (args.check_urls or args.check_images or args.check_instructions or args.check_semantic)

    report: dict = {
        "generated_at": _now(),
        "active_count": len(active),
    }

    # URL check
    if run_all or args.check_urls:
        print("[1/4] Checking source URLs...")
        dead_urls = check_urls(active, state, args.stale_days, args.workers, args.limit, args.dry_run)
        report["dead_urls"] = dead_urls
        print(f"  => {len(dead_urls)} dead/unreachable URLs\n")

    # Image check
    if run_all or args.check_images:
        print("[2/4] Checking images...")
        image_results = check_images(active, state, args.stale_days, args.workers, args.limit, args.dry_run)
        report["images"] = image_results
        print(f"  => found: {len(image_results['found'])}  "
              f"fetch_failed: {len(image_results['fetch_failed'])}  "
              f"no_source_url: {len(image_results['no_source_url'])}\n")

    # Instruction check
    if run_all or args.check_instructions:
        print("[3/4] Checking instructions...")
        instr_results = check_instructions(active, state, args.stale_days, args.workers, args.limit, args.dry_run)
        report["instructions"] = instr_results
        found_n  = len(instr_results["found"])
        failed_n = len(instr_results["failed_no_url"]) + len(instr_results["failed_fetch"])
        print(f"  => found: {found_n}  unresolvable: {failed_n}\n")

    # Semantic check
    if run_all or args.check_semantic:
        print("[4/4] Semantic / structural check...")
        sem = check_semantic(active, RECIPES_DIR)
        report["semantic"] = sem
        print(f"  => stub instructions: {len(sem['stub_instructions'])}  "
              f"needs_review: {len(sem['needs_review'])}  "
              f"orphan .md: {len(sem['orphan_md'])}  "
              f"missing .md: {len(sem['missing_md'])}\n")

    # Missing health/time (informational — classified via --fix-classify)
    no_health  = [(k, v) for k, v in active if not v.get("health")]
    no_time    = [(k, v) for k, v in active if not v.get("time")]
    report["missing_health"] = [{"key": k, "title": v.get("title", k)} for k, v in no_health]
    report["missing_time"]   = [{"key": k, "title": v.get("title", k)} for k, v in no_time]

    # Save state and report
    if not args.dry_run:
        _save_state(state)
        REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Print summary
    print("=" * 60)
    print("SCAN SUMMARY")
    print("=" * 60)
    if "dead_urls" in report:
        print(f"  Dead/unreachable URLs:    {len(report['dead_urls'])}")
    if "images" in report:
        img = report["images"]
        total_missing = len(img["found"]) + len(img["fetch_failed"]) + len(img["no_source_url"])
        print(f"  Missing images:           {total_missing}  ({len(img['found'])} fixable with --fix-safe)")
    if "instructions" in report:
        instr = report["instructions"]
        total_missing = len(instr["found"]) + len(instr["failed_no_url"]) + len(instr["failed_fetch"])
        print(f"  Missing instructions:     {total_missing}  ({len(instr['found'])} fixable with --fix-safe)")
    if "semantic" in report:
        sem = report["semantic"]
        print(f"  Stub instructions:        {len(sem['stub_instructions'])}")
        print(f"  Needs review (flagged):   {len(sem['needs_review'])}")
        print(f"  Orphan .md files:         {len(sem['orphan_md'])}")
    print(f"  Missing health:           {len(no_health)}  (run --fix-classify)")
    print(f"  Missing time:             {len(no_time)}  (run --fix-classify)")
    print()
    if not args.dry_run:
        print(f"Full report: {REPORT_PATH}")
        print(f"State cache: {STATE_PATH}")
    print()

    # Print dead URLs
    if report.get("dead_urls"):
        print("Dead URLs (manual fix needed):")
        for item in report["dead_urls"]:
            cached = " [cached]" if item.get("cached") else ""
            print(f"  [{item['status']}]{cached} {item['title'][:50]}  {item['url'][:70]}")
        print()

    # Print needs_review
    if report.get("semantic", {}).get("needs_review"):
        print("needs_review=True entries (clear flag after confirming):")
        for item in report["semantic"]["needs_review"]:
            print(f"  {item['title'][:55]}  (cooked {item['times_cooked']}×, source: {item['source'][:30]})")
        print()

    # Print orphan .md
    if report.get("semantic", {}).get("orphan_md"):
        print("Orphan .md files (in recipes/ but no metadata entry):")
        for fn in report["semantic"]["orphan_md"]:
            print(f"  {fn}")
        print()

    next_steps = []
    if report.get("images", {}).get("found"):
        next_steps.append("--fix-safe   to apply images + any recovered instructions")
    if no_health or no_time:
        next_steps.append("--fix-classify   to preview Haiku health/time estimates")
    if next_steps:
        print("Next steps:")
        for step in next_steps:
            print(f"  python3 cleanup_agent.py {step}")
        print()

# ---------------------------------------------------------------------------
# Condiment check
# ---------------------------------------------------------------------------

def check_condiments() -> None:
    """Check condiments.json for missing images and dead source_urls."""
    if not CONDIMENTS_PATH.exists():
        print("condiments.json not found")
        return

    data = json.loads(CONDIMENTS_PATH.read_text())
    print(f"\n=== Condiment check ({len(data)} entries) ===\n")

    missing_image = []
    missing_url   = []
    dead_url      = []

    for name, entry in data.items():
        if not entry.get("image"):
            missing_image.append(name)
        url = entry.get("source_url", "")
        if not url:
            missing_url.append(name)
        else:
            try:
                r = httpx.head(url, timeout=8, follow_redirects=True)
                if r.status_code >= 400:
                    dead_url.append((name, url, r.status_code))
            except Exception as e:
                dead_url.append((name, url, str(e)))

    if missing_url:
        print(f"No source_url ({len(missing_url)}) — home/original recipes:")
        for n in missing_url:
            print(f"  {n}")
    if missing_image:
        print(f"\nMissing image ({len(missing_image)}):")
        for n in missing_image:
            print(f"  {n}")
    if dead_url:
        print(f"\nDead source_url ({len(dead_url)}):")
        for name, url, status in dead_url:
            print(f"  {name}: {status}  {url}")
    if not dead_url and not missing_image:
        print("All condiments OK.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Recipe collection cleanup agent")
    # Fix modes (stackable — any combination runs in order)
    parser.add_argument("--fix-safe",     action="store_true", help="Apply cached images + instructions")
    parser.add_argument("--fix-metadata", action="store_true", help="Auto-fix cuisine, source, meal_type, needs_review (no HTTP, no model)")
    parser.add_argument("--fix-classify", action="store_true", help="Run Haiku health/time classification")
    parser.add_argument("--apply",        action="store_true", help="With --fix-classify: write to metadata")
    # Scan filter
    parser.add_argument("--check-urls",         action="store_true")
    parser.add_argument("--check-images",       action="store_true")
    parser.add_argument("--check-instructions", action="store_true")
    parser.add_argument("--check-semantic",     action="store_true")
    parser.add_argument("--check-condiments",   action="store_true")
    # Tuning
    parser.add_argument("--dry-run",    action="store_true", help="Never write state or metadata")
    parser.add_argument("--stale-days", type=int, default=7, help="Re-check state entries older than N days")
    parser.add_argument("--limit",      type=int, default=0, help="Cap HTTP requests at N (0=all)")
    parser.add_argument("--workers",    type=int, default=12, help="Parallel HTTP threads")
    args = parser.parse_args()

    any_fix = args.fix_safe or args.fix_metadata or args.fix_classify

    if args.fix_safe:
        state = _load_state()
        fix_safe(state, args.dry_run)
    if args.fix_metadata:
        fix_metadata(args.dry_run)
    if args.fix_classify:
        fix_classify(apply=args.apply)
    if args.check_condiments:
        check_condiments()
    elif not any_fix:
        run_scan(args)


if __name__ == "__main__":
    main()
