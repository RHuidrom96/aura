import json
import random
from pathlib import Path
import pandas as pd

BASE_DIR = Path("off_the_shelf_translations")
OUTPUT_DIR = Path("data/segments")

SEED = 2026
SHUFFLE_SYSTEMS = False
KEEP_SYSTEM_LABELS = True

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


def get_system_order(row_id, shuffle_systems=False, seed=2026):
    if shuffle_systems:
        return shuffled_system_order(seed, row_id)
    return ["claude", "openai", "gemini"]


def build_row_pairwise(rid, ordered_items, ordered_systems, keep_system_labels=True):
    item_a, item_b, item_c = ordered_items

    row = {
        "id": rid,
        "source": item_a.get("source", item_b.get("source", item_c.get("source", ""))),
        "target_a": item_a.get("target", ""),
        "target_b": item_b.get("target", ""),
        "target_c": item_c.get("target", ""),
        "system_a": ordered_systems[0],
        "system_b": ordered_systems[1],
        "system_c": ordered_systems[2],
        "domain": item_a.get("domain", item_b.get("domain", item_c.get("domain", ""))),
    }

    if keep_system_labels:
        row.update(
            {
                "system_a_label": item_a.get("system", ""),
                "system_b_label": item_b.get("system", ""),
                "system_c_label": item_c.get("system", ""),
            }
        )

    return row


def build_row_candidates(rid, ordered_items, ordered_systems, keep_system_labels=True):
    source = ""
    domain = ""

    for item in ordered_items:
        if not source:
            source = item.get("source", "")
        if not domain:
            domain = item.get("domain", "")

    candidates = []
    for sys_name, item in zip(ordered_systems, ordered_items):
        candidate = {
            "target": item.get("target", ""),
            "system": item.get("system", sys_name) if keep_system_labels else sys_name,
        }
        candidates.append(candidate)

    row = {
        "id": rid,
        "source": source,
        "candidates": json.dumps(candidates, ensure_ascii=False),
    }

    if domain:
        row["domain"] = domain

    return row


def build_rows_for_language(
    lang_prefix,
    data_maps,
    output_format="pairwise",
    shuffle_systems=False,
    seed=2026,
    keep_system_labels=True,
):
    maps = {}
    for sys_name, data in data_maps.items():
        maps[sys_name] = {
            normalize_id(item["id"]): item
            for item in data
            if normalize_id(item["id"]).startswith(lang_prefix + "_")
        }

    common_ids = sorted(set.intersection(*(set(m.keys()) for m in maps.values())))

    rows = []
    for rid in common_ids:
        ordered_systems = get_system_order(
            row_id=rid,
            shuffle_systems=shuffle_systems,
            seed=seed,
        )
        ordered_items = [maps[sys_name][rid] for sys_name in ordered_systems]

        if output_format == "pairwise":
            row = build_row_pairwise(
                rid,
                ordered_items,
                ordered_systems,
                keep_system_labels=keep_system_labels,
            )
        elif output_format == "candidates":
            row = build_row_candidates(
                rid,
                ordered_items,
                ordered_systems,
                keep_system_labels=keep_system_labels,
            )
        else:
            raise ValueError("output_format must be either 'pairwise' or 'candidates'")

        rows.append(row)

    return rows


def export_language_csvs(
    files=FILES,
    language_prefixes=LANGUAGE_PREFIXES,
    output_dir=OUTPUT_DIR,
    output_format="pairwise",
    shuffle_systems=SHUFFLE_SYSTEMS,
    seed=SEED,
    keep_system_labels=KEEP_SYSTEM_LABELS,
    encoding="utf-8-sig",
):
    data_maps = {name: load_json(path) for name, path in files.items()}
    output_dir.mkdir(parents=True, exist_ok=True)

    for lang_prefix, lang_name in language_prefixes.items():
        rows = build_rows_for_language(
            lang_prefix=lang_prefix,
            data_maps=data_maps,
            output_format=output_format,
            shuffle_systems=shuffle_systems,
            seed=seed,
            keep_system_labels=keep_system_labels,
        )

        df = pd.DataFrame(rows)
        out_path = output_dir / f"{lang_name}_{output_format}.csv"
        df.to_csv(out_path, index=False, encoding=encoding)
        print(f"Saved {len(df)} rows to {out_path}")


def main():
    export_language_csvs(output_format="pairwise")
    export_language_csvs(output_format="candidates")


if __name__ == "__main__":
    main()
