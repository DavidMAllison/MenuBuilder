#!/usr/bin/env python3
"""
backfill_images.py — Fetch og:image for existing recipes missing an image URL.

Reads recipe_metadata.json, fetches source_url for entries with no image,
and writes the og:image back to metadata.

Usage:
  python3 backfill_images.py              # all active recipes missing an image
  python3 backfill_images.py --dry-run    # preview without writing
  python3 backfill_images.py --limit 20   # process at most 20 recipes
  python3 backfill_images.py --recipe "Chicken Tikka Masala"
  python3 backfill_images.py --force      # re-fetch even if image already set
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
MAX_WORKERS = 8


def _fetch_og_image(url: str) -> str:
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
        if not resp.text:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        tag = soup.find("meta", property="og:image")
        return (tag.get("content", "") if tag else "") or ""
    except Exception:
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill og:image for existing recipes")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--limit", type=int, default=0, help="Max recipes to process (0 = all)")
    parser.add_argument("--recipe", type=str, default="", help="Process a single recipe by title")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if image already set")
    args = parser.parse_args()

    metadata = json.loads(METADATA_PATH.read_text())
    recipes = metadata["recipes"]

    candidates = []
    skipped_no_url = 0
    for key, v in recipes.items():
        if v.get("status") != "active":
            continue
        if args.recipe and v.get("title", key) != args.recipe:
            continue
        if not args.force and v.get("image"):
            continue
        url = (v.get("source_url", "") or v.get("url", "")).strip()
        if not url:
            skipped_no_url += 1
            continue
        candidates.append((key, v, url))

    if args.limit:
        candidates = candidates[: args.limit]

    prefix = "DRY RUN — " if args.dry_run else ""
    print(f"{prefix}{len(candidates)} recipes to process ({skipped_no_url} skipped: no source URL)")
    if not candidates:
        return

    results: dict[str, str] = {}  # key -> image url (or "")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_key = {
            pool.submit(_fetch_og_image, url): key
            for key, _v, url in candidates
        }
        done = 0
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            image = future.result()
            results[key] = image
            done += 1
            title = recipes[key].get("title", key)
            status = f"→ {image[:72]}" if image else "no image found"
            print(f"  [{done}/{len(candidates)}] {title[:52]:<52}  {status}")

    found    = sum(1 for img in results.values() if img)
    not_found = len(results) - found

    if not args.dry_run and found:
        for key, image in results.items():
            if image:
                recipes[key]["image"] = image
        METADATA_PATH.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))

    action = "Would update" if args.dry_run else "Updated"
    print(f"\n{action} {found} recipes.  {not_found} had no og:image.  {skipped_no_url} skipped (no URL).")


if __name__ == "__main__":
    main()
