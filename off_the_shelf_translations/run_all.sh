#!/usr/bin/env bash
#
# ================================================================================================
# Created By  : Rudali Huidrom
# Created Date: Wed Jul 01 2026
# ================================================================================================
#
# run_all.sh
#
# Runs translate_to_english.py for every (language x system) combination
# and also writes one combined JSON file with everything merged together.
#
# Edit the CONFIG section below to point at your actual CSV files, then run:
#
#   chmod +x run_all.sh
#   ./run_all.sh
#
# Requires: python3, translate_to_english.py, requirements.txt installed,
# and the relevant API keys exported as environment variables (see below).

set -euo pipefail

# --------------------------------------------------------------------------
# CONFIG - edit these to match your setup
# --------------------------------------------------------------------------

SCRIPT="translate_to_english.py"
OUTDIR="results"
DOMAIN="General"

# One entry per language: "csv_path|Language Name|lang_tag|systems_override"
# systems_override is optional, comma-separated (e.g. "gemini,claude,openai").
# Leave it blank to use the default SYSTEMS list below.
LANGUAGES=(
  "manipuri.csv|Manipuri (Meitei Mayek)|mni|"
  "nagamese.csv|Nagamese|nag|"
  "assamese.csv|Assamese|asm|"
)

# Systems to run. Comment out any you don't want / don't have API keys for.
SYSTEMS=(
  gemini
  claude
  openai
)

# --------------------------------------------------------------------------
# Pre-flight checks
# --------------------------------------------------------------------------

if [[ ! -f "$SCRIPT" ]]; then
  echo "ERROR: $SCRIPT not found in current directory." >&2
  exit 1
fi

check_key () {
  local var_name="$1"
  if [[ -z "${!var_name:-}" ]]; then
    echo "WARNING: $var_name is not set. Calls to that system will fail." >&2
  fi
}
check_key GEMINI_API_KEY
check_key ANTHROPIC_API_KEY
check_key OPENAI_API_KEY

mkdir -p "$OUTDIR"

# --------------------------------------------------------------------------
# Run every (language x system) combination as its own call, so a failure
# in one combination doesn't stop the others, and each result is easy to
# inspect individually.
# --------------------------------------------------------------------------

RUN_OUTPUTS=()

for lang_entry in "${LANGUAGES[@]}"; do
  IFS='|' read -r csv_path lang_name lang_tag systems_override <<< "$lang_entry"

  if [[ ! -f "$csv_path" ]]; then
    echo "WARNING: input file '$csv_path' not found, skipping $lang_name." >&2
    continue
  fi

  if [[ -n "$systems_override" ]]; then
    IFS=',' read -r -a lang_systems <<< "$systems_override"
  else
    lang_systems=("${SYSTEMS[@]}")
  fi

  for system in "${lang_systems[@]}"; do
    out_file="${OUTDIR}/${lang_tag}_${system}.json"
    echo "=== ${lang_name} x ${system} -> ${out_file} ==="

    python3 "$SCRIPT" \
      --input "$csv_path" \
      --language "$lang_name" \
      --lang-tag "$lang_tag" \
      --domain "$DOMAIN" \
      --systems "$system" \
      --output "$out_file"

    RUN_OUTPUTS+=("$out_file")
  done
done

# --------------------------------------------------------------------------
# NOTE: a single combined call across all languages with one shared
# --systems list isn't used here anymore, because it can't respect the
# per-language systems_override above (e.g. if you exclude a system for a
# specific language). The merge step below builds the equivalent combined
# file correctly from the individual per-combination runs instead.
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Merge the individual per-combination files into one master file too
# (equivalent content to COMBINED_OUT, but built from the individual runs -
# useful if you re-run only some combinations later and want to re-merge).
# --------------------------------------------------------------------------

MERGED_OUT="${OUTDIR}/merged_from_individual_runs.json"

if [[ ${#RUN_OUTPUTS[@]} -gt 0 ]]; then
  echo "=== Merging ${#RUN_OUTPUTS[@]} individual result files -> ${MERGED_OUT} ==="
  python3 - "$MERGED_OUT" "${RUN_OUTPUTS[@]}" <<'PYEOF'
import json
import sys

out_path = sys.argv[1]
in_paths = sys.argv[2:]

merged = []
for p in in_paths:
    with open(p, encoding="utf-8") as f:
        merged.extend(json.load(f))

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(merged, f, ensure_ascii=False, indent=2)

print(f"Merged {len(in_paths)} files, {len(merged)} total records -> {out_path}")
PYEOF
fi

echo ""
echo "All done. Individual results in '${OUTDIR}/', combined file at '${MERGED_OUT}'."