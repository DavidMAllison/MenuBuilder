#!/usr/bin/env python3
"""
recipe_review_server.py -- Local web server for reviewing agent-sourced recipes.

Reads /tmp/italian_agent_results_{uid}.json and serves a Pinterest-style card UI.

Usage:
  python3 recipe_review_server.py
  Then open http://localhost:5051
"""

import glob
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

# fill_menu_ideas is in the same directory — import its intake helpers
sys.path.insert(0, str(Path(__file__).parent))
from fill_menu_ideas import (  # noqa: E402
    classify_health, classify_effort, parse_ingredients_structured,
    _build_recipe_md, _title_to_filename, _infer_cooking_method,
    _infer_meal_type, _quality_check, _register_cuisine, RECIPES_DIR,
)
from prep_utils import classify_prep  # noqa: E402

app = Flask(__name__, static_folder="recipe_review")

UID = os.getuid()
METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"

# Health classification cache — keyed by mtime fingerprint of /tmp agent files
_health_cache: dict = {}
_health_cache_key: str = ""


def _tmp_fingerprint() -> str:
    files = sorted(glob.glob(f"/tmp/*_agent_results_{UID}.json"))
    return "|".join(
        f"{f}:{Path(f).stat().st_mtime:.0f}" for f in files if Path(f).exists()
    )


def _health_cache_lookup(candidates: list) -> dict:
    global _health_cache, _health_cache_key
    key = _tmp_fingerprint()
    if key and key == _health_cache_key:
        return _health_cache
    try:
        _health_cache = classify_health(candidates)
        _health_cache_key = key
    except Exception:
        _health_cache = {}
    return _health_cache

_TITLE_STOP = {
    "easy", "simple", "quick", "best", "classic", "authentic", "homemade",
    "traditional", "perfect", "crispy", "tender", "juicy", "creamy", "spicy",
    "cheesy", "smoky", "hearty", "rustic", "amazing", "ultimate", "foolproof",
    "the", "a", "an", "my", "with", "and", "in", "or", "for", "style",
}


def _normalize_title(title: str) -> str:
    t = re.sub(r"[^\w\s]", "", title.lower())
    return " ".join(w for w in t.split() if w not in _TITLE_STOP)


def _existing_sets() -> tuple[set, set]:
    """Return (existing_urls, existing_norm_titles) from recipe_metadata.json."""
    try:
        data = json.loads(METADATA_PATH.read_text())
        recipes = data.get("recipes", {})
        urls = {
            (v.get("source_url", "") or v.get("url", "")).rstrip("/")
            for v in recipes.values()
            if v.get("source_url") or v.get("url")
        }
        norm_titles = {_normalize_title(v.get("title", k)) for k, v in recipes.items()}
        return urls, norm_titles
    except Exception:
        return set(), set()


@app.route("/")
def index():
    return send_from_directory("recipe_review", "index.html")


@app.route("/api/recipes")
def recipes():
    files = sorted(glob.glob(f"/tmp/*_agent_results_{UID}.json"))
    candidates = []
    seen_urls: set = set()
    for path in files:
        try:
            for r in json.loads(Path(path).read_text(encoding="utf-8")):
                url = (r.get("url", "") or "").rstrip("/")
                if url and url in seen_urls:
                    continue
                seen_urls.add(url)
                candidates.append(r)
        except Exception:
            pass
    if not candidates:
        return jsonify([])

    existing_urls, existing_norm = _existing_sets()
    health_map = _health_cache_lookup(candidates)

    annotated = []
    for r in candidates:
        url  = (r.get("url", "") or "").rstrip("/")
        norm = _normalize_title(r.get("title", ""))
        in_collection = (
            (bool(url)  and url  in existing_urls) or
            (bool(norm) and norm in existing_norm)
        )
        annotated.append({
            **r,
            "in_collection": in_collection,
            "meal_type":     _infer_meal_type(r),
            "health":        health_map.get(r.get("title", ""), r.get("health", "Moderate")),
        })

    return jsonify(annotated)


@app.route("/api/add", methods=["POST"])
def add_recipe():
    recipe = request.get_json()
    if not recipe or not recipe.get("title"):
        return jsonify({"error": "No recipe data"}), 400

    title = recipe["title"].strip()

    # Final dedup check before writing
    existing_urls, existing_norm = _existing_sets()
    url = (recipe.get("url", "") or "").rstrip("/")
    norm = _normalize_title(title)
    if (url and url in existing_urls) or (norm and norm in existing_norm):
        return jsonify({"error": "already_exists", "title": title}), 409

    # Haiku classification — health, prep, effort, structured ingredients
    r = {
        "title": title,
        "ingredients": recipe.get("ingredients", []),
        "instructions": recipe.get("instructions", []),
        "time": recipe.get("time", ""),
        "source": recipe.get("source", ""),
        "url": recipe.get("url", ""),
        "cuisine": recipe.get("cuisine", ""),
    }

    health_map  = classify_health([r])
    prep_map    = classify_prep([r])
    effort_map  = classify_effort([r])
    ing_map     = parse_ingredients_structured([r])

    cuisine = recipe.get("cuisine", "")
    if isinstance(cuisine, list):
        cuisine = ", ".join(cuisine)
    _register_cuisine(cuisine)

    prep_data = prep_map.get(title, {})
    entry = {
        "title":           title,
        "filename":        _title_to_filename(title),
        "source":          recipe.get("source", ""),
        "source_url":      url,
        "url":             url,
        "cuisine":         cuisine,
        "meal_type":       _infer_meal_type(r),
        "health":          health_map.get(title, "Moderate"),
        "times_cooked":    0,
        "time":            recipe.get("time", ""),
        "servings":        recipe.get("yield", ""),
        "status":          "active",
        "cooking_method":  _infer_cooking_method(title, recipe.get("instructions", [])),
        "last_cooked_date": None,
        "ingredients_raw": recipe.get("ingredients", []),
        "instructions":    recipe.get("instructions", []),
        "ingredients":     ing_map.get(title, []),
        "prep_components": prep_data.get("prep_components", []),
        "prep_notes":      prep_data.get("prep_notes", ""),
        "weeknight_effort": effort_map.get(title, ""),
        "needs_review":    _quality_check(
                               recipe.get("ingredients", []),
                               recipe.get("instructions", []),
                           ),
        "image":           recipe.get("image", ""),
    }

    # Write .md file
    md_path = RECIPES_DIR / entry["filename"]
    if not md_path.exists():
        md_path.write_text(
            _build_recipe_md(title, entry, entry["needs_review"]),
            encoding="utf-8",
        )

    # Write to metadata
    metadata = json.loads(METADATA_PATH.read_text())
    metadata["recipes"][title] = entry
    metadata["last_updated"] = date.today().isoformat()
    METADATA_PATH.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))

    return jsonify({"success": True, "title": title, "health": entry["health"]})


@app.route("/api/collection")
def collection():
    """Return all active recipes from recipe_metadata.json for the Full Collection view."""
    try:
        recipes = json.loads(METADATA_PATH.read_text()).get("recipes", {})
        result = []
        for key, v in recipes.items():
            if v.get("status") != "active":
                continue
            result.append({
                "title":        v.get("title", key),
                "cuisine":      v.get("cuisine", ""),
                "source":       v.get("source", ""),
                "url":          v.get("source_url", "") or v.get("url", ""),
                "time":         v.get("time", ""),
                "yield":        v.get("servings", ""),
                "health":       v.get("health", ""),
                "meal_type":    v.get("meal_type", ""),
                "times_cooked": v.get("times_cooked", 0),
                "image":        v.get("image", ""),
                "ingredients":  v.get("ingredients_raw", []),
                "instructions": v.get("instructions", []),
                "in_collection": True,
            })
        result.sort(key=lambda r: (r["cuisine"], r["title"]))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/remove", methods=["POST"])
def remove_recipe():
    """Test utility — remove a recipe from the collection by URL or title."""
    body = request.get_json()
    url   = (body.get("url", "") or "").rstrip("/")
    title = (body.get("title", "") or "").strip()

    metadata = json.loads(METADATA_PATH.read_text())
    recipes  = metadata["recipes"]

    key_to_remove = None
    for k, v in recipes.items():
        entry_url = (v.get("source_url", "") or v.get("url", "")).rstrip("/")
        if (url and entry_url == url) or (title and v.get("title", "").strip() == title):
            key_to_remove = k
            break

    if not key_to_remove:
        return jsonify({"error": "not found"}), 404

    entry = recipes.pop(key_to_remove)

    # Delete .md file if it exists
    md_path = RECIPES_DIR / entry.get("filename", "")
    if md_path.exists():
        md_path.unlink()

    metadata["recipes"] = recipes
    METADATA_PATH.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))

    return jsonify({"removed": entry.get("title", key_to_remove)})


if __name__ == "__main__":
    print(f"Aggregating /tmp/*_agent_results_{UID}.json")
    print("Open http://localhost:5051")
    app.run(port=5051, debug=False)
