#!/usr/bin/env python3
from __future__ import annotations
"""
yt_utils.py — shared YouTube transcript extraction helpers.

Used by cuisine agents (mexican_agent, chef_agent, etc.) to enrich recipes
from video transcripts when description parsing is incomplete.

Strategy:
  - Ingredients: use description if available; fall back to transcript
  - Instructions: always pull from transcript (richer than description);
    fall back to description instructions only if transcript unavailable
"""

import json
import os
import re
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------

def _make_client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        env_path = Path.home() / "projects/personal/sms-assistant/.env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                    break
    return anthropic.Anthropic()


# ---------------------------------------------------------------------------
# Video snippet fetch (title, description, thumbnail)
# ---------------------------------------------------------------------------

_YT_API_BASE = "https://www.googleapis.com/youtube/v3"

def fetch_yt_snippet(video_id: str, api_key: str) -> dict | None:
    """Fetch YouTube video snippet (title, description, image) via Data API v3.
    Returns dict with keys title, description, image, or None on failure."""
    try:
        import httpx
        params = {"id": video_id, "part": "snippet", "key": api_key}
        with httpx.Client(timeout=15) as http:
            r = http.get(f"{_YT_API_BASE}/videos", params=params)
            r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return None
        snippet = items[0]["snippet"]
        thumbs = snippet.get("thumbnails", {})
        image = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
        return {
            "title": snippet.get("title", "").strip(),
            "description": snippet.get("description", ""),
            "image": image,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Description parser (English + Spanish headers)
# ---------------------------------------------------------------------------

_DESC_INGREDIENT_HEADERS = {"ingredients", "ingredientes", "ingredient", "ingrediente"}
_DESC_INSTRUCTION_HEADERS = {
    "instructions", "instrucciones", "directions", "method", "preparation",
    "preparacion", "preparación", "steps", "pasos", "procedure", "how to make",
}
_DESC_IMPORTANT_RE = re.compile(
    r"^[^a-zA-Z]*i\s*m\s*p\s*o\s*r\s*t\s*a\s*n\s*t[^a-zA-Z]*$", re.IGNORECASE
)
_DESC_SOCIAL_RE = re.compile(
    r"(tiktok|instagram|\bfb\b|facebook|business inquir|follow me|subscribe"
    r"|www\.|\.com/@|\.co/@|amazon|affiliate)",
    re.IGNORECASE,
)


def parse_yt_description(description: str) -> tuple[list[str], list[str]]:
    """Extract ingredients and instructions from a YouTube video description.

    Handles English and Spanish section headers. Returns (ingredients, instructions).
    Instructions from descriptions are usually sparse — use enrich_recipe_from_transcript
    to replace them with transcript-derived steps.
    """
    lines = description.splitlines()
    ingredients: list[str] = []
    instructions: list[str] = []
    section = None

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower().rstrip(":").strip()

        if any(lower == h or lower.startswith(h + ":") or lower.startswith(h + " ")
               for h in _DESC_INGREDIENT_HEADERS):
            section = "ingredients"
            continue
        if any(lower == h or lower.startswith(h + ":") or lower.startswith(h + " ")
               for h in _DESC_INSTRUCTION_HEADERS):
            section = "instructions"
            continue
        if _DESC_IMPORTANT_RE.match(stripped) or re.match(r"tip\b", stripped, re.IGNORECASE):
            section = None
            continue

        if not stripped:
            continue

        if stripped.startswith("http") or stripped.startswith("@") or _DESC_SOCIAL_RE.search(stripped):
            if ingredients or instructions:
                section = None
            continue

        if section == "ingredients":
            clean = re.sub(r"^[-•*✓]\s*", "", stripped)
            if clean and not re.match(r"^\d+[-–]\d+\s+servings?", clean, re.IGNORECASE):
                ingredients.append(clean)
        elif section == "instructions":
            clean = re.sub(r"^\d+[.)]\s*", "", stripped)
            if clean:
                instructions.append(clean)

    return ingredients, instructions


# ---------------------------------------------------------------------------
# Transcript fetch
# ---------------------------------------------------------------------------

def fetch_transcript(video_id: str) -> str | None:
    """Fetch YouTube transcript text for a video ID. Returns None on failure.

    Uses youtube-transcript-api v1.x (instance-based API).
    Preference order: manual English → auto English → any available (translated).
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound
    except ImportError:
        return None

    try:
        api = YouTubeTranscriptApi()
        tl = api.list(video_id)

        transcript = None
        try:
            transcript = tl.find_manually_created_transcript(["en"])
        except NoTranscriptFound:
            pass

        if transcript is None:
            try:
                transcript = tl.find_generated_transcript(["en"])
            except NoTranscriptFound:
                pass

        if transcript is None:
            # Fall back to first available in any language — Haiku translates at extraction time
            available = list(tl)
            if available:
                transcript = available[0]

        if transcript is None:
            return None

        snippets = transcript.fetch()
        return " ".join(
            (s.text if hasattr(s, "text") else s.get("text", ""))
            for s in snippets
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Haiku extraction
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """\
You are extracting a recipe from a YouTube cooking video transcript.
Translate everything to English if the transcript is in another language.

Video title: {title}

Transcript (may be auto-generated — fix obvious caption errors):
{transcript}

Extract:
- ingredients: list of strings, each with quantity and unit (e.g. "2 cups all-purpose flour")
- instructions: list of numbered step strings in the order they are performed

Return ONLY valid JSON, no prose:
{{"ingredients": ["...", ...], "instructions": ["...", ...]}}

If you cannot find ingredients or steps, return empty lists for those fields."""


def extract_recipe_from_transcript(transcript: str, title: str) -> dict:
    """Call Haiku to extract ingredients and instructions from a transcript.
    Returns {"ingredients": [...], "instructions": [...]}."""
    client = _make_client()
    # Truncate — transcripts can be long; 6000 chars covers a 20-30 min video
    text = transcript[:6000]
    prompt = _EXTRACT_PROMPT.format(title=title, transcript=text)
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return {
            "ingredients": [str(i) for i in data.get("ingredients", []) if i],
            "instructions": [str(s) for s in data.get("instructions", []) if s],
        }
    except Exception:
        return {"ingredients": [], "instructions": []}


# ---------------------------------------------------------------------------
# Top-level enrichment helper (what agents call)
# ---------------------------------------------------------------------------

def enrich_recipe_from_transcript(recipe: dict, video_id: str) -> dict:
    """Enrich a recipe dict using the YouTube transcript.

    - ingredients: kept from recipe if non-empty; filled from transcript if empty
    - instructions: always replaced with transcript-derived steps (richer source);
      falls back to existing instructions if transcript unavailable or yields nothing

    Returns the recipe dict modified in-place (also returned for convenience).
    """
    needs_ingredients = not recipe.get("ingredients")
    # Always attempt transcript for instructions
    transcript = fetch_transcript(video_id)
    if not transcript:
        return recipe

    extracted = extract_recipe_from_transcript(transcript, recipe.get("title", ""))

    if needs_ingredients and extracted["ingredients"]:
        recipe["ingredients"] = extracted["ingredients"]

    if extracted["instructions"]:
        recipe["instructions"] = extracted["instructions"]

    return recipe
