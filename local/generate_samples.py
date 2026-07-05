from pathlib import Path
import pandas as pd
import numpy as np
import re
import json

# ============================================================
# Reproducible human-eval sample selection for WMT CSV files
# Assamese + Meitei Mayek + Nagamese only
# Also stores statistics and JSON outputs
# ============================================================


SEED = 2026
N_SAMPLES = 30

TEST_ROOT = Path("wmt")
OUT_DIR = Path("samples/human_eval_sampling")
OUT_DIR.mkdir(parents=True, exist_ok=True)

TEST_FILES = {
    "assamese": TEST_ROOT / "wmt2026_train_engLatn_asmBeng.csv",
    "mni_mtei": TEST_ROOT / "wmt2026_train_engLatn_mniMtei.csv",
    "nagamese": TEST_ROOT / "wmt2026_train_engLatn_nagLatn.csv",
}

COL_HINTS = {
    "assamese": [
        ("en", "as"),
        ("english", "assamese"),
        ("eng", "asm"),
        ("source", "target"),
    ],
    "mni_mtei": [
        ("eng-mtei-eng-train", "eng-mtei-mtei-train"),
        ("eng", "mtei"),
        ("source", "target"),
    ],
    "nagamese": [
        ("eng", "nag"),
        ("english", "nagamese"),
        ("source", "target"),
    ],
}


def normalize_text(s):
    if pd.isna(s):
        return ""
    s = str(s)
    s = s.replace("\u00a0", " ")  # non-breaking space
    s = s.replace(
        "\xa0", " "
    )  # hexadecimal escape sequence for a non-breaking space [nag]
    s = s.replace("\u200b", " ")  # zero-width space
    s = s.replace("\u200c", " ")  # Zero-Width Non-Joiner (ZWNJ) [mni]
    s = s.replace("\u200d", " ")  # zero Width Joiner (ZW) [mni]
    s = s.replace("\ufeff", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_for_dedup(s):
    s = normalize_text(s).lower()
    s = re.sub(r"[\"'“”‘’`]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def word_count(s):
    s = normalize_text(s)
    if not s:
        return 0
    return len(s.split())


def punctuation_ratio(s):
    s = normalize_text(s)
    if not s:
        return 1.0
    punct = sum(1 for ch in s if re.match(r"[^\w\s]", ch))
    return punct / max(len(s), 1)


def digit_ratio(s):
    s = normalize_text(s)
    if not s:
        return 1.0
    digits = sum(ch.isdigit() for ch in s)
    return digits / max(len(s), 1)


def has_url_or_email(s):
    s = normalize_text(s).lower()
    return "http://" in s or "https://" in s or "www." in s or "@" in s


def read_test_file(path, lang_key):
    df = pd.read_csv(path, dtype=str, engine="python", on_bad_lines="skip")
    df.columns = [str(c).strip() for c in df.columns]

    print(f"[DEBUG] {path.name} columns: {list(df.columns)}")
    print(f"[DEBUG] {path.name} shape: {df.shape}")

    lower_map = {c.lower(): c for c in df.columns}
    chosen = None

    for a, b in COL_HINTS.get(lang_key, []):
        if a.lower() in lower_map and b.lower() in lower_map:
            chosen = (lower_map[a.lower()], lower_map[b.lower()])
            break

    if chosen is None:
        if len(df.columns) < 2:
            raise ValueError(f"Need at least 2 columns in {path}")
        chosen = (df.columns[0], df.columns[1])

    src_col, tgt_col = chosen
    print(f"[DEBUG] {path.name} selected columns: src={src_col}, tgt={tgt_col}")

    out = pd.DataFrame(
        {
            "src_text": df[src_col].fillna("").astype(str).map(normalize_text),
            "tgt_text": df[tgt_col].fillna("").astype(str).map(normalize_text),
        }
    )

    out = out[(out["src_text"] != "") & (out["tgt_text"] != "")].copy()
    out.insert(0, "original_row_id", range(len(out)))
    return out, src_col, tgt_col


def make_stats_dict(df, prefix):
    stats = {f"{prefix}_rows": int(len(df))}
    if len(df) == 0:
        return stats

    if "src_word_count" in df.columns:
        stats.update(
            {
                f"{prefix}_src_word_count_mean": round(
                    float(df["src_word_count"].mean()), 4
                ),
                f"{prefix}_src_word_count_std": round(
                    float(df["src_word_count"].std(ddof=0)), 4
                ),
                f"{prefix}_src_word_count_min": int(df["src_word_count"].min()),
                f"{prefix}_src_word_count_max": int(df["src_word_count"].max()),
                f"{prefix}_src_word_count_median": round(
                    float(df["src_word_count"].median()), 4
                ),
            }
        )

    if "src_punct_ratio" in df.columns:
        stats[f"{prefix}_src_punct_ratio_mean"] = round(
            float(df["src_punct_ratio"].mean()), 4
        )
    if "tgt_punct_ratio" in df.columns:
        stats[f"{prefix}_tgt_punct_ratio_mean"] = round(
            float(df["tgt_punct_ratio"].mean()), 4
        )
    if "src_digit_ratio" in df.columns:
        stats[f"{prefix}_src_digit_ratio_mean"] = round(
            float(df["src_digit_ratio"].mean()), 4
        )
    if "tgt_digit_ratio" in df.columns:
        stats[f"{prefix}_tgt_digit_ratio_mean"] = round(
            float(df["tgt_digit_ratio"].mean()), 4
        )

    return stats


def build_filtered_candidate_pool(df):
    work = df.copy()

    initial_rows = len(work)

    work["src_norm_dedup"] = work["src_text"].map(normalize_for_dedup)
    work["tgt_norm_dedup"] = work["tgt_text"].map(normalize_for_dedup)
    work["pair_key"] = work["src_norm_dedup"] + " ||| " + work["tgt_norm_dedup"]

    before_dedup = len(work)
    work = work.drop_duplicates(subset=["pair_key"]).copy()
    after_dedup = len(work)

    work["src_word_count"] = work["src_text"].map(word_count)
    work["tgt_word_count"] = work["tgt_text"].map(word_count)
    work["src_punct_ratio"] = work["src_text"].map(punctuation_ratio)
    work["tgt_punct_ratio"] = work["tgt_text"].map(punctuation_ratio)
    work["src_digit_ratio"] = work["src_text"].map(digit_ratio)
    work["tgt_digit_ratio"] = work["tgt_text"].map(digit_ratio)
    work["src_has_url_or_email"] = work["src_text"].map(has_url_or_email)
    work["tgt_has_url_or_email"] = work["tgt_text"].map(has_url_or_email)

    mu = float(work["src_word_count"].mean()) if len(work) else 0.0
    sigma = float(work["src_word_count"].std(ddof=0)) if len(work) else 0.0

    lower = int(np.floor(mu))
    upper = int(np.floor(mu + 5))

    primary_filtered = work[
        (work["src_word_count"] >= lower)
        & (work["src_word_count"] <= upper)
        & (work["src_word_count"] >= 3)
        & (work["src_word_count"] <= 60)
        & (work["src_punct_ratio"] <= 0.35)
        & (work["tgt_punct_ratio"] <= 0.35)
        & (work["src_digit_ratio"] <= 0.30)
        & (work["tgt_digit_ratio"] <= 0.30)
        & (~work["src_has_url_or_email"])
        & (~work["tgt_has_url_or_email"])
    ].copy()

    fallback_used = False
    fallback_lower = None
    fallback_upper = None

    filtered = primary_filtered

    if len(filtered) < N_SAMPLES:
        fallback_used = True
        fallback_lower = max(1, int(np.floor(mu - 2)))
        fallback_upper = int(np.floor(mu + 8))

        filtered = work[
            (work["src_word_count"] >= fallback_lower)
            & (work["src_word_count"] <= fallback_upper)
            & (work["src_word_count"] >= 3)
            & (work["src_word_count"] <= 60)
            & (work["src_punct_ratio"] <= 0.40)
            & (work["tgt_punct_ratio"] <= 0.40)
            & (work["src_digit_ratio"] <= 0.35)
            & (work["tgt_digit_ratio"] <= 0.35)
            & (~work["src_has_url_or_email"])
            & (~work["tgt_has_url_or_email"])
        ].copy()

    stats = {
        "initial_rows_after_empty_removal": int(initial_rows),
        "rows_before_dedup": int(before_dedup),
        "rows_after_dedup": int(after_dedup),
        "duplicate_rows_removed": int(before_dedup - after_dedup),
        "avg_src_word_count": round(mu, 4),
        "std_src_word_count": round(sigma, 4),
        "primary_length_lower": int(lower),
        "primary_length_upper": int(upper),
        "primary_filtered_pool_size": int(len(primary_filtered)),
        "fallback_used": bool(fallback_used),
        "fallback_length_lower": (
            int(fallback_lower) if fallback_lower is not None else None
        ),
        "fallback_length_upper": (
            int(fallback_upper) if fallback_upper is not None else None
        ),
        "final_filtered_pool_size": int(len(filtered)),
    }

    stats.update(make_stats_dict(work, "dedup_pool"))
    stats.update(make_stats_dict(primary_filtered, "primary_filtered_pool"))
    stats.update(make_stats_dict(filtered, "final_filtered_pool"))

    return filtered, stats


def sample_reproducibly(filtered_df, n_samples, seed):
    if len(filtered_df) <= n_samples:
        sampled = filtered_df.copy()
        sampled["length_bucket"] = 0
        sampling_stats = {
            "sampling_seed": int(seed),
            "sampling_strategy": "take_all_because_pool_leq_requested",
            "requested_samples": int(n_samples),
            "returned_samples": int(len(sampled)),
            "length_bucket_count": 1,
        }
        return sampled, sampling_stats

    work = filtered_df.copy()

    work = work.sort_values(
        by=["src_word_count", "src_text", "tgt_text", "original_row_id"]
    ).reset_index(drop=True)

    try:
        bucket_count = min(3, work["src_word_count"].nunique())
        work["length_bucket"] = pd.qcut(
            work["src_word_count"],
            q=bucket_count,
            labels=False,
            duplicates="drop",
        )
    except ValueError:
        work["length_bucket"] = 0
        bucket_count = 1

    rng = np.random.default_rng(seed)

    buckets = []
    bucket_sizes = {}
    for bucket_id, group in work.groupby("length_bucket", dropna=False):
        idx = rng.permutation(group.index.to_numpy())
        buckets.append(list(idx))
        bucket_sizes[str(bucket_id)] = int(len(group))

    selected_idx = []
    while len(selected_idx) < n_samples and any(buckets):
        for bucket in buckets:
            if bucket and len(selected_idx) < n_samples:
                selected_idx.append(bucket.pop())

    sampled = work.loc[selected_idx].copy()
    sampled = sampled.sort_values("original_row_id").reset_index(drop=True)

    sampling_stats = {
        "sampling_seed": int(seed),
        "sampling_strategy": "round_robin_over_length_buckets",
        "requested_samples": int(n_samples),
        "returned_samples": int(len(sampled)),
        "length_bucket_count": int(bucket_count),
        "bucket_sizes_before_sampling": bucket_sizes,
        "selected_original_row_ids": sampled["original_row_id"].tolist(),
    }

    return sampled, sampling_stats


def dataframe_to_json_records(df, fields=None):
    if fields is not None:
        df = df[fields].copy()

    records = []
    for row in df.to_dict(orient="records"):
        clean_row = {}
        for k, v in row.items():
            if isinstance(v, (np.integer,)):
                clean_row[k] = int(v)
            elif isinstance(v, (np.floating,)):
                clean_row[k] = float(v)
            elif pd.isna(v):
                clean_row[k] = None
            else:
                clean_row[k] = v
        records.append(clean_row)
    return records


def main():
    summary_rows = []
    global_summary = {
        "seed": int(SEED),
        "n_samples_per_language": int(N_SAMPLES),
        "languages": {},
    }

    for lang_key, path in TEST_FILES.items():
        if not path.exists():
            print(f"Missing file: {path}")
            continue

        print(f"\nProcessing {lang_key}: {path}")

        df, src_col, tgt_col = read_test_file(path, lang_key)
        filtered_df, filter_stats = build_filtered_candidate_pool(df)
        sampled_df, sampling_stats = sample_reproducibly(filtered_df, N_SAMPLES, SEED)

        filtered_out = OUT_DIR / f"{lang_key}_filtered_pool.csv"
        filtered_df.to_csv(filtered_out, index=False, encoding="utf-8")

        sample_out_csv = OUT_DIR / f"{lang_key}_human_eval_30samples_seed{SEED}.csv"
        sampled_df.to_csv(sample_out_csv, index=False, encoding="utf-8")

        # NEW: minimized CSV with only source and target
        sample_out_min_csv = (
            OUT_DIR / f"{lang_key}_human_eval_30samples_seed{SEED}.min.csv"
        )
        sampled_df[["src_text", "tgt_text"]].to_csv(
            sample_out_min_csv, index=False, encoding="utf-8"
        )

        sample_out_json = OUT_DIR / f"{lang_key}_human_eval_30samples_seed{SEED}.json"
        sample_json = {
            "language": lang_key,
            "seed": int(SEED),
            "requested_samples": int(N_SAMPLES),
            "returned_samples": int(len(sampled_df)),
            "src_column_used": src_col,
            "tgt_column_used": tgt_col,
            "samples": dataframe_to_json_records(
                sampled_df,
                fields=[
                    "original_row_id",
                    "src_text",
                    "tgt_text",
                    "src_word_count",
                    "tgt_word_count",
                    "src_punct_ratio",
                    "tgt_punct_ratio",
                    "src_digit_ratio",
                    "tgt_digit_ratio",
                    "length_bucket",
                ],
            ),
        }
        with sample_out_json.open("w", encoding="utf-8") as f:
            json.dump(sample_json, f, ensure_ascii=False, indent=2)

        stats_out_json = OUT_DIR / f"{lang_key}_sampling_stats_seed{SEED}.json"
        lang_stats = {
            "language": lang_key,
            "input_file": str(path),
            "src_column_used": src_col,
            "tgt_column_used": tgt_col,
            "seed": int(SEED),
            "requested_samples": int(N_SAMPLES),
            "filter_stats": filter_stats,
            "sampling_stats": sampling_stats,
        }
        with stats_out_json.open("w", encoding="utf-8") as f:
            json.dump(lang_stats, f, ensure_ascii=False, indent=2)

        summary_row = {
            "language": lang_key,
            "input_file": str(path),
            "src_column_used": src_col,
            "tgt_column_used": tgt_col,
            "initial_rows_after_empty_removal": filter_stats[
                "initial_rows_after_empty_removal"
            ],
            "rows_after_dedup": filter_stats["rows_after_dedup"],
            "duplicate_rows_removed": filter_stats["duplicate_rows_removed"],
            "avg_src_word_count": filter_stats["avg_src_word_count"],
            "std_src_word_count": filter_stats["std_src_word_count"],
            "primary_length_lower": filter_stats["primary_length_lower"],
            "primary_length_upper": filter_stats["primary_length_upper"],
            "primary_filtered_pool_size": filter_stats["primary_filtered_pool_size"],
            "fallback_used": filter_stats["fallback_used"],
            "fallback_length_lower": filter_stats["fallback_length_lower"],
            "fallback_length_upper": filter_stats["fallback_length_upper"],
            "final_filtered_pool_size": filter_stats["final_filtered_pool_size"],
            "final_sample_size": len(sampled_df),
            "seed": int(SEED),
            "sample_csv_file": str(sample_out_csv),
            "sample_min_csv_file": str(sample_out_min_csv),
            "sample_json_file": str(sample_out_json),
            "filtered_pool_file": str(filtered_out),
            "stats_json_file": str(stats_out_json),
        }
        summary_rows.append(summary_row)

        global_summary["languages"][lang_key] = {
            "input_file": str(path),
            "src_column_used": src_col,
            "tgt_column_used": tgt_col,
            "filter_stats": filter_stats,
            "sampling_stats": sampling_stats,
            "sample_csv_file": str(sample_out_csv),
            "sample_min_csv_file": str(sample_out_min_csv),
            "sample_json_file": str(sample_out_json),
            "filtered_pool_file": str(filtered_out),
            "stats_json_file": str(stats_out_json),
        }

        print(f"  avg src word count: {filter_stats['avg_src_word_count']:.2f}")
        print(f"  filtered pool size: {filter_stats['final_filtered_pool_size']}")
        print(f"  final sample size : {len(sampled_df)}")
        print(f"  wrote full CSV    : {sample_out_csv}")
        print(f"  wrote min CSV     : {sample_out_min_csv}")
        print(f"  wrote JSON        : {sample_out_json}")

    summary_df = pd.DataFrame(summary_rows)
    summary_path_csv = OUT_DIR / "human_eval_sampling_summary.csv"
    summary_df.to_csv(summary_path_csv, index=False, encoding="utf-8")

    summary_path_json = OUT_DIR / "human_eval_sampling_summary.json"
    with summary_path_json.open("w", encoding="utf-8") as f:
        json.dump(global_summary, f, ensure_ascii=False, indent=2)

    print(f"\nSummary written to CSV : {summary_path_csv}")
    print(f"Summary written to JSON: {summary_path_json}")


if __name__ == "__main__":
    main()
