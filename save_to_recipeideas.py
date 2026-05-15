#!/usr/bin/env python3
"""Save recipes from /tmp/mexican_agent_results.json to recipeideas/ by index."""

import json
import re
import sys
from pathlib import Path

RESULTS_PATH = Path("/tmp/mexican_agent_results.json")
RECIPEIDEAS_DIR = Path.home() / "Dropbox/LLMContext/cooking/recipeideas"


def save(indices: list[int]) -> None:
    recipes = json.loads(RESULTS_PATH.read_text())

    for i in indices:
        if i < 1 or i > len(recipes):
            print(f"  No recipe #{i}")
            continue
        r = recipes[i - 1]
        title = (r.get("title") or "").strip()
        if not title:
            print(f"  #{i}: no title, skipping")
            continue

        filename = re.sub(r"[^\w\s-]", "", title).strip()
        filename = re.sub(r"\s+", "_", filename) + ".md"
        filepath = RECIPEIDEAS_DIR / filename

        if filepath.exists():
            print(f"  #{i}: {filename} already in recipeideas, skipping")
            continue

        lines = [f"# {title}", "", f"Source: {r.get('url', '')}"]
        if r.get("time"):
            lines.append(f"Time: {r['time']}")
        if r.get("yield"):
            lines.append(f"Yield: {r['yield']}")
        lines += ["", "## Ingredients", ""]
        for ing in r.get("ingredients", []):
            lines.append(f"- {ing}")
        lines += ["", "## Instructions", ""]
        for idx, step in enumerate(r.get("instructions", []), 1):
            lines.append(f"{idx}. {step}")
        if r.get("description"):
            lines += ["", "## Notes", "", r["description"]]
        lines.append("")

        filepath.write_text("\n".join(lines), encoding="utf-8")
        print(f"  Saved to recipeideas: {filename}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 save_to_recipeideas.py 1 3 4")
        sys.exit(1)
    save([int(x) for x in sys.argv[1:]])
