# Recipe Source Patterns

Reference for how to classify and wire up new recipe sources. Every source maps to one of these patterns. Match the pattern first, then apply the corresponding technique.

---

## Pattern 1 — Website: structured recipe data

**What it is:** A recipe website that serves ingredients and steps in the page, either as `ld+json` (Recipe schema) or parseable HTML.

**Examples:** patijinich.com, altonbrown.com, smittenkitchen.com, mexicoinmykitchen.com, rickbayless.com, GialloZafferano, Memorie di Angelina, most WordPress recipe sites (WPRM plugin)

**Extraction:**
- Try `ld+json` (`@type: Recipe`) first — gives clean `recipeIngredient` and `recipeInstructions`
- Fall back to site-specific HTML parser if no schema
- WordPress/WPRM sites: REST API endpoint `/wp-json/wp/v2/wprm_recipe/` often returns structured data without scraping

**Discovery:**
- Sitemap crawl (`sitemap.xml`, `sitemap_index.xml`) — fetch all recipe URLs, filter by keyword
- WordPress search: `/?s=query` or WP REST API search
- Site-specific search page

**`video_url` field:** not set (no companion video)

---

## Pattern 2 — Website + companion YouTube video

**What it is:** A recipe website (Pattern 1) where the chef also posts a companion cooking video on YouTube. The recipe is complete on the site; the video is supplemental.

**Examples:** Chetna Makan (chetnamakan.co.uk — full recipe on site; YouTube video found by searching her channel with the recipe title)

**Extraction:** Same as Pattern 1 (site is authoritative for ingredients + steps). After fetching the site recipe, search the chef's YouTube channel by title to find the matching video and store as `video_url`.

**In the .md file:** `_write_recipe_md` adds `**Watch**: [YouTube](url)` when `video_url` differs from `source_url`.

**Discovery:** Site search or sitemap for the recipe; YouTube Data API channel search for the companion video.

**`video_url` field:** set to YouTube URL (different from `source_url`)

---

## Pattern 3 — YouTube only: ingredients in description, steps in transcript

**What it is:** A YouTube-only channel where the chef lists ingredients in the video description (often structured with an "Ingredients:" header), but step-by-step instructions are only spoken in the video.

**Examples:** Cooking con Claudia (`fetch_claudia` in `mexican_agent.py`)

**Extraction:**
1. Fetch video snippet via YouTube Data API v3 (`videos.list`, `part=snippet`) to get title, description, thumbnail
2. Parse description with `_parse_claudia_description()` (or `parse_yt_description()` from `yt_utils.py`) — handles English and Spanish section headers
3. Always enrich instructions from transcript via `enrich_recipe_from_transcript()` in `yt_utils.py` — transcript steps are more complete than any description notes
4. Ingredients: use description if non-empty; fall back to transcript if description had none

**`video_url` field:** set to the YouTube URL (same as `source_url` for pure-YT sources, so no Watch line is added — the attribution link already points to YouTube)

**Discovery:** YouTube Data API `search.list` with `channelId` filter

---

## Pattern 4 — YouTube only: both ingredients and steps in transcript

**What it is:** A YouTube-only channel where the description is unstructured (or in a foreign language with no consistent headers), so both ingredients and steps must be derived from the video transcript. Haiku extracts and translates in one pass.

**Examples:** De Mi Rancho a Tu Cocina / Doña Ángela (`fetch_rancho` in `mexican_agent.py`), J. Kenji López-Alt (`fetch_kenji` in `chef_agent.py`)

**Extraction:**
1. Fetch video snippet (same as Pattern 3)
2. Attempt description parse — if the description happens to be structured, capture anything found
3. Call `enrich_recipe_from_transcript(recipe, video_id)` from `yt_utils.py`:
   - Fetches transcript via `youtube-transcript-api` (prefers English; falls back to any language + translate)
   - Sends to Haiku with title: extracts ingredients list + numbered steps, translates to English
   - Fills ingredients from transcript only if description parse yielded nothing
   - Always replaces instructions with transcript-derived steps

**Note on translation:** Haiku handles Spanish (and other languages) transparently in the extraction prompt — no separate translation step needed.

**`video_url` field:** set to the YouTube URL

**Discovery:** YouTube Data API `search.list` with `channelId` filter. Filter out Shorts (`#short`/`shorts` in title).

---

## Pattern 5 — Paywalled / auth-required website

**What it is:** A recipe website that requires a paid account or login to access recipe content.

**Examples:** America's Test Kitchen / ATK (`atk_agent.py`)

**Extraction:** Same as Pattern 1 once authenticated. Use Playwright to:
1. Log in and cache session cookies in `config.json` (refresh after ~20h)
2. Fetch recipe pages via `httpx` with stored cookies (faster than Playwright per-page)
3. Parse ld+json or ATK-specific HTML

**Discovery:** ATK exposes saved-recipe collections via API; `atk_agent.py` syncs three collections (Try Out, Sunday Dinner, Dinners).

**`video_url` field:** not typically set (ATK videos are paywalled too)

---

## Routing in `fetch_recipe()`

Each agent's generic `fetch_recipe()` dispatcher routes by URL domain/pattern:

```
if "youtube.com" or "youtu.be" in url:
    # Pattern 3 or 4 — must have a dedicated fetch function per channel
    # youtube watch URLs do NOT encode channel ID, so routing must use
    # a dedicated tool per source (see below)
```

**Important:** Two YouTube sources in the same agent cannot share `fetch_recipe()` for routing because `watch?v=VIDEO_ID` URLs don't identify the channel. Use a **dedicated tool per YouTube source**:
- `fetch_recipe` → generic sites + Claudia (backward compat)
- `fetch_rancho_recipe` → De Mi Rancho a Tu Cocina only
- In `chef_agent.py`: `fetch_recipe` routes YouTube → `fetch_kenji` (only one YT source so no ambiguity)

If a second YouTube source is added to an agent that already has one, add a new dedicated tool.

---

## Decision tree when adding a new source

```
Does the chef have a website with recipes?
├── Yes → does the site have ld+json Recipe schema or a WPRM plugin?
│   ├── Yes → Pattern 1. Try generic ld+json parser first; add site-specific
│   │         HTML fallback only if schema is missing or incomplete.
│   └── No → Pattern 1, site-specific HTML parser required.
│       Does the chef also post companion YouTube videos?
│       └── Yes → Pattern 2. After site fetch, search their channel by title
│                 and store result as `video_url`.
│   Is the site paywalled?
│   └── Yes → Pattern 5. Add Playwright auth + cookie caching to config.json.
│
└── No → YouTube-only source.
    Does the video description consistently list structured ingredients?
    ├── Yes → Pattern 3. Parse description; always pull steps from transcript.
    └── No (unstructured or foreign-language description) → Pattern 4.
               Skip description parse; pull everything from transcript via Haiku.
```

---

## Key shared utilities

| Utility | Location | Used by |
|---|---|---|
| `parse_yt_description(desc)` | `yt_utils.py` | Pattern 3/4 — description parse |
| `fetch_yt_snippet(video_id, api_key)` | `yt_utils.py` | Pattern 3/4 — title, description, thumbnail |
| `fetch_transcript(video_id)` | `yt_utils.py` | Pattern 3/4 — raw transcript text |
| `extract_recipe_from_transcript(transcript, title)` | `yt_utils.py` | Pattern 4 — Haiku extraction + translation |
| `enrich_recipe_from_transcript(recipe, video_id)` | `yt_utils.py` | Pattern 3/4 — top-level enrichment call |
| `_parse_claudia_description(desc)` | `mexican_agent.py` | Pattern 3, Spanish descriptions |

For new YouTube sources, call `enrich_recipe_from_transcript()` after any description parsing and before returning the recipe dict. That single call covers both Pattern 3 and Pattern 4.
