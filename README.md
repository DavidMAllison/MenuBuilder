# MenuBuilder

A personal meal planning system built around real family constraints — health goals, picky kids, busy weeknights, and a rotating recipe library. Designed to reduce the weekly "what's for dinner" decision fatigue while hitting dietary targets consistently.

## What It Does

- **Weekly meal planning**: Proposes 7 dinners tailored to the week's schedule, health balance, and what's already in the fridge
- **Recipe candidate scoring**: Filters recipes by recency, health classification, protein variety, cuisine variety, and seasonal cooking method
- **Shopping list generation**: Aggregates ingredients across the week's plan into a structured CSV, auto-imported into iOS Reminders via a Mac app
- **Calendar integration**: Adds dinner events to iCloud Calendar with cook times and recipe links, built as a Mac app
- **Feedback loop**: Tracks which meals the family liked, surfaces family-favorite signals in future candidate scoring
- **SMS assistant**: Companion WhatsApp bot for querying recipes, meal plans, and inventory from a phone (separate project)

## Design Decisions

**JSON as single source of truth.** All recipe metadata lives in `recipe_metadata.json` — health classification, cuisine type, cook time, cooking method, times cooked, last cooked date, and a structured ingredients array. PDFs are recipe content only (no metadata). This lets the planning system work entirely from the JSON without re-parsing PDFs on every run.

**Scoring over hard rules.** `suggest_meals.py` produces a ranked candidate list rather than making the final selection. A scoring function penalizes recently-cooked meals, overused recipes, and indulgent options while rewarding heart-healthy choices, family favorites, and in-season grill meals. The human makes the final call from the ranked list.

**Constraint-driven health balance.** The family has specific dietary goals (lowering LDL, reducing blood pressure). Rather than tracking macros, the system classifies each recipe as Heart-Healthy / Moderate / Indulgent and enforces a weekly ratio (5-6 heart-healthy, max 1 indulgent). Simple enough to maintain, effective enough to shift eating patterns.

**Kids-friendly layered cooking.** Recipes are structured where possible so kids' plain portions can be pulled before adding adult sauces — one meal, two outcomes, no separate cooking.

**Claude Code as the planning interface.** The AI assistant handles the conversational workflow — logging last week's meals, checking the schedule, running the candidate script, proposing options, generating the plan file, and triggering the Mac apps. The Python tooling handles deterministic filtering and file I/O.

## Repository Structure

```
suggest_meals.py        # Candidate meal filter — run before each weekly plan
recipe_metadata.json    # (not committed) Single source of truth for all recipe data
CLAUDE.md               # AI assistant context and workflow instructions
backlog.md              # Planned features
release-notes.md        # Shipped features log
```

## Usage

```bash
# Basic candidate list
python3 suggest_meals.py

# Flag nights with early practices (needs quick meals)
python3 suggest_meals.py --quick mon,tue,thu

# Override week start date
python3 suggest_meals.py --week 2026-04-21
```

Copy `config.example.json` to `config.json` and update the values for your setup:

```bash
cp config.example.json config.json
```

```json
{
  "metadata_path": "~/path/to/recipe_metadata.json",
  "adult_names": ["Parent1", "Parent2"]
}
```

## Mac Apps

- **WeeklyShoppingList.app** — reads `shopping_YYYY-MM-DD.csv` and populates the iOS Grocery Reminders list (syncs to family members)
- **WeeklyMealCalendar.app** — reads the week's meal plan and adds dinner events to iCloud Calendar with cook times and recipe links

Both run via `open /Applications/<AppName>.app` at the end of each planning session.

## Recipe Metadata Schema

```json
{
  "Recipe Name": {
    "source": "America's Test Kitchen",
    "cuisine": "Italian",
    "meal_type": "Weeknight",
    "health": "Heart-Healthy",
    "cooking_method": "stovetop",
    "time": "40 min",
    "times_cooked": 4,
    "last_cooked_date": "2026-03-15",
    "status": "active",
    "ingredients": [
      {"name": "chicken breast", "quantity": "1.5", "unit": "lbs", "category": "Proteins"}
    ],
    "feedback": [
      {"person": "adult", "sentiment": "liked", "note": ""}
    ]
  }
}
```

## Status Fields

| Status | Meaning |
|---|---|
| `active` | In rotation, PDF exists |
| `idea` | Staged for trial, not yet tried |
| `disliked` | Tried, didn't work — kept as tombstone |
