# Learning MCP (for Dummies)

## What is MCP?

Model Context Protocol — Anthropic's standard for giving Claude access to external tools and data sources. Instead of hardcoding tools inside a script (like the recipe agents), MCP servers run as persistent processes that Claude can discover and call from any session.

Think of it as: your agents have tools baked in → MCP lets those tools live outside Claude, accessible from Claude Code, the desktop app, or any MCP-compatible client.

## Why it matters for this project

Right now Claude reads `recipe_metadata.json` directly during meal planning. With MCP:
- Claude calls a tool instead of loading the full JSON
- Semantic search becomes possible (find recipes by meaning, not just keyword)
- The same server works from Claude Code sessions, SMS, wherever

## Learning Path

### Step 1 — Understand the protocol
Read the official docs first: https://modelcontextprotocol.io
Focus on: what a server looks like, how tools are defined, how Claude discovers them.

### Step 2 — Install the Python SDK
```bash
pip install mcp
```

### Step 3 — Build a trivial server (do this before anything else)
One tool, returns static data. Goal: understand the structure, not build anything useful.

```python
# hello_mcp.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("hello")

@mcp.tool()
def greet(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run()
```

### Step 4 — Wire it to Claude Code
Add the server to Claude Code's MCP config (`~/.claude/claude_desktop_config.json` or via `/mcp` in Claude Code). Confirm Claude can call `greet` in a session. This step is where the protocol clicks.

### Step 5 — Swap in a real tool
Replace the `greet` tool with `search_local_collection` from `recipe_agent.py`. Now Claude can search the recipe collection via MCP during any conversation.

```python
from mcp.server.fastmcp import FastMCP
import sys
sys.path.insert(0, "/Users/davidallison/projects/personal/MenuBuilder")
from recipe_agent import search_local_collection

mcp = FastMCP("recipes")

@mcp.tool()
def search_recipes(query: str) -> list:
    """Search the local recipe collection by name, cuisine, or source."""
    return search_local_collection(query)
```

### Step 6 (stretch) — Add semantic search
Replace the keyword search with a vector search using `sentence-transformers` + ChromaDB (already in the backlog as the RAG experiment). Same MCP interface, smarter results.

## Key Concepts to Understand Along the Way

| Concept | What it means |
|---|---|
| Server | A process exposing tools over the MCP protocol |
| Tool | A function Claude can call (like the tools in the recipe agents) |
| Transport | How Claude talks to the server — `stdio` for local, HTTP for remote |
| Discovery | How Claude learns what tools a server has |

## What's different from the inline tools in the agents?

The recipe agents define tools as JSON dicts and handle the loop manually. MCP abstracts all of that — you just define Python functions and the SDK handles the protocol, discovery, and invocation. Less boilerplate, more portable.

## Files to create when ready

```
MenuBuilder/
  mcp/
    recipe_server.py    # the MCP server
    README.md           # how to run it and add to Claude Code config
```

---

## Proposed Architecture: Recipe Search MCP Server

### The core design principle: thin tools, smart orchestrator

The existing recipe agents embed a Claude instance as the orchestrator — a nested LLM call that knows nothing about your family's preferences, inventory, or what you cooked last week. It just searches and returns raw results.

In a CLI conversation, Claude already has all of that context (from CLAUDE.md, the weekly plan, the inventory file). The better design is to give Claude thin search tools and let it do the orchestrating with full context. MCP is how you do that.

### Why this use case justifies MCP

The trigger was this query: *"find me a recipe that uses rosemary — I have too much."*

That query needs to:
1. Search the existing collection by **ingredient** (not just name/cuisine/source — a gap in the current local search)
2. Search all external sources in parallel
3. Search ATK with credentials
4. Filter results against family preferences, health goals, recent meals, and inventory

A Python script can do steps 1-3. Step 4 requires context the script doesn't have. Claude does — if it has the right tools to search with.

### The tools to expose

| Tool | What it does | Why it's a tool and not hardcoded logic |
|---|---|---|
| `search_local(query)` | Semantic search over existing collection — finds by meaning, not keyword | Enables "uses rosemary", "something light", "like bulgogi but faster" queries |
| `search_chef(query)` | Alton Brown, Smitten Kitchen, Chetna Makan | Public sites, no auth needed |
| `search_mexican(query)` | Pati Jinich, Rick Bayless, Claudia | Separate agent, specialized routing |
| `search_sites(query)` | Serious Eats | Playwright, public |
| `search_atk(query)` | America's Test Kitchen | Requires credentials — the main reason for MCP |
| `fetch_recipe(url)` | Full recipe content from any URL | Claude decides which candidates are worth fetching |

`search_local` is the tool that benefits most from semantic search (Step 6). A query like "find me a recipe that uses rosemary" needs to match against ingredient lists, descriptions, and recipe names by *meaning* — keyword matching on "rosemary" misses recipes that call for "fresh herbs" or are just tagged with it in the ingredients array but not the name. With sentence-transformers + ChromaDB, the same tool handles "something light for a Tuesday", "like the bulgogi but less prep", and "uses up the rosemary" without any query parsing logic.

### Why ATK specifically benefits from MCP

The existing agents are stateless — each script run opens a fresh Playwright browser. For ATK, that means re-authenticating every time: slow and increases bot-detection risk.

An MCP server persists across tool calls. The right approach:
- On first `search_atk` call: log in, then save the browser session to disk with Playwright's `storage_state`
- On subsequent calls: load session from disk, skip login if still valid
- If session is expired: re-authenticate and save again

This works even with `stdio` transport (where Claude Code starts/stops the server), because the session state lives on disk, not in memory.

```python
SESSION_PATH = Path("~/.claude/atk_session.json").expanduser()

def _get_atk_page(playwright):
    browser = playwright.chromium.launch(headless=True)
    if SESSION_PATH.exists():
        context = browser.new_context(storage_state=str(SESSION_PATH))
    else:
        context = browser.new_context()
    page = context.new_page()
    # verify session is live; if not, log in and save
    page.goto("https://www.americastestkitchen.com")
    if "sign-in" in page.url:
        _login(page, config["atk_email"], config["atk_password"])
        context.storage_state(path=str(SESSION_PATH))
    return page
```

### Two entry points, same underlying functions

SMS already calls `recipe_agent.py` as a Python script — that stays. CLI uses the MCP tools directly. Same search functions imported in both places.

```
SMS → recipe_agent.py → imports chef_agent, mexican_agent, sites_agent, atk_agent
CLI → MCP server     → imports same functions, exposes as tools
```

No duplication. The agents stay as importable modules. The MCP server is just a thin wrapper that registers their functions as tools.

### What Claude does with the tools

For a query like "find me a rosemary recipe, I have too much":

1. Calls `search_local(ingredient="rosemary")` — checks what's already in the collection
2. Calls `search_chef`, `search_sites`, `search_atk` in parallel with a rosemary-focused query
3. Receives raw candidates from all sources
4. Filters against: health classification, last cooked date, protein variety, kids' preferences, spice level
5. Calls `fetch_recipe` on the 2-3 most promising candidates to get full content
6. Presents ranked suggestions with rationale

This is the same reasoning Claude already does during meal planning — MCP just gives it the search reach to do it on demand.

### stdio vs HTTP transport for this use case

Use `stdio`. Claude Code launches the server as a subprocess — same lifecycle as the session. No server to manage, no port to expose. The ATK session state persists via disk (`storage_state`), so the Playwright session benefit isn't lost when the server restarts.

HTTP transport is for remote or shared servers (e.g., a server running on a home assistant that multiple clients connect to). Not needed here.
