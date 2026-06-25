#!/usr/bin/env python3
"""
recipe_agent.py -- Recipe search orchestrator.

Routes recipe requests to the appropriate subagents and aggregates results.
This is the single entry point for all recipe searches.

Sources (via subagents):
  Chef   : Alton Brown, Smitten Kitchen (Deb Perelman), Chetna Makan
  Mexican: Pati Jinich, Rick Bayless, Cooking con Claudia
  Asian  : Just One Cookbook (Japanese), Maangchi (Korean),
           Hot Thai Kitchen (Thai), Viet World Kitchen (Vietnamese),
           Woks of Life (Chinese/Pan-Asian)
  Indian : Indian Healthy Recipes, Hebbars Kitchen, Chetna Makan,
           Kannamma Cooks (South Indian/Tamil)
  Sites  : Serious Eats

Routing:
  Mexican dish/ingredient → mexican agent
  Asian dish (Japanese, Korean, Thai, Vietnamese, Chinese) → asian agent
  Indian dish/ingredient  → indian agent
  Chef name mentioned     → chef agent (restricted to that chef)
  "Serious Eats" / site   → sites agent
  General query           → chef agent (default)

Results capped at 5, written to /tmp/recipe_agent_results.json.

Usage:
  recipe "find a carnitas recipe"
  recipe "weeknight chicken from Alton Brown"
  recipe "braised short rib from Serious Eats"
"""

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List

import anthropic

if not os.environ.get("ANTHROPIC_API_KEY"):
    env_path = Path.home() / "projects/personal/sms-assistant/.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                break

sys.path.insert(0, str(Path(__file__).parent))

RESULTS_PATH = Path(f"/tmp/recipe_agent_results_{os.getuid()}.json")
MAX_RESULTS = 5

client = anthropic.Anthropic()

_CONFIG_PATH = Path(__file__).parent / "config.json"


def search_local_collection(query: str) -> List[dict]:
    """Search recipes in recipe_metadata.json by name, cuisine, and source.

    Returns a list of matching recipes with title, url, cuisine, source, time, and health.
    Results are sorted by relevance (number of query terms matched).
    Intended for SMS lookup intent — user asking for a recipe they already have.
    """
    config = json.loads(_CONFIG_PATH.read_text())
    metadata_path = Path(config["metadata_path"].replace("~", str(Path.home())))
    github_base = config.get("github_pages_base_url", "")

    recipes = json.loads(metadata_path.read_text())
    terms = [t for t in query.lower().split() if len(t) > 2]
    if not terms:
        return []

    results = []
    for name, data in recipes.items():
        if not isinstance(data, dict):
            continue
        if data.get("status") in ("disliked", "ignored"):
            continue
        searchable = " ".join(filter(None, [
            name,
            data.get("cuisine", ""),
            data.get("source", ""),
        ])).lower()
        score = sum(1 for t in terms if t in searchable)
        if score == 0:
            continue
        filename = name.replace(" ", "_")
        results.append({
            "title": name,
            "url": f"{github_base}/{filename}" if github_base else "",
            "cuisine": data.get("cuisine", ""),
            "source": data.get("source", ""),
            "time": data.get("time", ""),
            "health": data.get("health", ""),
            "_score": score,
        })

    results.sort(key=lambda r: r["_score"], reverse=True)
    for r in results:
        del r["_score"]
    return results

TOOLS = [
    {
        "name": "search_mexican_agent",
        "description": (
            "Search Mexican recipe sources: Pati Jinich (patijinich.com), "
            "Rick Bayless (rickbayless.com), Cooking con Claudia (YouTube). "
            "Use for any dish that is Mexican cuisine — apply culinary knowledge, "
            "not keyword matching. Mole is always Mexican regardless of protein "
            "(turkey mole, chicken mole, etc.). Same for carnitas, tamales, "
            "enchiladas, pozole, chiles rellenos, birria, cochinita pibil, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Recipe search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_chef_agent",
        "description": (
            "Search chef recipe sources: Alton Brown (altonbrown.com), "
            "Smitten Kitchen / Deb Perelman (smittenkitchen.com), "
            "Chetna Makan (chetnamakan.co.uk). "
            "Default for general queries. Include chef name in query if the user specifies one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Recipe search query; include chef name if specified"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_asian_agent",
        "description": (
            "Search Asian recipe sources covering Japanese, Korean, Thai, Vietnamese, and Chinese cuisines. "
            "Sources: Just One Cookbook (Japanese), Maangchi (Korean), Hot Thai Kitchen (Thai), "
            "Viet World Kitchen (Vietnamese), Woks of Life (Chinese/Pan-Asian). "
            "Use for any dish you know to be Japanese, Korean, Thai, Vietnamese, or Chinese. "
            "Examples: ramen, miso soup, teriyaki, gyoza, bibimbap, bulgogi, tteokbokki, japchae, "
            "pad thai, green curry, tom kha, pho, banh mi, kung pao chicken, mapo tofu, char siu, "
            "beef and broccoli, dan dan noodles, fried rice, dumplings, red braised pork belly. "
            "Also use for general 'Asian' or 'stir-fry' queries with no specific cuisine cue."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Recipe search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_indian_agent",
        "description": (
            "Search Indian recipe sources covering North Indian, South Indian, British-Indian, and Tamil cuisines. "
            "Sources: Indian Healthy Recipes (mainstream Indian), Hebbars Kitchen (street food, snacks, sweets), "
            "Chetna Makan (British-Indian, lighter family cooking), Kannamma Cooks (South Indian/Tamil/Chettinad). "
            "Use for any dish you know to be Indian. "
            "Examples: butter chicken, dal makhani, palak paneer, biryani, chole, rajma, aloo gobi, korma, "
            "chicken tikka masala, rogan josh, rasam, sambar, dosa, idli, chettinad chicken, pav bhaji, "
            "pakora, samosa, gulab jamun, halwa, naan, tandoori chicken, saag, khichdi, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Recipe search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_sites_agent",
        "description": (
            "Search cross-cuisine recipe sites: Serious Eats (seriouseats.com). "
            "Use only when the user explicitly names a site or says 'other sites'. "
            "Include site name in query if specified."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Recipe search query; include site name if specified"},
            },
            "required": ["query"],
        },
    },
]

SYSTEM = """You are a recipe search orchestrator. Route recipe requests to the right search tools.

Routing rules:
- Any dish that is Mexican cuisine: call search_mexican_agent. Use culinary knowledge — mole (turkey mole, chicken mole, mole negro), carnitas, tamales, enchiladas, pozole, tacos, tostadas, cochinita pibil, birria, aguachile, etc. The protein modifier does not change the cuisine.
- Any dish that is Japanese, Korean, Thai, Vietnamese, or Chinese: call search_asian_agent. Use culinary knowledge — ramen, miso soup, teriyaki, gyoza, bulgogi, bibimbap, tteokbokki, galbi, japchae, pad thai, green curry, tom kha, larb, pho, banh mi, goi cuon, kung pao chicken, mapo tofu, char siu, beef and broccoli, fried rice, dumplings, etc. Also use for "stir-fry" with no other cue.
- Any dish that is Indian cuisine: call search_indian_agent. Use culinary knowledge — butter chicken, dal makhani, palak paneer, biryani, chole, rajma, aloo gobi, korma, tikka masala, rogan josh, nihari, rasam, sambar, idli, dosa, uttapam, chettinad chicken, pav bhaji, pakora, samosa, khichdi, saag, naan, etc. Indian cuisine is distinct from Asian — do not route Indian dishes to search_asian_agent.
- General query with no cuisine or site cue: call search_chef_agent (default)
- User names a specific chef (Alton Brown, Smitten Kitchen, Deb Perelman, Chetna Makan): call search_chef_agent with the chef name in the query
- User says "Serious Eats", "other sites", or names a site: call search_sites_agent with the site name in the query
- Multiple cues: call multiple tools

Never call the same tool twice. After all tools return, print a short summary: title, source, and cook time for each result."""

_CACHED_SYSTEM = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
_CACHED_TOOLS = [*TOOLS[:-1], {**TOOLS[-1], "cache_control": {"type": "ephemeral"}}]


def _get_runner(tool_name: str):
    if tool_name == "search_mexican_agent":
        from mexican_agent import run_agent
        return run_agent
    if tool_name == "search_asian_agent":
        from asian_agent import run_agent
        return run_agent
    if tool_name == "search_indian_agent":
        from indian_agent import run_agent
        return run_agent
    if tool_name == "search_chef_agent":
        from chef_agent import run_agent
        return run_agent
    if tool_name == "search_sites_agent":
        from sites_agent import run_agent
        return run_agent
    return None


def run_agent(user_request: str) -> List[dict]:
    messages = [{"role": "user", "content": user_request}]
    print(f"\nSearching: {user_request}\n")

    all_results = []
    seen_urls: set = set()

    while True:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            system=_CACHED_SYSTEM,
            tools=_CACHED_TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    print(block.text)
            break

        if response.stop_reason != "tool_use":
            print(f"Unexpected stop reason: {response.stop_reason}")
            break

        messages.append({"role": "assistant", "content": response.content})

        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        tool_results = []

        def _dispatch(block):
            runner = _get_runner(block.name)
            if runner is None:
                return block.id, {"error": f"Unknown tool: {block.name}"}
            label = block.name.replace("search_", "").replace("_agent", "").replace("_", " ").title()
            print(f"\n--- {label} ---")
            query = re.sub(r"\s+recipes?$", "", block.input["query"], flags=re.IGNORECASE).strip()
            return block.id, runner(query)

        with ThreadPoolExecutor(max_workers=len(tool_blocks)) as executor:
            futures = {executor.submit(_dispatch, b): b for b in tool_blocks}
            results_by_id = {}
            for future in as_completed(futures):
                tool_id, results = future.result()
                results_by_id[tool_id] = results

        for block in tool_blocks:
            results = results_by_id[block.id]
            if isinstance(results, list):
                for r in results:
                    url = r.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append(r)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(results),
            })

        messages.append({"role": "user", "content": tool_results})

    capped = all_results[:MAX_RESULTS]
    RESULTS_PATH.write_text(json.dumps(capped, indent=2), encoding="utf-8")
    return capped


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    results = run_agent(" ".join(sys.argv[1:]))
    if results:
        print(f"\nFound {len(results)} recipe(s):")
        for i, r in enumerate(results, 1):
            source = r.get("source", "").split(" - ")[0] or r.get("site", "")
            time_str = r.get("time", "")
            cuisine = r.get("cuisine", "")
            if isinstance(cuisine, list):
                cuisine = ", ".join(cuisine)
            detail = " | ".join(filter(None, [source, cuisine, time_str]))
            print(f"  {i}. {r.get('title', '?')} ({detail})")
        print(f"\nResults saved to {RESULTS_PATH}")
    else:
        print("\nNo recipes found.")
