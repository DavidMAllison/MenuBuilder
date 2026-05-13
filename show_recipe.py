#!/usr/bin/env python3
"""Open a recipe as a styled HTML page in the browser."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

METADATA_PATH = Path.home() / "Dropbox/LLMContext/cooking/recipe_metadata.json"
RECIPES_DIR = Path.home() / "Dropbox/LLMContext/cooking/recipes"

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: Georgia, 'Times New Roman', serif;
    background: #faf9f6;
    color: #2c2c2c;
    padding: 2rem 1rem;
  }}
  .card {{
    max-width: 720px;
    margin: 0 auto;
    background: #fff;
    border-radius: 8px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
    padding: 2.5rem 3rem;
  }}
  h1 {{
    font-size: 2rem;
    font-weight: bold;
    margin-bottom: 0.4rem;
    color: #1a1a1a;
  }}
  .meta {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem 1.4rem;
    font-family: -apple-system, sans-serif;
    font-size: 0.85rem;
    color: #666;
    margin-bottom: 1.8rem;
    padding-bottom: 1.2rem;
    border-bottom: 1px solid #e8e5e0;
  }}
  .meta span {{ display: flex; align-items: center; gap: 0.3rem; }}
  .badge {{
    display: inline-block;
    padding: 0.15rem 0.55rem;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 600;
    font-family: -apple-system, sans-serif;
  }}
  .heart-healthy {{ background: #d4edda; color: #155724; }}
  .moderate {{ background: #fff3cd; color: #856404; }}
  .indulgent {{ background: #f8d7da; color: #721c24; }}
  h2 {{
    font-size: 1.1rem;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #555;
    margin: 1.8rem 0 0.8rem;
    font-family: -apple-system, sans-serif;
  }}
  ul {{
    padding-left: 1.2rem;
    line-height: 1.8;
  }}
  ul li {{ margin-bottom: 0.1rem; }}
  ol {{
    padding-left: 1.3rem;
    line-height: 1.8;
  }}
  ol li {{
    margin-bottom: 0.6rem;
    padding-left: 0.2rem;
  }}
  p {{ line-height: 1.7; margin-bottom: 0.6rem; }}
  .note {{
    background: #f5f3ee;
    border-left: 3px solid #c8b89a;
    padding: 0.8rem 1rem;
    border-radius: 0 4px 4px 0;
    font-size: 0.9rem;
    color: #555;
    margin-top: 1.2rem;
    font-family: -apple-system, sans-serif;
  }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .card {{ box-shadow: none; padding: 1rem; }}
  }}
</style>
</head>
<body>
<div class="card">
  <h1>{title}</h1>
  <div class="meta">
    {meta_items}
  </div>
  {body}
</div>
</body>
</html>"""


def find_recipe(query: str, recipes: dict):
    query_lower = query.lower()
    # exact match first
    for name, data in recipes.items():
        if name.lower() == query_lower:
            return name, data
    # substring match
    matches = [(name, data) for name, data in recipes.items()
               if query_lower in name.lower() and data.get("status") == "active"]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Multiple matches: {[m[0] for m in matches]}")
        return matches[0]
    return None


def md_to_html_body(md: str) -> str:
    """Minimal markdown-to-HTML for recipe files (no external deps)."""
    lines = md.split("\n")
    html_lines = []
    in_ul = False
    in_ol = False
    in_p = False

    def close_lists():
        nonlocal in_ul, in_ol, in_p
        if in_ul:
            html_lines.append("</ul>")
            in_ul = False
        if in_ol:
            html_lines.append("</ol>")
            in_ol = False
        if in_p:
            html_lines.append("</p>")
            in_p = False

    # skip the title line (h1) — already in header
    skip_first_h1 = True

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("# ") and skip_first_h1:
            skip_first_h1 = False
            continue

        if stripped.startswith("## "):
            close_lists()
            section = stripped[3:].strip()
            html_lines.append(f"<h2>{section}</h2>")

        elif stripped.startswith("### "):
            close_lists()
            section = stripped[4:].strip()
            html_lines.append(f"<h3>{section}</h3>")

        elif stripped.startswith("- "):
            if in_ol:
                html_lines.append("</ol>")
                in_ol = False
            if in_p:
                html_lines.append("</p>")
                in_p = False
            if not in_ul:
                html_lines.append("<ul>")
                in_ul = True
            html_lines.append(f"<li>{stripped[2:]}</li>")

        elif stripped and stripped[0].isdigit() and ". " in stripped:
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            if in_p:
                html_lines.append("</p>")
                in_p = False
            if not in_ol:
                html_lines.append("<ol>")
                in_ol = True
            text = stripped.split(". ", 1)[1]
            html_lines.append(f"<li>{text}</li>")

        elif stripped == "":
            close_lists()

        else:
            if not in_p and not in_ul and not in_ol:
                html_lines.append("<p>")
                in_p = True
            html_lines.append(stripped)

    close_lists()
    return "\n".join(html_lines)


def health_badge(health: str) -> str:
    css = {"Heart-Healthy": "heart-healthy", "Moderate": "moderate", "Indulgent": "indulgent"}
    cls = css.get(health, "moderate")
    return f'<span class="badge {cls}">{health}</span>'


def build_html(name: str, meta: dict, md_content: str) -> str:
    meta_items = []
    if meta.get("time"):
        meta_items.append(f"<span>⏱ {meta['time']}</span>")
    if meta.get("servings"):
        meta_items.append(f"<span>Serves {meta['servings']}</span>")
    if meta.get("cuisine"):
        meta_items.append(f"<span>{meta['cuisine']}</span>")
    if meta.get("source"):
        if meta.get("source_url"):
            meta_items.append(f'<span><a href="{meta["source_url"]}" target="_blank" style="color:#555;text-decoration:underline dotted;">{meta["source"]}</a></span>')
        else:
            meta_items.append(f"<span>{meta['source']}</span>")
    if meta.get("health"):
        meta_items.append(f"<span>{health_badge(meta['health'])}</span>")

    body = md_to_html_body(md_content)

    return HTML_TEMPLATE.format(
        title=name,
        meta_items="\n    ".join(meta_items),
        body=body,
    )


def main():
    if len(sys.argv) < 2:
        print("Usage: show_recipe.py <recipe name>")
        sys.exit(1)

    query = " ".join(sys.argv[1:])

    with open(METADATA_PATH) as f:
        data = json.load(f)
    recipes = data["recipes"]

    result = find_recipe(query, recipes)
    if not result:
        print(f"Recipe not found: {query}")
        sys.exit(1)

    name, meta = result
    filename = meta.get("filename", "")

    # prefer .md, fall back to whatever is in metadata
    md_filename = filename.replace(".pdf", ".md")
    md_path = RECIPES_DIR / md_filename
    if not md_path.exists():
        md_path = RECIPES_DIR / filename
    if not md_path.exists():
        print(f"Recipe file not found: {md_path}")
        sys.exit(1)

    md_content = md_path.read_text()
    html = build_html(name, meta, md_content)

    safe_name = name.replace(" ", "_").replace("/", "-")
    tmp_path = Path(tempfile.gettempdir()) / f"recipe_{safe_name}.html"
    tmp_path.write_text(html)

    subprocess.run(["open", str(tmp_path)])
    print(f"Opened: {name}")


if __name__ == "__main__":
    main()
