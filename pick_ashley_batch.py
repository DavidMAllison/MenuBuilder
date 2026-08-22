#!/usr/bin/env python3
"""
pick_ashley_batch.py -- Pick 5 fresh recipe candidates from the idea queue
for Ashley to review via the Review UI's filtered batch view.

Selection:
  - Pulls from the same agent_results/*_agent_results.json queue as the
    Review UI's New view.
  - Excludes anything already in the active collection, and anything sent
    in a prior batch (tracked in ashley_batch_sent.json).
  - Ashley is time-focused -- sorts by cook time ascending (parse_minutes),
    so quick weeknight-shaped recipes surface first; multi-hour recipes
    (slow cooker, ATK braises, etc.) only get picked if nothing shorter
    remains in the queue.

Writes the picks to ashley_recipe_batch.json (read by the Review UI's
/api/batch/<id> endpoint and by the sms-assistant tool that texts the
link) and appends them to ashley_batch_sent.json so they're not repeated.

Usage:
    python3 pick_ashley_batch.py            # generate + write, print summary
    python3 pick_ashley_batch.py --dry-run  # preview without writing
"""

import argparse
import glob
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from candidate_scoring import parse_minutes  # noqa: E402

# Hardlinked to ~/Dropbox/LLMContext/cooking (same inode) -- never Path.home()
# here: this module is also invoked via the MCP bridge subprocess, which runs
# as whichever account triggered it (often allisonbot, not davidallison), so
# Path.home() would silently resolve to the wrong account's home directory.
COOKING_DIR = Path("/Users/Shared/cooking")
AGENT_RESULTS_DIR = COOKING_DIR / "agent_results"
METADATA_PATH = COOKING_DIR / "recipe_metadata.json"
STATE_DIR = Path("/Users/Shared/cooking-state")
BATCH_PATH = STATE_DIR / "ashley_recipe_batch.json"
SENT_LOG_PATH = STATE_DIR / "ashley_batch_sent.json"

BATCH_SIZE = 5

_TITLE_STOP = {
    "easy", "simple", "quick", "best", "classic", "authentic", "homemade",
    "traditional", "perfect", "crispy", "tender", "juicy", "creamy", "spicy",
    "cheesy", "smoky", "hearty", "rustic", "amazing", "ultimate", "foolproof",
    "the", "a", "an", "my", "with", "and", "in", "on", "or", "for", "style",
}


def _clean_source_name(source: str) -> str:
    """Strip URL suffix from agent source labels: 'Serious Eats - https://...' -> 'Serious Eats'."""
    if " - http" in source:
        return source.split(" - http")[0].strip()
    return source


def _normalize_title(title: str) -> str:
    # Punctuation becomes a space, not deleted -- "Chorizo-Potato" must split
    # into "chorizo"/"potato" the same way "Chorizo Potato" does, or hyphenated
    # titles silently dodge fuzzy-duplicate matching against spaced ones.
    t = re.sub(r"[^\w\s]", " ", title.lower())
    return " ".join(w for w in t.split() if w not in _TITLE_STOP)


def _existing_sets() -> tuple[set, set]:
    data = json.loads(METADATA_PATH.read_text())
    recipes = data.get("recipes", {})
    urls = {
        (v.get("source_url", "") or v.get("url", "")).rstrip("/")
        for v in recipes.values()
        if (v.get("source_url") or v.get("url")) and v.get("status") == "active"
    }
    titles = {
        _normalize_title(v.get("title", k))
        for k, v in recipes.items()
        if v.get("status") == "active"
    }
    return urls, titles


DISMISSED_PATH = STATE_DIR / "dismissed_recipes.json"


def _dismissed_sets() -> tuple[set, set]:
    """Return (dismissed_urls, dismissed_norm_titles). Belt-and-suspenders alongside
    fill_menu_ideas.py's post-run sweep of agent_results/*.json -- a recipe can be
    dismissed at any time between sweeps, and this runs on every batch/email build."""
    try:
        entries = json.loads(DISMISSED_PATH.read_text())
    except Exception:
        return set(), set()
    urls = {(e.get("url", "") or "").rstrip("/") for e in entries if e.get("url")}
    norm_titles = {_normalize_title(e.get("title", "")) for e in entries if e.get("title")}
    norm_titles.discard("")
    return urls, norm_titles


def _sent_sets() -> tuple[set, set]:
    if not SENT_LOG_PATH.exists():
        return set(), set()
    try:
        entries = json.loads(SENT_LOG_PATH.read_text())
    except Exception:
        return set(), set()
    urls = {(e.get("url", "") or "").rstrip("/") for e in entries}
    titles = {_normalize_title(e.get("title", "")) for e in entries}
    return urls, titles


def gather_candidates() -> list:
    existing_urls, existing_titles = _existing_sets()
    sent_urls, sent_titles = _sent_sets()
    dismissed_urls, dismissed_titles = _dismissed_sets()

    files = sorted(glob.glob(str(AGENT_RESULTS_DIR / "*_agent_results.json")))
    fresh = []
    seen_urls, seen_titles = set(), set()
    for f in files:
        try:
            entries = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        for r in entries:
            url = (r.get("url", "") or "").rstrip("/")
            title = r.get("title", "")
            nt = _normalize_title(title)
            if not title or not url:
                continue
            if url in existing_urls or nt in existing_titles:
                continue
            if url in sent_urls or nt in sent_titles:
                continue
            if url in dismissed_urls or nt in dismissed_titles:
                continue
            if url in seen_urls or nt in seen_titles:
                continue
            seen_urls.add(url)
            seen_titles.add(nt)
            fresh.append(r)
    return fresh


def pick_batch(candidates: list, n: int = BATCH_SIZE) -> list:
    ranked = sorted(candidates, key=lambda r: parse_minutes(r.get("time", "")))
    return ranked[:n]


def generate_batch(size: int = BATCH_SIZE, write: bool = True) -> Optional[dict]:
    """Gather, pick, and (by default) persist a batch. Returns the batch dict,
    or None if the queue has nothing fresh to offer. Shared by the CLI entry
    point below and the sms-assistant tool that texts Ashley the link."""
    candidates = gather_candidates()
    if not candidates:
        return None

    picks = pick_batch(candidates, size)
    batch = {
        "batch_id": date.today().isoformat(),
        "generated_at": datetime.now().isoformat(),
        "picks": [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "source": _clean_source_name(r.get("source", "")),
                "time": r.get("time", ""),
            }
            for r in picks
        ],
    }

    if write:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        BATCH_PATH.write_text(json.dumps(batch, indent=2, ensure_ascii=False))
        # chmod requires owning the file, not just write access -- harmless to skip
        # if a different account (davidallison vs. allisonbot) already owns it with
        # workable permissions; only matters the first time a given account creates it.
        try:
            BATCH_PATH.chmod(0o666)
        except Exception:
            pass

        sent_log = []
        if SENT_LOG_PATH.exists():
            try:
                sent_log = json.loads(SENT_LOG_PATH.read_text())
            except Exception:
                sent_log = []
        for p in batch["picks"]:
            sent_log.append({"title": p["title"], "url": p["url"], "sent_at": batch["generated_at"]})
        SENT_LOG_PATH.write_text(json.dumps(sent_log, indent=2, ensure_ascii=False))
        try:
            SENT_LOG_PATH.chmod(0o666)
        except Exception:
            pass

    return batch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--size", type=int, default=BATCH_SIZE, help="Number of picks")
    args = parser.parse_args()

    batch = generate_batch(size=args.size, write=not args.dry_run)
    if batch is None:
        print("No fresh candidates available -- queue is empty or fully sent.")
        return

    print(f"Batch {batch['batch_id']}: {len(batch['picks'])} picks")
    for p in batch["picks"]:
        print(f"  - {p['title']} ({p['time'] or '?'}) -- {p['source']}")

    if args.dry_run:
        print("Dry run -- not writing.")
    else:
        print(f"Wrote {BATCH_PATH}")


if __name__ == "__main__":
    main()
