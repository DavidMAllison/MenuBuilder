#!/usr/bin/env python3
"""
recipe_review_server.py -- Local web server for reviewing agent-sourced recipes.

Reads /tmp/italian_agent_results_{uid}.json and serves a Pinterest-style card UI.

Usage:
  python3 recipe_review_server.py
  Then open http://localhost:5051
"""

import glob
import hashlib
import json
import os
import re
import sys
import threading
import urllib.request
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_file, send_from_directory, session, url_for
from werkzeug.security import check_password_hash

# fill_menu_ideas is in the same directory — import its intake helpers
sys.path.insert(0, str(Path(__file__).parent))
from fill_menu_ideas import (  # noqa: E402
    classify_health, classify_effort, parse_ingredients_structured,
    _build_recipe_md, _title_to_filename, _infer_cooking_method,
    _infer_meal_type, _quality_check, _register_cuisine, RECIPES_DIR,
)
from prep_utils import classify_prep  # noqa: E402

app = Flask(__name__, static_folder="recipe_review")

_CONFIG_PATH = Path(__file__).parent / "config.json"
_CONFIG = json.loads(_CONFIG_PATH.read_text())
app.secret_key = _CONFIG.get("flask_secret_key", "dev-key-change-me")
app.permanent_session_lifetime = timedelta(days=30)

_REVIEW_USERS = {u["email"]: u["name"] for u in _CONFIG.get("review_users", [])}
_REVIEW_PW_HASH = _CONFIG.get("review_password_hash", "")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login_page", next=request.path))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        raw_next = request.args.get("next") or "/"
        next_url = raw_next if raw_next.startswith("/") else "/"
        if email in _REVIEW_USERS and _REVIEW_PW_HASH and check_password_hash(_REVIEW_PW_HASH, password):
            session.permanent = remember
            session["user"] = email
            session["name"] = _REVIEW_USERS[email]
            return redirect(next_url)
        error_next = f"&next={next_url}" if next_url != "/" else ""
        return redirect(url_for("login_page") + f"?error=1{error_next}")
    return send_from_directory("recipe_review", "login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


UID = os.getuid()
METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"
IMG_CACHE_DIR = Path.home() / ".cache" / "recipe_images"
IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Health classification cache — keyed by mtime fingerprint of /tmp agent files
_health_cache: dict = {}
_health_cache_key: str = ""

# Metadata cache — invalidated when recipe_metadata.json mtime changes
_metadata_cache = None
_metadata_mtime: float = 0.0


def _load_metadata() -> dict:
    global _metadata_cache, _metadata_mtime
    try:
        mtime = METADATA_PATH.stat().st_mtime
    except OSError:
        return _metadata_cache or {}
    if _metadata_cache is not None and mtime == _metadata_mtime:
        return _metadata_cache
    _metadata_cache = json.loads(METADATA_PATH.read_text())
    _metadata_mtime = mtime
    return _metadata_cache


def _save_metadata(data: dict) -> None:
    global _metadata_cache, _metadata_mtime
    METADATA_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    _metadata_mtime = METADATA_PATH.stat().st_mtime
    _metadata_cache = data

# Semantic search index — built lazily in a background thread on first request
_chroma_client = None
_chroma_collection = None
_index_lock = threading.Lock()
_index_built = False
_index_building = False


def _doc_text(key: str, v: dict) -> str:
    """Build the text document to embed for a single recipe."""
    parts = [
        v.get("title", key),
        v.get("cuisine", ""),
        v.get("source", ""),
        v.get("time", ""),
        v.get("health", ""),
        v.get("meal_type", ""),
    ]
    ings = v.get("ingredients_raw") or []
    if not ings:
        ings = [i.get("name", "") for i in (v.get("ingredients") or []) if isinstance(i, dict)]
    parts.extend(ings[:20])
    instr = v.get("instructions") or []
    parts.extend(instr[:5])
    return " | ".join(str(p) for p in parts if p)


def _build_index():
    global _chroma_client, _chroma_collection, _index_built, _index_building
    with _index_lock:
        if _index_built:
            return
        _index_building = True

    try:
        import chromadb
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("all-MiniLM-L6-v2")
        client = chromadb.Client()
        col = client.get_or_create_collection("recipes", metadata={"hnsw:space": "cosine"})

        data = _load_metadata()
        recipes = {k: v for k, v in data.get("recipes", {}).items()
                   if v.get("status") == "active"}

        ids, docs, metas = [], [], []
        for key, v in recipes.items():
            ids.append(key)
            docs.append(_doc_text(key, v))
            metas.append({
                "title":        v.get("title", key),
                "cuisine":      v.get("cuisine", "") or "",
                "source":       v.get("source", "") or "",
                "time":         v.get("time", "") or "",
                "health":       v.get("health", "") or "",
                "meal_type":    v.get("meal_type", "") or "",
                "times_cooked": v.get("times_cooked", 0),
                "image":        v.get("image", "") or "",
                "url":          (v.get("source_url") or v.get("url") or ""),
                "ingredients":  json.dumps((v.get("ingredients_raw") or [])[:12]),
                "instructions": json.dumps((v.get("instructions") or [])[:6]),
            })

        if ids:
            embeddings = model.encode(docs, show_progress_bar=False).tolist()
            col.add(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)

        with _index_lock:
            _chroma_client = client
            _chroma_collection = col
            _index_built = True
            _index_building = False

    except Exception as e:
        with _index_lock:
            _index_building = False
        print(f"[search] index build failed: {e}", flush=True)


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
        data = _load_metadata()
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
@login_required
def index():
    return send_from_directory("recipe_review", "index.html")


@app.route("/api/me")
@login_required
def me():
    return jsonify({"name": session.get("name", ""), "email": session.get("user", "")})


@app.route("/api/recipes")
@login_required
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
@login_required
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
    metadata = _load_metadata()
    metadata["recipes"][title] = entry
    metadata["last_updated"] = date.today().isoformat()
    _save_metadata(metadata)

    return jsonify({"success": True, "title": title, "health": entry["health"]})


@app.route("/api/this_week")
@login_required
def this_week():
    """Return current week's meals with recipe detail from metadata."""
    weeklyplan_dir = METADATA_PATH.parent / "weeklyplan"
    if not weeklyplan_dir.exists():
        return jsonify({"found": False, "meals": []})

    today = date.today()
    dated = []
    for f in weeklyplan_dir.glob("mealplan_*.json"):
        try:
            d = date.fromisoformat(f.stem.replace("mealplan_", ""))
            dated.append((d, f))
        except ValueError:
            continue
    dated.sort(key=lambda x: x[0], reverse=True)
    plan_file = next((f for d, f in dated if d <= today + timedelta(days=1)), None)
    if not plan_file:
        return jsonify({"found": False, "meals": []})

    try:
        plan = json.loads(plan_file.read_text())
    except Exception:
        return jsonify({"found": False, "meals": []})

    metadata = _load_metadata().get("recipes", {})

    meals = []
    for m in plan.get("meals", []):
        title = m.get("title", "")
        # fuzzy lookup in metadata for image + ingredients + instructions
        meta = metadata.get(title, {})
        if not meta:
            title_lower = title.lower()
            for k, v in metadata.items():
                if k.lower() == title_lower:
                    meta = v
                    break
        meals.append({
            "day":          m.get("day", ""),
            "date":         m.get("date", ""),
            "title":        title,
            "health":       m.get("health", ""),
            "time":         m.get("time", ""),
            "url":          m.get("url", ""),
            "reminder":     m.get("reminder", ""),
            "image":        meta.get("image", ""),
            "source":       meta.get("source", ""),
            "cuisine":      meta.get("cuisine", ""),
            "ingredients":  meta.get("ingredients_raw", []) or [
                f"{i.get('quantity','')} {i.get('unit','')} {i.get('name','')}".strip()
                for i in (meta.get("ingredients") or [])
                if isinstance(i, dict)
            ],
            "instructions": meta.get("instructions", []),
            "times_cooked": meta.get("times_cooked", 0),
        })

    return jsonify({
        "found":      True,
        "week_start": plan.get("week_start", ""),
        "week_end":   plan.get("week_end", ""),
        "balance":    plan.get("balance", {}),
        "meals":      meals,
    })


@app.route("/api/collection")
@login_required
def collection():
    """Return all active recipes from recipe_metadata.json for the Full Collection view."""
    try:
        recipes = _load_metadata().get("recipes", {})
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
                "ingredients":  v.get("ingredients_raw") or [
                    f"{i.get('quantity','')} {i.get('unit','')} {i.get('name','')}".strip()
                    for i in (v.get("ingredients") or []) if isinstance(i, dict)
                ],
                "instructions": v.get("instructions", []),
                "in_collection": True,
            })
        result.sort(key=lambda r: (r["cuisine"], r["title"]))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/remove", methods=["POST"])
@login_required
def remove_recipe():
    """Test utility — remove a recipe from the collection by URL or title."""
    body = request.get_json()
    url   = (body.get("url", "") or "").rstrip("/")
    title = (body.get("title", "") or "").strip()

    metadata = _load_metadata()
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
    _save_metadata(metadata)

    return jsonify({"removed": entry.get("title", key_to_remove)})


def _negation_terms(q: str) -> list[str]:
    """Extract terms the user wants to exclude, e.g. 'not salmon', 'isn\'t salmon'."""
    terms = []
    for pattern in [r"\bnot\s+(\w+)", r"\bisn'?t\s+(\w+)", r"\bno\s+(\w+)", r"\bwithout\s+(\w+)"]:
        terms.extend(m.group(1).lower() for m in re.finditer(pattern, q, re.I))
    return terms


@app.route("/api/search")
@login_required
def search_recipes():
    """Semantic search over the full collection using sentence-transformers + ChromaDB."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])

    if not _index_built:
        if _index_building:
            return jsonify({"status": "building"}), 202
        # Shouldn't happen after startup kick, but handle gracefully
        threading.Thread(target=_build_index, daemon=True).start()
        return jsonify({"status": "building"}), 202

    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        q_emb = model.encode([q], show_progress_bar=False).tolist()
        results = _chroma_collection.query(
            query_embeddings=q_emb,
            n_results=min(20, _chroma_collection.count()),
            include=["metadatas", "distances"],
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    exclude = _negation_terms(q)

    hits = []
    for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
        title_lower = meta.get("title", "").lower()
        if any(term in title_lower for term in exclude):
            continue
        hits.append({
            "title":        meta.get("title", ""),
            "cuisine":      meta.get("cuisine", ""),
            "source":       meta.get("source", ""),
            "time":         meta.get("time", ""),
            "health":       meta.get("health", ""),
            "meal_type":    meta.get("meal_type", ""),
            "times_cooked": meta.get("times_cooked", 0),
            "image":        meta.get("image", ""),
            "url":          meta.get("url", ""),
            "ingredients":  json.loads(meta.get("ingredients", "[]")),
            "instructions": json.loads(meta.get("instructions", "[]")),
            "in_collection": True,
            "score":        round(1 - dist, 3),
        })
    return jsonify(hits)


@app.route("/api/search_status")
@login_required
def search_status():
    return jsonify({"ready": _index_built, "building": _index_building})


@app.route("/api/img")
@login_required
def proxy_image():
    """Proxy + disk-cache external recipe images. Avoids mobile re-fetching external CDNs."""
    url = request.args.get("url", "").strip()
    if not url or not url.startswith("http"):
        return "", 400

    cache_key = hashlib.sha256(url.encode()).hexdigest()
    # Preserve extension hint for MIME sniffing; default to jpg
    ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
        ext = "jpg"
    cache_path = IMG_CACHE_DIR / f"{cache_key}.{ext}"

    if cache_path.exists():
        return send_file(cache_path)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
        cache_path.write_bytes(data)
        return send_file(cache_path, mimetype=content_type.split(";")[0].strip())
    except Exception:
        return "", 404


if __name__ == "__main__":
    print(f"Aggregating /tmp/*_agent_results_{UID}.json")
    print("Open http://localhost:5051")
    # Kick off index build in background so it's ready before first search
    threading.Thread(target=_build_index, daemon=True).start()
    app.run(host="0.0.0.0", port=5051, debug=False)
