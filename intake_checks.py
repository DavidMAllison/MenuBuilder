"""intake_checks.py -- catch hidden prep work when a recipe enters the collection.

Two things the source page's structured data doesn't tell us, both of which
let Miso Chicken (Oct 2026) be planned as a quick weeknight dinner:

1. Required waits. Sites' ld+json `totalTime` usually leaves out marinating /
   brining / chilling (Just One Cookbook lists "Marinating: 1 day" in a
   separate custom field). detect_advance_prep() reads the instructions and
   returns the minimum *required* wait, so "25 minutes" becomes "25 minutes
   plus 24 hours marinating" and the recipe is never treated as quick.

2. Sub-recipes. "5 Tbsp Homemade All-Purpose Miso Sauce" linked to a separate
   recipe on the source page, and the link was dropped with the HTML.
   detect_sub_recipes() follows ingredient links on the source page and keeps
   the ones that are themselves recipes; ensure_condiment() files each one in
   condiments.json (+ .md) so it lands in `condiment_deps` -- which the
   shopping list already expands and the .md builder links.

3. Chicken cut. Most boneless-chicken recipes work with breasts or thighs,
   and which one is in the fridge shouldn't decide dinner. detect_cut_swap()
   decides whether the cut matters; when it doesn't, apply_cut_swap() rewrites
   the ingredient line to list both and the steps to give each cut's doneness
   temperature, cook time and -- for acidic/enzymatic marinades -- marinating
   time. Recipes with `cut_locked: true` (Miso Chicken) are left alone.

Called from every path that writes a new recipe to recipe_metadata.json
(Review UI add, SMS/MCP URL intake) via run_intake_checks(). Same house
pattern as everywhere else: cheap keyword fast-path, LLM only when it fires,
every failure degrades to "no signal" and never blocks the intake.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from prep_utils import _make_client

_CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())
_COOKING_DIR = Path(_CONFIG["metadata_path"].replace("~", str(Path.home()))).parent
CONDIMENTS_PATH = _COOKING_DIR / "condiments.json"
CONDIMENTS_DIR = _COOKING_DIR / "condiments"
PAGES_BASE = _CONFIG.get("github_pages_base_url", "").rstrip("/")

_FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
_MODEL = "claude-sonnet-5-5"

# A recipe whose required wait is at least this long can't be a "quick" night
# and needs its ingredients bought the day before.
ADVANCE_PREP_MIN_HOURS = 1
BUY_DAY_BEFORE_HOURS = 4

_WAIT_KEYWORDS = re.compile(
    r"marinat|brine|overnight|refrigerat|chill|soak|\bcure|ferment|\brise\b|proof|"
    r"\d+\s*(?:hours?|hrs?|days?)\b",
    re.IGNORECASE,
)
_INGREDIENT_LINK_SELECTORS = (
    ".wprm-recipe-ingredient a",
    ".tasty-recipes-ingredients a",
    ".mv-create-ingredients a",
    "[itemprop=recipeIngredient] a",
    ".ingredients a",
)
_MAX_LINKS = 6


def _structured(prompt: str, schema: dict, client=None, effort: str = "low") -> Optional[dict]:
    """One Sonnet call with a JSON-schema response. None on any failure."""
    try:
        client = client or _make_client()
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=4000,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
        return json.loads(next(b.text for b in resp.content if b.type == "text"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 1. Required waits
# ---------------------------------------------------------------------------

_WAIT_PROMPT = """Recipe: {title}

Instructions:
{instructions}

Is there a REQUIRED waiting period before this dish can be served that a cook
must plan ahead for -- marinating, brining, dry-brining, curing, chilling,
soaking beans, dough rising, or similar?

Rules:
- Report the MINIMUM required wait in hours. "Marinate at least 8 hours or up
  to 2 days" -> 8. "Marinate 30 minutes or up to 24 hours" -> 0.5.
  "Refrigerate overnight" -> 8. "1 day" -> 24.
- Ignore optional waits ("if you have time", "up to 24 hours" with no minimum),
  storage notes ("keeps 3 days"), make-ahead tips, and active cooking time
  (simmering, braising, baking, resting meat a few minutes).
- If there is no required wait, return hours 0.
- label: one word for the wait -- marinating, brining, chilling, soaking,
  rising, curing, or resting.
"""

_WAIT_SCHEMA = {
    "type": "object",
    "properties": {
        "hours": {"type": "number"},
        "label": {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": ["hours", "label", "evidence"],
    "additionalProperties": False,
}


def detect_advance_prep(title: str, instructions: list, client=None) -> Optional[dict]:
    """Return {"hours", "label", "evidence"} for a required wait, else None."""
    text = "\n".join(f"{i}. {s}" for i, s in enumerate(instructions or [], 1))
    if not text or not _WAIT_KEYWORDS.search(text):
        return None
    result = _structured(_WAIT_PROMPT.format(title=title, instructions=text), _WAIT_SCHEMA, client)
    if not result or float(result.get("hours") or 0) < ADVANCE_PREP_MIN_HOURS:
        return None
    return {"hours": float(result["hours"]), "label": (result.get("label") or "waiting").strip().lower(),
            "evidence": result.get("evidence", "")}


def _format_hours(hours: float) -> str:
    h = math.ceil(hours)
    return "1 hour" if h == 1 else f"{h} hours"


_TIME_MENTIONS_WAIT = re.compile(r"\bplus\b|\+|marinat|brin|chill|soak|ris(e|ing)|rest|cur(e|ing)", re.IGNORECASE)


def apply_advance_prep(entry: dict, prep: Optional[dict]) -> None:
    """Record a required wait on the entry and make it visible in `time`.

    The wait is only appended when `time` doesn't already account for it --
    either by mentioning it ("+ 4 hours marinating") or by already being at
    least that long (Hot Smoked Salmon's "30 hours" includes its brine).
    """
    if not prep:
        return
    from candidate_scoring import parse_minutes  # lazy: candidate_scoring imports this module
    entry["advance_prep_hours"] = prep["hours"]
    time_str = (entry.get("time") or "").strip()
    if _TIME_MENTIONS_WAIT.search(time_str):
        return
    if time_str and parse_minutes(time_str) != 999 and parse_minutes(time_str) >= prep["hours"] * 60:
        return
    suffix = f"{_format_hours(prep['hours'])} {prep['label']}"
    entry["time"] = f"{time_str} plus {suffix}" if time_str else suffix


def buy_ahead_days(recipe: dict) -> int:
    """Days before the cook date the ingredients are needed (0 = same day)."""
    hours = float(recipe.get("advance_prep_hours") or 0)
    return 1 if hours >= BUY_DAY_BEFORE_HOURS else 0


def day_before_note(title: str, recipe: dict) -> str:
    """"Start Miso Chicken tonight (needs 24 hours marinating)" for a recipe whose
    required wait means it has to be started the day before; "" otherwise."""
    if not buy_ahead_days(recipe):
        return ""
    m = re.search(r"\bplus\s+(.+)$", recipe.get("time") or "", re.IGNORECASE)
    what = m.group(1).strip() if m else f"{_format_hours(float(recipe['advance_prep_hours']))} of prep"
    return f"Start {title} tonight (needs {what})"


def is_quick_blocked(recipe: dict) -> bool:
    """True when a required wait means the recipe can't be a quick-night pick."""
    return float(recipe.get("advance_prep_hours") or 0) >= ADVANCE_PREP_MIN_HOURS


# ---------------------------------------------------------------------------
# 2. Sub-recipes
# ---------------------------------------------------------------------------

def _fetch(url: str) -> Optional[str]:
    try:
        resp = httpx.get(url, headers=_FETCH_HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def _ld_recipe(html: str) -> Optional[dict]:
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in (data if isinstance(data, list) else data.get("@graph", [data])):
            t = item.get("@type", "") if isinstance(item, dict) else ""
            if "Recipe" in (t if isinstance(t, list) else [t]):
                return item
    return None


def detect_sub_recipes(source_url: str) -> list[dict]:
    """Recipes the source page's ingredient list links to (same site only).

    Each linked page is fetched and kept only if it carries Recipe ld+json, so
    glossary links ("neutral oil", "sesame seeds") are dropped, and only if
    its ingredient line makes it required (see _requires_sub_recipe), so
    "corn tortillas (store-bought or homemade)" doesn't drag a tortilla
    recipe onto the shopping list. Returns [{"name", "url", "line", "ld"}].
    """
    if not source_url:
        return []
    html = _fetch(source_url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(source_url).netloc
    own = source_url.rstrip("/")
    links: dict[str, str] = {}  # href -> ingredient line text
    for sel in _INGREDIENT_LINK_SELECTORS:
        for a in soup.select(sel):
            href = urljoin(source_url, a.get("href", "")).split("#")[0].rstrip("/")
            row = a.find_parent("li") or a.parent
            line = row.get_text(" ", strip=True) if row else a.get_text(strip=True)
            if urlparse(href).netloc == host and href != own and href not in links \
                    and _requires_sub_recipe(line):
                links[href] = line
    found = []
    for href, line in list(links.items())[:_MAX_LINKS]:
        page = _fetch(href)
        ld = _ld_recipe(page) if page else None
        if ld and ld.get("name") and ld.get("recipeIngredient"):
            found.append({"name": ld["name"].strip(), "url": href + "/", "line": line, "ld": ld})
    return found


_HOMEMADE_RE = re.compile(r"\bhomemade\b", re.IGNORECASE)
_SUB_RECIPE_RE = re.compile(r"\bhomemade\b|\brecipe\b|\bbatch\b", re.IGNORECASE)
_HAS_ALTERNATIVE_RE = re.compile(r"store[- ]?bought|\bor\b|preferabl|optional|substitut", re.IGNORECASE)


def _requires_sub_recipe(line: str) -> bool:
    """Ingredient line says to make it ("Homemade X", "1 batch X") with no buy-it option."""
    return bool(_SUB_RECIPE_RE.search(line)) and not _HAS_ALTERNATIVE_RE.search(line)


def unresolved_homemade(ingredients_raw: list, deps: list) -> list[str]:
    """'Homemade X' lines with no store-bought alternative and no linked sub-recipe."""
    out = []
    for line in ingredients_raw or []:
        if _HOMEMADE_RE.search(line) and not _HAS_ALTERNATIVE_RE.search(line):
            if not any(d.lower() in line.lower() for d in deps):
                out.append(line)
    return out


_COND_PROMPT = """Turn this sauce/sub-recipe into structured data.

Title: {title}
Yield: {yield_}
Ingredients:
{ingredients}
Instructions:
{instructions}

Rules:
- ingredients: one entry per ingredient, quantity as written (fractions ok),
  unit lowercased ("cup", "tbsp", "tsp", "" if none). category is one of
  Proteins, Produce, Dairy, Pantry/Asian, Dry Goods, Spices/Herbs.
- instructions: reword each step concisely in your own words; drop "gather
  the ingredients" steps; keep storage guidance as the last step.
- type: sauce, rub, marinade, dressing, paste, condiment, or other.
"""

_COND_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},
        "servings": {"type": "string"},
        "ingredients": {"type": "array", "items": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in ("name", "quantity", "unit", "category")},
            "required": ["name", "quantity", "unit", "category"],
            "additionalProperties": False,
        }},
        "instructions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["type", "servings", "ingredients", "instructions"],
    "additionalProperties": False,
}


def _iso_minutes(iso: str) -> str:
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", iso or "")
    if not m or not any(m.groups()):
        return ""
    total = int(m.group(1) or 0) * 1440 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)
    return f"{total} minutes" if total < 120 else f"{total // 60} hours {total % 60} minutes".replace(" 0 minutes", "")


def condiment_page_url(name: str) -> str:
    return f"{PAGES_BASE}/{_filename(name)[:-3]}"


def _filename(name: str) -> str:
    return re.sub(r"[^\w\-]", "", name.replace(" ", "_")) + ".md"


def _condiment_md(c: dict) -> str:
    stem = _filename(c["name"])[:-3]
    lines = ["---", f"permalink: /{stem}", "---", f"# {c['name']}", ""]
    if c.get("time") or c.get("servings"):
        if c.get("time"):
            lines.append(f"**Time**: {c['time']}  ")
        if c.get("servings"):
            lines.append(f"**Yield**: {c['servings']}")
        lines.append("")
    lines += ["## Ingredients", ""]
    for i in c["ingredients"]:
        lines.append("- " + " ".join(p for p in (i["quantity"], i["unit"], i["name"]) if p))
    lines += ["", "## Instructions", ""]
    lines += [f"{n}. {s}" for n, s in enumerate(c["instructions"], 1)]
    if c.get("source_url"):
        lines += ["", f"*Adapted from [{c.get('source') or 'source'}]({c['source_url']})*"]
    lines.append("")
    return "\n".join(lines)


def ensure_condiment(sub: dict, source_name: str, client=None) -> Optional[tuple[str, Optional[Path]]]:
    """Make sure `sub` is in condiments.json. Returns (name, new_md_path or None).

    An existing condiment (same name or same source_url) is reused as-is.
    A new one needs full structured data -- if the LLM step fails we return
    None rather than write a stub (JSON invariant: no naked entries).
    """
    condiments = json.loads(CONDIMENTS_PATH.read_text()) if CONDIMENTS_PATH.exists() else {}
    for name, c in condiments.items():
        if name.lower() == sub["name"].lower() or (c.get("source_url") or "").rstrip("/") == sub["url"].rstrip("/"):
            return name, None

    ld = sub["ld"]
    steps = [(s.get("text", "") if isinstance(s, dict) else str(s)).strip()
             for s in ld.get("recipeInstructions", [])]
    data = _structured(_COND_PROMPT.format(
        title=sub["name"],
        yield_=ld.get("recipeYield", ""),
        ingredients="\n".join(f"- {i}" for i in ld.get("recipeIngredient", [])),
        instructions="\n".join(f"{n}. {s}" for n, s in enumerate(steps, 1) if s),
    ), _COND_SCHEMA, client)
    if not data or not data["ingredients"] or not data["instructions"]:
        return None

    entry = {
        "name": sub["name"],
        "type": data["type"],
        "source": source_name,
        "source_url": sub["url"],
        "servings": data["servings"],
        "time": _iso_minutes(ld.get("totalTime") or ld.get("cookTime") or ""),
        "ingredients": data["ingredients"],
        "instructions": data["instructions"],
        "notes": "",
        "image": None,
    }
    condiments[sub["name"]] = entry
    CONDIMENTS_PATH.write_text(json.dumps(condiments, indent=2))
    CONDIMENTS_DIR.mkdir(exist_ok=True)
    md_path = CONDIMENTS_DIR / _filename(sub["name"])
    md_path.write_text(_condiment_md(entry))
    return sub["name"], md_path


# ---------------------------------------------------------------------------
# 3. Chicken cut
# ---------------------------------------------------------------------------

_CHICKEN_CUT_RE = re.compile(r"\bchicken\b.*\b(breasts?|thighs?)\b|\b(breasts?|thighs?)\b.*\bchicken\b", re.IGNORECASE)
_CUT_RE = re.compile(r"\b(breast|thigh)s?\b", re.IGNORECASE)
_CUT_MATTERS_RE = re.compile(r"bone[- ]in|skin[- ]on|with skin|\bsplit\b|whole chicken|drumstick|\bwings?\b|"
                             r"\bleg quarters?\b|tenderloins?|cutlets?|\bground\b|\bminced\b|cut[- ]up",
                             re.IGNORECASE)
SWAPPED_CHICKEN_NAME = "boneless, skinless chicken breasts or thighs"


def _cut_swap_candidate(title: str, ingredients_raw: list) -> Optional[int]:
    """Index of the single chicken line when the recipe uses exactly one boneless
    cut and its title doesn't name the cut; None otherwise (no LLM call)."""
    if _CUT_RE.search(title or ""):
        return None
    lines = [(i, s) for i, s in enumerate(ingredients_raw or []) if _CHICKEN_CUT_RE.search(s or "")]
    if len(lines) != 1:
        return None
    i, line = lines[0]
    cuts = {m.lower() for m in _CUT_RE.findall(line)}
    if len(cuts) != 1 or _CUT_MATTERS_RE.search(line):
        return None
    return i


_CUT_PROMPT = """Recipe: {title}

Ingredients:
{ingredients}

Instructions:
{instructions}

This recipe calls for one cut of boneless chicken (line [{index}]). Decide
whether boneless, skinless breasts and thighs are interchangeable here, and if
so rewrite it so the cook can use either.

NOT swappable (return swappable false, no changes) when the cut matters:
- the technique depends on it: butterflied, pounded thin, stuffed, rolled,
  breaded cutlets, schnitzel/katsu-style;
- a long braise, stew or slow-cooker cook (over ~30 minutes in liquid) where
  breasts would dry out;
- the recipe or notes explain why that cut was chosen.

When swappable:
- new_ingredient: rewrite line [{index}] to list the original cut first, then
  the other, at the same total weight. If the original is a count ("4 (6- to
  8-ounce) breasts"), give the other cut by weight, e.g. "4 (6- to 8-ounce)
  boneless, skinless chicken breasts or 1 1/2 to 2 pounds boneless, skinless
  chicken thighs, trimmed". Keep prep notes ("trimmed", "cut into 1-inch
  pieces").
- shop_quantity / shop_unit: total amount to buy, by weight ("1.5", "pounds").
- step_changes: only the steps that must change, each with its full rewritten
  text; keep every other word of those steps as written. Give both cuts'
  doneness and timing where the recipe gives one: breasts 160F, thighs 175F,
  thighs a few minutes longer -- e.g. "until registering 160 degrees for
  breasts, about 6 minutes per side, or 175 degrees for thighs, about 7 to 8
  minutes per side". Keep the recipe's own temperature for its original cut.
- Marinating: if the chicken marinates in something acidic (citrus, vinegar,
  wine, yogurt, buttermilk) or enzymatic (pineapple, papaya, kiwi), breasts
  turn mushy sooner than thighs -- give a time for each cut, e.g. "Marinate
  breasts 30 minutes to 2 hours, thighs up to 8 hours." Never raise the
  minimum marinating time. For non-acidic marinades (soy, miso, oil, herbs,
  spices) leave the time as written.
- Index fields are the bracketed numbers above.
"""

_CUT_SCHEMA = {
    "type": "object",
    "properties": {
        "swappable": {"type": "boolean"},
        "reason": {"type": "string"},
        "new_ingredient": {"type": "string"},
        "shop_quantity": {"type": "string"},
        "shop_unit": {"type": "string"},
        "step_changes": {"type": "array", "items": {
            "type": "object",
            "properties": {"index": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["index", "text"],
            "additionalProperties": False,
        }},
    },
    "required": ["swappable", "reason", "new_ingredient", "shop_quantity", "shop_unit", "step_changes"],
    "additionalProperties": False,
}


def detect_cut_swap(title: str, ingredients_raw: list, instructions: list, client=None) -> Optional[dict]:
    """The model's verdict for a single-cut boneless chicken recipe, with
    `index` (the chicken line) added; None when the recipe isn't a candidate or
    the call fails. Check result["swappable"] before applying."""
    index = _cut_swap_candidate(title, ingredients_raw)
    if index is None or not instructions:
        return None
    result = _structured(_CUT_PROMPT.format(
        title=title, index=index,
        ingredients="\n".join(f"[{i}] {s}" for i, s in enumerate(ingredients_raw)),
        instructions="\n".join(f"[{i}] {s}" for i, s in enumerate(instructions)),
    ), _CUT_SCHEMA, client, effort="medium")
    if result is None:
        return None
    result["index"] = index
    return result


def apply_cut_swap(entry: dict, swap: Optional[dict]) -> bool:
    """Write a swappable verdict into the entry's ingredients_raw, instructions
    and structured `ingredients` (the shopping-list name). All or nothing: a
    verdict that doesn't hold together (bad index, a line that no longer names
    both cuts) changes nothing. Returns True when applied."""
    if not swap or not swap.get("swappable") or entry.get("cut_locked"):
        return False
    raw = list(entry.get("ingredients_raw") or [])
    steps = list(entry.get("instructions") or [])
    idx, line = swap.get("index"), (swap.get("new_ingredient") or "").strip()
    if not isinstance(idx, int) or not 0 <= idx < len(raw) or {m.lower() for m in _CUT_RE.findall(line)} != {"breast", "thigh"}:
        return False
    changes = swap.get("step_changes") or []
    if any(not 0 <= c.get("index", -1) < len(steps) or not (c.get("text") or "").strip() for c in changes):
        return False
    raw[idx] = line
    for c in changes:
        steps[c["index"]] = c["text"].strip()
    entry["ingredients_raw"], entry["instructions"] = raw, steps
    for ing in entry.get("ingredients") or []:
        name = ing.get("name", "") if isinstance(ing, dict) else ""
        if re.search(r"\bchicken\b", name, re.IGNORECASE) and _CUT_RE.search(name):
            ing.update(name=SWAPPED_CHICKEN_NAME, quantity=swap.get("shop_quantity", ""),
                       unit=swap.get("shop_unit", ""))
            break
    return True


# ---------------------------------------------------------------------------
# Entry point for intake paths
# ---------------------------------------------------------------------------

def run_intake_checks(title: str, entry: dict, source_url: str = "", source_name: str = "") -> list[Path]:
    """Mutate a new recipe entry in place before it's written. Never raises.

    Sets `time` (+ required wait), `advance_prep_hours`, `condiment_deps`, and
    `intake_warnings` for "homemade X" lines it couldn't resolve (a note, not
    needs_review -- most turn out to be buyable, e.g. hummus). May rewrite
    `ingredients_raw`, `instructions` and `ingredients` for the chicken cut --
    callers must write the .md and entry from these fields afterwards. Returns
    new condiment .md paths the caller should publish.
    """
    new_mds: list[Path] = []
    try:
        # Before advance prep, so the wait is read from the final marinade wording.
        apply_cut_swap(entry, detect_cut_swap(title, entry.get("ingredients_raw", []),
                                              entry.get("instructions", [])))
    except Exception:
        pass
    try:
        apply_advance_prep(entry, detect_advance_prep(title, entry.get("instructions", [])))
    except Exception:
        pass
    try:
        deps = list(entry.get("condiment_deps") or [])
        for sub in detect_sub_recipes(source_url):
            got = ensure_condiment(sub, source_name)
            if got:
                name, md = got
                if name not in deps:
                    deps.append(name)
                if md:
                    new_mds.append(md)
        if deps:
            entry["condiment_deps"] = deps
        missing = unresolved_homemade(entry.get("ingredients_raw", []), deps)
        if missing:
            entry["intake_warnings"] = [f"Homemade sub-recipe not found: {m}" for m in missing]
    except Exception:
        pass
    return new_mds


def condiment_links(entry: dict) -> dict:
    """{name: page_url} for an entry's condiment_deps -- feed to build_recipe_md."""
    return {name: condiment_page_url(name) for name in entry.get("condiment_deps") or []}
