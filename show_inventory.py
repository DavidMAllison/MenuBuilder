#!/usr/bin/env python3
"""Render the current inventory as a styled HTML page and open it in the browser."""

import json
import subprocess
import tempfile
from pathlib import Path
from datetime import date

INVENTORY_FILE = Path("/Users/Shared/cooking-state/inventory.json")

# Display order for categories
CATEGORY_ORDER = ["Proteins", "Produce", "Dairy", "Pantry", "Dry Goods", "Frozen Meals", "Other"]

CATEGORY_ICONS = {
    "Proteins": "🥩",
    "Produce": "🥦",
    "Dairy": "🧀",
    "Pantry": "🫙",
    "Dry Goods": "🌾",
    "Frozen Meals": "🧊",
    "Other": "📦",
}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Inventory</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #faf9f6;
    color: #2c2c2c;
    padding: 2rem 1rem;
  }}
  .card {{
    max-width: 860px;
    margin: 0 auto;
    background: #fff;
    border-radius: 8px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
    padding: 2.5rem 3rem;
  }}
  h1 {{
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 2rem;
    font-weight: bold;
    color: #1a1a1a;
    margin-bottom: 0.4rem;
  }}
  .meta {{
    font-size: 0.85rem;
    color: #888;
    margin-bottom: 2rem;
    padding-bottom: 1.2rem;
    border-bottom: 1px solid #e8e5e0;
  }}
  .categories {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
    gap: 1.5rem;
  }}
  .category {{
    border: 1px solid #e8e5e0;
    border-radius: 6px;
    overflow: hidden;
  }}
  .category-header {{
    background: #f5f3ee;
    padding: 0.6rem 1rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid #e8e5e0;
  }}
  .category-title {{
    font-weight: 600;
    font-size: 0.9rem;
    color: #444;
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }}
  .category-count {{
    font-size: 0.75rem;
    color: #999;
    background: #fff;
    border-radius: 999px;
    padding: 0.1rem 0.5rem;
    border: 1px solid #ddd;
  }}
  .item-list {{
    padding: 0;
    list-style: none;
  }}
  .item {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 0.45rem 1rem;
    border-bottom: 1px solid #f0ede8;
    font-size: 0.88rem;
  }}
  .item:last-child {{ border-bottom: none; }}
  .item:hover {{ background: #faf9f7; }}
  .item-name {{
    color: #2c2c2c;
    flex: 1;
    text-transform: capitalize;
  }}
  .item-qty {{
    color: #888;
    font-size: 0.82rem;
    margin-left: 1rem;
    white-space: nowrap;
  }}
  .item-qty.low {{
    color: #c0392b;
    font-weight: 600;
  }}
  .subcategory-tag {{
    font-size: 0.72rem;
    color: #aaa;
    margin-left: 0.4rem;
    font-style: italic;
  }}
  .summary {{
    margin-top: 2rem;
    padding-top: 1.2rem;
    border-top: 1px solid #e8e5e0;
    font-size: 0.82rem;
    color: #aaa;
    text-align: right;
  }}
  @media (max-width: 600px) {{
    .card {{ padding: 1.5rem 1.2rem; }}
    .categories {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<div class="card">
  <h1>Inventory</h1>
  <div class="meta">Last updated {last_updated} &nbsp;·&nbsp; {total} items across {cat_count} categories</div>
  <div class="categories">
    {categories_html}
  </div>
  <div class="summary">Generated {today}</div>
</div>
</body>
</html>"""


def fmt_qty(qty, unit):
    """Format quantity nicely: drop .0 suffix, handle singular/plural."""
    if qty == int(qty):
        qty_str = str(int(qty))
    else:
        qty_str = f"{qty:g}"
    if unit in ("ea", ""):
        return qty_str if qty_str != "1" else "1"
    return f"{qty_str} {unit}"


def is_low(qty, unit):
    """Flag suspiciously low quantities."""
    if unit in ("lbs", "kg"):
        return qty < 0.5
    return qty <= 1


def build_category_html(cat_name, items):
    icon = CATEGORY_ICONS.get(cat_name, "")
    rows = []
    for item in sorted(items, key=lambda x: x["name"].lower()):
        name = item["name"]
        qty = item.get("quantity", 0)
        unit = item.get("unit", "")
        subcat = item.get("subcategory", "")

        qty_str = fmt_qty(qty, unit)
        low = is_low(qty, unit)
        qty_class = 'class="item-qty low"' if low else 'class="item-qty"'

        subcat_html = f'<span class="subcategory-tag">{subcat}</span>' if subcat else ""

        rows.append(
            f'<li class="item">'
            f'<span class="item-name">{name}{subcat_html}</span>'
            f'<span {qty_class}>{qty_str}</span>'
            f'</li>'
        )

    return f"""<div class="category">
      <div class="category-header">
        <span class="category-title">{icon} {cat_name}</span>
        <span class="category-count">{len(items)}</span>
      </div>
      <ul class="item-list">
        {''.join(rows)}
      </ul>
    </div>"""


def main():
    if not INVENTORY_FILE.exists():
        print(f"Inventory file not found: {INVENTORY_FILE}")
        return

    data = json.loads(INVENTORY_FILE.read_text())
    items = [i for i in data.get("items", []) if i.get("quantity", 0) > 0]
    last_updated = data.get("last_updated", "unknown")

    # Group by category
    by_cat: dict[str, list] = {}
    for item in items:
        cat = item.get("category", "Other")
        by_cat.setdefault(cat, []).append(item)

    # Build HTML in defined order, then any remaining categories
    cats_html_parts = []
    for cat in CATEGORY_ORDER:
        if cat in by_cat:
            cats_html_parts.append(build_category_html(cat, by_cat[cat]))
    for cat in sorted(by_cat):
        if cat not in CATEGORY_ORDER:
            cats_html_parts.append(build_category_html(cat, by_cat[cat]))

    html = HTML_TEMPLATE.format(
        last_updated=last_updated,
        total=len(items),
        cat_count=len(by_cat),
        categories_html="\n    ".join(cats_html_parts),
        today=date.today().strftime("%b %d, %Y"),
    )

    tmp = Path(tempfile.gettempdir()) / "inventory.html"
    tmp.write_text(html)
    subprocess.run(["open", str(tmp)])
    print(f"Opened inventory ({len(items)} items, last updated {last_updated})")


if __name__ == "__main__":
    main()
