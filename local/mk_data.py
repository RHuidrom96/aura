import json
import random
from pathlib import Path
import pandas as pd

BASE_DIR = Path("off_the_shelf_translations")
OUTPUT_DIR = Path("data/segments")

SEED = 2026
SHUFFLE_SYSTEMS = False  # set False to keep fixed order
KEEP_SYSTEM_LABELS = True  # keep original system names in extra columns

FILES = {
    "claude": BASE_DIR / "results_Claude.json",
    "openai": BASE_DIR / "results_OpenAI.json",
    "gemini": BASE_DIR / "results_Gemini.json",
}

LANGUAGE_PREFIXES = {
    "mni": "manipuri",
    "asm": "assamese",
    "nag": "nagamese",
}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_id(full_id):
    parts = full_id.split("_")
    if len(parts) >= 3:
        return f"{parts[0]}_{parts[-1]}"
    return full_id


def get_language_prefix(row_id):
    return row_id.split("_")[0]


def shuffled_system_order(seed, row_id):
    systems = ["claude", "openai", "gemini"]
    rng = random.Random(f"{seed}_{row_id}")
    rng.shuffle(systems)
    return systems


def build_rows_for_language(lang_prefix, data_maps):
    maps = {k: {} for k in data_maps}
    for sys_name, data in data_maps.items():
        maps[sys_name] = {
            normalize_id(item["id"]): item
            for item in data
            if normalize_id(item["id"]).startswith(lang_prefix + "_")
        }

    common_ids = sorted(set(maps["claude"]) & set(maps["openai"]) & set(maps["gemini"]))

    rows = []
    for rid in common_ids:
        if SHUFFLE_SYSTEMS:
            order = shuffled_system_order(SEED, rid)
        else:
            order = ["claude", "openai", "gemini"]

        item_a = maps[order[0]][rid]
        item_b = maps[order[1]][rid]
        item_c = maps[order[2]][rid]

        rows.append(
            {
                "id": rid,
                "source": item_a.get(
                    "source", item_b.get("source", item_c.get("source", ""))
                ),
                "target_a": item_a.get("target", ""),
                "target_b": item_b.get("target", ""),
                "target_c": item_c.get("target", ""),
                "system_a": order[0],
                "system_b": order[1],
                "system_c": order[2],
                "system_a_label": item_a.get("system", ""),
                "system_b_label": item_b.get("system", ""),
                "system_c_label": item_c.get("system", ""),
                "domain": item_a.get(
                    "domain", item_b.get("domain", item_c.get("domain", ""))
                ),
            }
        )

    return rows


def main():
    data_maps = {name: load_json(path) for name, path in FILES.items()}

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for lang_prefix, lang_name in LANGUAGE_PREFIXES.items():
        rows = build_rows_for_language(lang_prefix, data_maps)
        df = pd.DataFrame(rows)
        out_path = OUTPUT_DIR / f"{lang_name}_pairwise.csv"
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"Saved {len(df)} rows to {out_path}")


if __name__ == "__main__":
    main()
