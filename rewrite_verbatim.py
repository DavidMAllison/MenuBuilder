#!/usr/bin/env python3
"""Batch rewrite Instructions sections of verbatim-copied recipe files using Claude Haiku."""

import re
import subprocess
import time
from pathlib import Path

RECIPES_DIR = Path.home() / "Dropbox/LLMContext/cooking/recipes"

TARGET_FILES = [
    # patijinich.com
    "Amarillito_Mole_with_Chicken.md",
    "Chicken_in_a_Pecan_and_Ancho_Chile_Sauce.md",
    "Enchiladas_Suizas_Sanborns_Swiss_Chicken_Enchiladas.md",
    "Dressed-up_Chicken_Milanesa.md",
    "Garlic_and_Cumin_Rubbed_Chicken.md",
    "Red_Pozole_with_Traditional_Garnishes.md",
    "Black_Bean_Puree.md",
    "Colado_Black_Beans.md",
    "Black_Beans_from_the_Pot.md",
    "Lamb_Barbacoa_Mixiote.md",
    # thewoksoflife.com
    "Moo_Shu_Chicken.md",
    "Cantonese_Steamed_Fish.md",
    "Chicken_and_Soft_Tofu_Casserole.md",
    "Moo_Goo_Gai_Pan_Mushroom_and_Chicken_Stir-Fry.md",
    "Cauliflower_Stir-fry_with_Beef.md",
    "Shanghai-Style_Braised_Pork_Belly_Hong_Shao_Rou.md",
    "Braised_Pork_Belly_Dong_Po_Rou.md",
    # altonbrown.com
    "Bi-Level_King_Salmon_Fillet.md",
    "Broiled_Salmon_with_ABs_Spice_Pomade.md",
    "Hot_Smoked_Salmon.md",
    "Lamb_Shoulder_Chops_with_Red_Wine.md",
    "Barley_and_Lamb_Stew.md",
    "Chuanr_Grilled_Lamb_Skewers.md",
    # chetnamakan.co.uk
    "One-Pan_Fish_Broccoli_Curry.md",
    "Onion_Masala_Chicken.md",
    "Fish_Curry.md",
    "Salmon_and_Dill_Blini.md",
    # rickbayless.com
    "Crispy_Chicken_Thighs_with_Creamy_Jalapeño_Salsa.md",
    "Yucatecan_Black_Bean_Dinner.md",
    "Red_Pozole.md",
    "Slow-Cooker_Red_Chile_Pozole.md",
    "Quick_Pozole.md",
    # indianhealthyrecipes.com
    "Chicken_Bhuna_Masala_Recipe.md",
    "Rajma_Recipe_Rajma_Masala.md",
    "Nihari_Recipe.md",
    "Chicken_Tikka_Recipe_Tandoori_Tikka_Kabab.md",
    # archanaskitchen.com
    "Kashmiri_Style_Rajma_Gogji_Recipe_-_Kidney_Beans_Turnip_Curry.md",
    "Nihari_Gosht_Recipe.md",
    "Awadhi_Khaas_Nihari_Recipe.md",
    # maangchi.com
    "Kimchi_sundubu-jjigae_김치순두부찌개_Spicy_soft_tofu_stew_with_kimchi.md",
    "Haemul_sundubu-jjigae_Spicy_soft_tofu_stew_with_seafood.md",
    "Spicy_beef_bulgogi_Maeun-sobulgogi_매운소불고기.md",
    # ranveerbrar.com
    "Mom8217s_Style_Rajma_Chawal_Maa_ki_Baat_8211_Episode_1.md",
    "Kashmiri_Rajma_Curry.md",
    # vietworldkitchen.com
    "Red_Boat_Pork_Belly_in_Caramel_Sauce.md",
    "Vietnamese_Restaurant-Style_Grilled_Lemongrass_Pork_Thịt_Heo_Nướng_Sả.md",
    "Char_Siu_Pork_Skewers.md",
    # justonecookbook.com
    "Kakuni_Japanese_Braised_Pork_Belly.md",
    "Rafute.md",
    # seriouseats.com
    "Crispy_Braised_Chicken_Thighs_with_Cabbage_and_Bacon.md",
    "Lemon-Pepper_Chicken.md",
    "One-Pan_Chicken_and_Rice_With_Preserved_Lemon_and_Cilantro.md",
    # others
    "Chicken_Cacciatore.md",
    "Braciole_di_maiale_alla_brace_Grilled_Pork_Chops.md",
    "Ultimate_Thai_BBQ_Chicken_ไกยาง_gai_yang.md",
]

REWRITE_PROMPT = (
    "Rewrite these recipe instructions in your own words. "
    "Preserve every step, technique, temperature, timing, and quantity exactly. "
    "Change the sentence structure and phrasing so it reads as an original adaptation rather than a copy. "
    "Do not add or remove any steps. Do not change any measurements, temperatures, or cooking times. "
    "Output only the rewritten instructions as a numbered list, no preamble.\n\n"
)


def extract_instructions(content: str) -> tuple[str, int, int]:
    """Return (instructions_text, start_line_idx, end_line_idx) or raises ValueError."""
    lines = content.split("\n")
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^##\s+Instructions", line, re.IGNORECASE):
            start = i
            break
    if start is None:
        raise ValueError("No ## Instructions section found")
    # Find end: next ## heading or end of file
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^##\s+", lines[i]):
            end = i
            break
    instructions = "\n".join(lines[start + 1 : end]).strip()
    return instructions, start, end


def replace_instructions(content: str, new_instructions: str) -> str:
    lines = content.split("\n")
    _, start, end = extract_instructions(content)
    # Find the heading line
    heading_line = lines[start]
    new_lines = lines[: start + 1] + ["", new_instructions, ""] + lines[end:]
    return "\n".join(new_lines)


CLAUDE_CLI = "/Users/davidallison/.local/bin/claude"


def rewrite_instructions(instructions: str) -> str:
    prompt = REWRITE_PROMPT + instructions
    result = subprocess.run(
        [CLAUDE_CLI, "-p", "--model", "claude-haiku-4-5-20251001"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "CLI returned non-zero")
    return result.stdout.strip()


def main():
    results = {"ok": [], "not_found": [], "no_instructions": [], "error": []}
    samples = []

    for filename in TARGET_FILES:
        path = RECIPES_DIR / filename
        if not path.exists():
            print(f"  NOT FOUND: {filename}")
            results["not_found"].append(filename)
            continue

        content = path.read_text(encoding="utf-8")
        try:
            original_instructions, _, _ = extract_instructions(content)
        except ValueError as e:
            print(f"  NO INSTRUCTIONS: {filename} — {e}")
            results["no_instructions"].append(filename)
            continue

        print(f"  Rewriting: {filename} ...", end="", flush=True)
        try:
            new_instructions = rewrite_instructions(original_instructions)
            new_content = replace_instructions(content, new_instructions)
            path.write_text(new_content, encoding="utf-8")
            results["ok"].append(filename)
            print(" done")
            if len(samples) < 3:
                samples.append({
                    "file": filename,
                    "before": original_instructions[:400],
                    "after": new_instructions[:400],
                })
            time.sleep(0.2)  # light rate-limit breathing room
        except Exception as e:
            print(f" ERROR: {e}")
            results["error"].append((filename, str(e)))

    print("\n=== RESULTS ===")
    print(f"Rewritten:       {len(results['ok'])}")
    print(f"Not found:       {len(results['not_found'])}")
    print(f"No instructions: {len(results['no_instructions'])}")
    print(f"Errors:          {len(results['error'])}")

    if results["not_found"]:
        print("\nNot found:", results["not_found"])
    if results["error"]:
        print("\nErrors:", results["error"])

    print("\n=== SAMPLES (before → after) ===")
    for s in samples:
        print(f"\n--- {s['file']} ---")
        print("BEFORE:", s["before"])
        print("AFTER: ", s["after"])


if __name__ == "__main__":
    main()
