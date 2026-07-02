#!/usr/bin/env python3
# ================================================================================================
# Created By  : Rudali Huidrom
# Created Date: Wed Jul 01 2026
# ================================================================================================
"""
translate_to_english.py

Translate English text into Northeast Indian / Indic languages (Manipuri /
Meitei Mayek, Nagamese, Assamese, or others you configure) using three
different systems:
  - Google Gemini
  - Anthropic Claude
  - OpenAI (GPT)

For every translated sentence, this script also records:
  - latency_seconds : wall-clock time taken by the successful API call
  - input_tokens / output_tokens : token usage reported by the API (if any)
  - cost_usd : estimated cost of that single call, based on the pricing
               table below (see PRICING NOTE)

Input:
  One or more CSV files with (at minimum) these columns:
      src_text   -> the English sentence to translate
      tgt_text   -> a human reference translation in the target language
                    (used as "reference" in output)

Output:
  A single JSON file containing a list of records, one per (row, system) pair,
  in the following format (matching segments_bengali.json, plus timing/cost):

  [
    {
      "id": "<lang>_<system>_<row-number, zero-padded>",
      "source": "<original English text>",
      "target": "<model-generated translation, in the target language>",
      "reference": "<human reference translation, in the target language>",
      "system": "<System A | System B | System C | System D>",
      "domain": "<domain, default 'General' or from --domain / a 'domain' column>",
      "latency_seconds": 1.234,
      "input_tokens": 42,
      "output_tokens": 17,
      "cost_usd": 0.000312
    },
    ...
  ]

  A summary of total time and total estimated cost per system is printed to
  stdout at the end of the run, and written alongside the output JSON as
  "<output>.summary.json".

Usage:
  python translate_to_english.py \\
      --input manipuri.csv --language "Manipuri (Meitei Mayek)" --domain General \\
      --output manipuri_results.json \\
      --systems gemini claude openai

  --language is the TARGET language you're translating English into.

  You can pass multiple --input files (e.g. one per language) together with
  matching --language values, or run the script once per language/file.

  If a run ends with some empty targets (e.g. a row exhausted all retries
  against a persistent 503), re-run the exact same command plus
  --resume-from <previous-output.json> to reuse everything that already
  succeeded and only re-attempt what didn't -- no wasted calls/cost/time:
      python translate_to_english.py \\
          --input manipuri.csv --language "Manipuri (Meitei Mayek)" \\
          --output manipuri_results.json \\
          --systems gemini \\
          --resume-from manipuri_results.json

Required environment variables (set only the ones for systems you use):
  GEMINI_API_KEY     - Google AI Studio / Gemini API key
  ANTHROPIC_API_KEY  - Anthropic API key
  OPENAI_API_KEY     - OpenAI API key

Optional retry tuning (defaults shown):
  API_MAX_RETRIES=5                  - attempts per call before giving up
  API_RETRY_BACKOFF_BASE_SECONDS=5   - first retry delay (doubles each attempt)
  API_RETRY_BACKOFF_MAX_SECONDS=60   - cap on any single retry delay
  API_REQUEST_TIMEOUT_SECONDS=60     - how long to wait for one API response
                                        before treating it as a timeout
  Retries only apply to transient errors (5xx, 429, timeouts, connection
  errors). Errors like 400/401/403/404/422 fail immediately since retrying
  a malformed or unauthorized request can't succeed.

Optional generation tuning:
  OPENAI_TEMPERATURE   - if set, sent as the 'temperature' param to OpenAI.
                          Left unset by default because some models (e.g.
                          gpt-5) reject any non-default temperature with a
                          400 error; omitting it lets the API use its own
                          default instead.
  GEMINI_TEMPERATURE   - if set, sent as generationConfig.temperature to
                          Gemini. Left unset by default for the same reason
                          as OPENAI_TEMPERATURE above.

=====================================================================
PRICING NOTE (please read before trusting cost_usd)
=====================================================================
API prices change often and vary by model. The numbers in DEFAULT_PRICING
below are placeholders (approximate, USD per 1,000,000 tokens) and may be
OUT OF DATE by the time you run this. Before relying on cost_usd:
  1. Check the current pricing pages for each provider you use.
  2. Either edit DEFAULT_PRICING directly, or override at run time with
     environment variables, e.g.:
        export GEMINI_PRICE_INPUT_PER_M=0.30
        export GEMINI_PRICE_OUTPUT_PER_M=2.50
        export CLAUDE_PRICE_INPUT_PER_M=3.00
        export CLAUDE_PRICE_OUTPUT_PER_M=15.00
        export OPENAI_PRICE_INPUT_PER_M=2.50
        export OPENAI_PRICE_OUTPUT_PER_M=10.00

Install dependencies:
  pip install -r requirements.txt --break-system-packages
"""

import argparse
import csv
import json
import os
import sys
import time
from typing import Optional, Tuple, Dict, Any

import requests

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# Map internal system keys -> the "system" label used in the output JSON.
SYSTEM_LABELS = {
    "gemini": "System A (Gemini)",
    "claude": "System B (Claude)",
    "openai": "System D (GPT)",
}

# Placeholder pricing, USD per 1,000,000 tokens. SEE "PRICING NOTE" ABOVE.
DEFAULT_PRICING = {
    "gemini": {"input": 0.10, "output": 0.40},  # gemini-2.5-flash-lite tier -- verify current price
    "claude": {"input": 3.00, "output": 15.00},
    "openai": {"input": 0.15, "output": 0.60},  # gpt-4.1-mini tier -- UNVERIFIED, check OpenAI's pricing page
}

MAX_RETRIES = int(os.environ.get("API_MAX_RETRIES", 5))
RETRY_BACKOFF_BASE_SECONDS = float(os.environ.get("API_RETRY_BACKOFF_BASE_SECONDS", 5))
RETRY_BACKOFF_MAX_SECONDS = float(os.environ.get("API_RETRY_BACKOFF_MAX_SECONDS", 60))
REQUEST_TIMEOUT = float(os.environ.get("API_REQUEST_TIMEOUT_SECONDS", 60))

# Status codes where retrying is pointless -- the request itself is invalid
# (bad language code, bad auth, bad payload, etc.) and will fail identically
# every time, so we fail fast instead of burning retries/quota on it.
NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 404, 422}


class NonRetryableError(RuntimeError):
    """Base class for errors where retrying can't possibly help -- the same
    input will produce the same failure every time (bad config, content
    that will always be blocked/refused, etc.)."""


class APICallError(RuntimeError):
    """HTTP error from a translation API, carrying enough detail to decide
    whether it's worth retrying (transient like 503/429) or not (like 400)."""

    def __init__(self, message: str, status_code: Optional[int] = None, retry_after: Optional[float] = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class ConfigurationError(NonRetryableError):
    """Local, pre-request validation failure (missing API key, unsupported
    language for a given system, etc.)."""


def raise_for_status_verbose(resp: requests.Response) -> None:
    """
    Like resp.raise_for_status(), but includes the response body in the
    raised error so API-side validation messages are
    actually visible instead of being swallowed, and preserves the status
    code / Retry-After header so the caller can decide how to retry.
    """
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        body = resp.text.strip()
        if len(body) > 1500:
            body = body[:1500] + " ...[truncated]"

        retry_after = None
        header_val = resp.headers.get("Retry-After")
        if header_val is not None:
            try:
                retry_after = float(header_val)
            except ValueError:
                retry_after = None

        raise APICallError(
            f"{exc} | response body: {body}",
            status_code=resp.status_code,
            retry_after=retry_after,
        ) from exc


# Some languages are ambiguous about which script to use unless told
# explicitly -- an LLM given just the language name can silently default to
# the "wrong" one. Map a language name (lowercased) to an explicit script
# instruction appended to the prompt. Extend this if you hit the same issue
# with another language.
SCRIPT_INSTRUCTIONS = {
    "manipuri (meitei mayek)": (
        "Write the output in the native Meitei Mayek script "
        "(Unicode block U+ABC0-U+ABFF, e.g. ꯃꯤꯇꯩꯂꯣꯟ), NOT in Bengali/Assamese "
        "script. Do not transliterate into Bengali script under any "
        "circumstances."
    ),
    "manipuri": (
        "Write the output in the native Meitei Mayek script "
        "(Unicode block U+ABC0-U+ABFF, e.g. ꯃꯤꯇꯩꯂꯣꯟ), NOT in Bengali/Assamese "
        "script. Do not transliterate into Bengali script under any "
        "circumstances."
    ),
    "meitei": (
        "Write the output in the native Meitei Mayek script "
        "(Unicode block U+ABC0-U+ABFF, e.g. ꯃꯤꯇꯩꯂꯣꯟ), NOT in Bengali/Assamese "
        "script. Do not transliterate into Bengali script under any "
        "circumstances."
    ),
    "meitei mayek": (
        "Write the output in the native Meitei Mayek script "
        "(Unicode block U+ABC0-U+ABFF, e.g. ꯃꯤꯇꯩꯂꯣꯟ), NOT in Bengali/Assamese "
        "script. Do not transliterate into Bengali script under any "
        "circumstances."
    ),
}


def build_prompt(language: str, text: str) -> str:
    """Shared instruction used across all LLM-based systems (Gemini/Claude/OpenAI)."""
    script_instruction = SCRIPT_INSTRUCTIONS.get(language.strip().lower())
    script_line = f"\n{script_instruction}" if script_instruction else ""
    return (
        f"Translate the following English sentence into fluent, natural {language}.{script_line}\n"
        f"Only output the {language} translation, with no extra commentary, quotes, "
        f"or explanation.\n\n"
        f"English sentence: {text}\n"
        f"{language} translation:"
    )


def get_price(system_key: str) -> Dict[str, float]:
    """Look up per-1M-token price for a system, allowing env var overrides."""
    default = DEFAULT_PRICING.get(system_key, {"input": 0.0, "output": 0.0})
    prefix = system_key.upper()
    input_price = float(os.environ.get(f"{prefix}_PRICE_INPUT_PER_M", default["input"]))
    output_price = float(os.environ.get(f"{prefix}_PRICE_OUTPUT_PER_M", default["output"]))
    return {"input": input_price, "output": output_price}


def compute_cost(system_key: str, input_tokens: Optional[int], output_tokens: Optional[int]) -> Optional[float]:
    """Estimate USD cost of a single call. Returns None if token counts are unknown."""
    if input_tokens is None and output_tokens is None:
        return None
    price = get_price(system_key)
    cost = (input_tokens or 0) / 1_000_000 * price["input"] + (output_tokens or 0) / 1_000_000 * price["output"]
    return cost


# --------------------------------------------------------------------------
# Per-system translation functions.
# Each takes (text, language) and returns (translated_text, usage_dict) where
# usage_dict has "input_tokens" and "output_tokens" (either may be None if
# the API doesn't report it). Each raises RuntimeError on unrecoverable
# failure.
# --------------------------------------------------------------------------

def translate_gemini(text: str, language: str) -> Tuple[str, Dict[str, Any]]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ConfigurationError("GEMINI_API_KEY environment variable not set")

    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": build_prompt(language, text)}]}
        ],
    }
    # Only send temperature if explicitly requested. Not hardcoding this
    # avoids a repeat of the gpt-5-style failure (some models reject
    # non-default sampling params outright), and lets the API use its own
    # default otherwise.
    temp_override = os.environ.get("GEMINI_TEMPERATURE")
    if temp_override is not None:
        payload["generationConfig"] = {"temperature": float(temp_override)}

    resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    try:
        raise_for_status_verbose(resp)
    except APICallError as exc:
        if exc.status_code == 404:
            # Google deprecates/renames Gemini model IDs fairly often. Rather
            # than have this dead-end into "figure it out yourself", point
            # directly at the fix: list what your key can actually call.
            raise APICallError(
                f"{exc}\n"
                f"    Hint: model '{model}' may be deprecated/renamed. List "
                f"models your key can currently use with:\n"
                f'    curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY" '
                f'| python3 -c "import json,sys; d=json.load(sys.stdin); '
                f"[print(m['name'].replace('models/','')) for m in d.get('models',[]) "
                f"if 'generateContent' in m.get('supportedGenerationMethods',[])]\"\n"
                f"    Then: export GEMINI_MODEL=<a model from that list>",
                status_code=exc.status_code,
                retry_after=exc.retry_after,
            ) from exc
        raise
    data = resp.json()

    # A 200 response doesn't guarantee usable content: the prompt or the
    # response can be safety-blocked, in which case "candidates" is either
    # missing/empty or present without a "content"/"parts" payload. Retrying
    # an input that got blocked won't change the outcome, so surface this as
    # non-retryable rather than crashing into IndexError/KeyError and
    # burning through the generic retry loop for nothing.
    candidates = data.get("candidates") or []
    if not candidates:
        block_reason = data.get("promptFeedback", {}).get("blockReason")
        raise NonRetryableError(
            f"Gemini returned no candidates (likely safety-blocked). "
            f"blockReason={block_reason!r}. Full response: {json.dumps(data)[:500]}"
        )

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts") or []
    if not parts:
        finish_reason = candidate.get("finishReason")
        raise NonRetryableError(
            f"Gemini candidate had no content (finishReason={finish_reason!r}), "
            f"likely safety/recitation blocked. Full response: {json.dumps(data)[:500]}"
        )

    translated = "".join(p.get("text", "") for p in parts).strip()

    usage_meta = data.get("usageMetadata", {})
    usage = {
        "input_tokens": usage_meta.get("promptTokenCount"),
        "output_tokens": usage_meta.get("candidatesTokenCount"),
    }
    return translated, usage


def translate_claude(text: str, language: str) -> Tuple[str, Dict[str, Any]]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ConfigurationError("ANTHROPIC_API_KEY environment variable not set")

    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 512,
        "messages": [{"role": "user", "content": build_prompt(language, text)}],
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    raise_for_status_verbose(resp)
    data = resp.json()
    translated = "".join(
        block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
    ).strip()

    usage_meta = data.get("usage", {})
    usage = {
        "input_tokens": usage_meta.get("input_tokens"),
        "output_tokens": usage_meta.get("output_tokens"),
    }
    return translated, usage


def translate_openai(text: str, language: str) -> Tuple[str, Dict[str, Any]]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ConfigurationError("OPENAI_API_KEY environment variable not set")

    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": build_prompt(language, text)}],
    }
    # Some newer models (e.g. gpt-5) only support the default temperature
    # (1) and reject any explicit value with a 400. Rather than hardcode a
    # model-name check that will go stale, temperature is only sent if you
    # explicitly opt in via OPENAI_TEMPERATURE -- omitting it lets the API
    # use its own default and keeps this working across model generations.
    temp_override = os.environ.get("OPENAI_TEMPERATURE")
    if temp_override is not None:
        payload["temperature"] = float(temp_override)
    resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    raise_for_status_verbose(resp)
    data = resp.json()

    # A 200 response doesn't guarantee usable content: "choices" can be
    # empty, or message.content can be null if the model issued a refusal
    # (the refusal text lives in message.refusal instead) or returned only
    # tool_calls. Retrying the same input won't change a refusal, so treat
    # this as non-retryable rather than crashing into IndexError/
    # AttributeError and burning through the generic retry loop for nothing.
    choices = data.get("choices") or []
    if not choices:
        raise NonRetryableError(
            f"OpenAI returned no choices. Full response: {json.dumps(data)[:500]}"
        )

    message = choices[0].get("message", {})
    content = message.get("content")
    if content is None:
        refusal = message.get("refusal")
        finish_reason = choices[0].get("finish_reason")
        raise NonRetryableError(
            f"OpenAI returned no content (finish_reason={finish_reason!r}, "
            f"refusal={refusal!r}). Full response: {json.dumps(data)[:500]}"
        )

    translated = content.strip()

    usage_meta = data.get("usage", {})
    usage = {
        "input_tokens": usage_meta.get("prompt_tokens"),
        "output_tokens": usage_meta.get("completion_tokens"),
    }
    return translated, usage


TRANSLATORS = {
    "gemini": translate_gemini,
    "claude": translate_claude,
    "openai": translate_openai,
}


# --------------------------------------------------------------------------
# Core pipeline
# --------------------------------------------------------------------------

def translate_with_retry(fn, text: str, language: str) -> Tuple[str, Dict[str, Any], float]:
    """
    Calls fn(text, language) with retries. Timing (latency_seconds) covers
    only the final, successful attempt -- not time spent sleeping between
    retries or on earlier failed attempts.

    Retry behavior:
      - NonRetryableError (missing API key, unsupported language for a
        given system, safety-blocked/refused content, etc.) fails
        immediately -- the same input will fail identically every time, so
        retrying just wastes wall-clock time.
      - APICallError with a non-retryable status code (400/401/403/404/422)
        fails immediately -- retrying a malformed/unauthorized request just
        wastes time and quota.
      - Everything else (5xx, 429, timeouts, connection errors) is retried
        up to MAX_RETRIES times with exponential backoff (capped at
        RETRY_BACKOFF_MAX_SECONDS), honoring a Retry-After header if the API
        sent one.

    Returns (translated_text, usage_dict, latency_seconds).
    """
    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        start = time.perf_counter()
        try:
            translated, usage = fn(text, language)
            latency = time.perf_counter() - start
            return translated, usage, latency
        except NonRetryableError as exc:
            print(f"    [error] non-retryable: {exc}", file=sys.stderr)
            raise
        except APICallError as exc:
            last_err = exc
            if exc.status_code in NON_RETRYABLE_STATUS_CODES:
                print(f"    [error] non-retryable ({exc.status_code}): {exc}", file=sys.stderr)
                raise
            print(
                f"    [warn] attempt {attempt}/{MAX_RETRIES} failed "
                f"(HTTP {exc.status_code}): {exc}",
                file=sys.stderr,
            )
            if attempt < MAX_RETRIES:
                sleep_time = exc.retry_after if exc.retry_after else min(
                    RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), RETRY_BACKOFF_MAX_SECONDS
                )
                print(f"    retrying in {sleep_time:.1f}s ...", file=sys.stderr)
                time.sleep(sleep_time)
        except Exception as exc:  # noqa: BLE001 - network errors, timeouts, etc.
            last_err = exc
            print(
                f"    [warn] attempt {attempt}/{MAX_RETRIES} failed: {exc}",
                file=sys.stderr,
            )
            if attempt < MAX_RETRIES:
                sleep_time = min(RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), RETRY_BACKOFF_MAX_SECONDS)
                print(f"    retrying in {sleep_time:.1f}s ...", file=sys.stderr)
                time.sleep(sleep_time)
    raise RuntimeError(f"All retries failed: {last_err}")


def read_input_csv(path: str):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise RuntimeError(f"{path}: could not read header row")
        fieldnames = [c.strip() for c in reader.fieldnames]
        if "src_text" not in fieldnames:
            raise RuntimeError(f"{path}: missing required column 'src_text'")
        # src_text = English source sentence; tgt_text = human reference
        # translation in the target (Indic) language.
        rows = []
        for row in reader:
            clean = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
            rows.append(clean)
        return rows


def process_file(
    input_path: str,
    language: str,
    default_domain: str,
    systems: list,
    lang_tag: str,
    stats: Dict[str, Dict[str, float]],
    resume_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
):
    rows = read_input_csv(input_path)
    print(f"Loaded {len(rows)} rows from {input_path} ({language})")

    results = []
    for idx, row in enumerate(rows, start=1):
        source_text = row.get("src_text", "")       # English
        reference_text = row.get("tgt_text", "")    # human reference in target language
        domain = row.get("domain") or default_domain

        if not source_text:
            print(f"  [skip] row {idx}: empty src_text", file=sys.stderr)
            continue

        for system_key in systems:
            translate_fn = TRANSLATORS[system_key]
            label = SYSTEM_LABELS[system_key]
            record_id = f"{lang_tag}_{system_key}_{idx:03d}"

            # If resuming from a prior output file and this (row, system)
            # already succeeded there (non-empty target), reuse it verbatim
            # instead of re-calling the API -- no cost, no wait, no retries.
            if resume_lookup is not None:
                prior = resume_lookup.get(record_id)
                if prior is not None and prior.get("target"):
                    print(f"  row {idx}/{len(rows)} -> {system_key} ... [skip: already succeeded in resume file]")
                    results.append(prior)
                    continue

            print(f"  row {idx}/{len(rows)} -> {system_key} ...")

            target_text = ""
            usage = {"input_tokens": None, "output_tokens": None}
            latency = None
            try:
                target_text, usage, latency = translate_with_retry(translate_fn, source_text, language)
            except Exception as exc:  # noqa: BLE001
                print(f"    [error] giving up on row {idx} / {system_key}: {exc}", file=sys.stderr)

            cost = compute_cost(system_key, usage.get("input_tokens"), usage.get("output_tokens"))

            # Update running totals for the end-of-run summary.
            s = stats.setdefault(system_key, {"calls": 0, "total_seconds": 0.0, "total_cost_usd": 0.0, "cost_calls": 0})
            s["calls"] += 1
            if latency is not None:
                s["total_seconds"] += latency
            if cost is not None:
                s["total_cost_usd"] += cost
                s["cost_calls"] += 1

            results.append(
                {
                    "id": record_id,
                    "source": source_text,
                    "target": target_text,
                    "reference": reference_text,
                    "system": label,
                    "domain": domain,
                    "latency_seconds": round(latency, 3) if latency is not None else None,
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "cost_usd": round(cost, 6) if cost is not None else None,
                }
            )

    return results


def print_and_save_summary(stats: Dict[str, Dict[str, float]], output_path: str):
    print("\n=== Run summary ===")
    summary = {}
    for system_key, s in stats.items():
        calls = s["calls"]
        total_seconds = s["total_seconds"]
        avg_seconds = total_seconds / calls if calls else 0.0
        total_cost = s["total_cost_usd"]
        cost_note = "" if s["cost_calls"] == calls else f" (cost known for {s['cost_calls']}/{calls} calls)"
        print(
            f"  {SYSTEM_LABELS.get(system_key, system_key):<22} "
            f"calls={calls:<5} total_time={total_seconds:8.2f}s avg_time={avg_seconds:6.2f}s "
            f"total_cost=${total_cost:.6f}{cost_note}"
        )
        summary[system_key] = {
            "calls": calls,
            "total_seconds": round(total_seconds, 3),
            "avg_seconds": round(avg_seconds, 3),
            "total_cost_usd": round(total_cost, 6),
            "cost_known_for_calls": s["cost_calls"],
        }

    summary_path = f"{output_path}.summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\nSummary written to {summary_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Translate English CSV files into Manipuri/Nagamese/Assamese "
        "(or other target languages) using Gemini, Claude, and OpenAI, "
        "recording latency and cost."
    )
    parser.add_argument(
        "--input",
        required=True,
        nargs="+",
        action="append",
        help="Input CSV file(s) (columns: src_text [English], tgt_text "
        "[reference translation in the target language], [domain]). Either "
        "list several files after one --input, or repeat --input once per "
        "file/language group -- both work.",
    )
    parser.add_argument(
        "--language",
        required=True,
        nargs="+",
        action="append",
        help="Target language to translate English into, for each --input "
        "file, in the same order (e.g. 'Manipuri (Meitei Mayek)' 'Nagamese' "
        "'Assamese'). If a single value is given it is used for all input "
        "files. Can also be repeated once per --input, matching that style.",
    )
    parser.add_argument(
        "--lang-tag",
        nargs="+",
        action="append",
        default=None,
        help="Short tag used in output 'id' fields for each input file "
        "(e.g. mni nag asm). Defaults to a slug derived from --language. "
        "Can be repeated once per --input, matching that style.",
    )
    parser.add_argument(
        "--domain",
        default="General",
        help="Default domain label if the CSV has no 'domain' column (default: General).",
    )
    parser.add_argument(
        "--systems",
        nargs="+",
        choices=list(TRANSLATORS.keys()),
        default=list(TRANSLATORS.keys()),
        help="Which systems to run (default: all of gemini claude openai).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the combined output JSON file.",
    )
    parser.add_argument(
        "--resume-from",
        default=None,
        help="Path to a prior output JSON file (e.g. an earlier --output). "
        "Any (row, system) pair that already has a non-empty 'target' in "
        "that file is reused as-is -- no API call, no cost, no wait -- and "
        "only rows that were empty/missing (e.g. from exhausted retries) "
        "are actually re-translated. Matching is by 'id', so this only "
        "works if you use the same --lang-tag values as the prior run.",
    )
    args = parser.parse_args()

    # --input/--language/--lang-tag use action="append" combined with
    # nargs="+", so argparse gives a list of lists -- one sub-list per time
    # the flag appeared on the command line. Flatten so both invocation
    # styles work identically:
    #   --input a.csv b.csv c.csv                 (one flag, several values)
    #   --input a.csv --input b.csv --input c.csv (flag repeated)
    inputs = [v for group in args.input for v in group]
    languages = [v for group in args.language for v in group]
    lang_tag_groups = [v for group in args.lang_tag for v in group] if args.lang_tag else None

    if len(languages) == 1 and len(inputs) > 1:
        languages = languages * len(inputs)
    if len(languages) != len(inputs):
        parser.error("--language must have one value, or one value per --input file")

    if lang_tag_groups:
        lang_tags = lang_tag_groups
        if len(lang_tags) == 1 and len(inputs) > 1:
            lang_tags = lang_tags * len(inputs)
        if len(lang_tags) != len(inputs):
            parser.error("--lang-tag must have one value, or one value per --input file")
    else:
        lang_tags = [
            "".join(c for c in lang.lower() if c.isalnum())[:4] or "lang"
            for lang in languages
        ]

    all_results = []
    stats: Dict[str, Dict[str, float]] = {}
    run_start = time.perf_counter()

    resume_lookup: Optional[Dict[str, Dict[str, Any]]] = None
    if args.resume_from:
        try:
            with open(args.resume_from, encoding="utf-8") as f:
                prior_records = json.load(f)
            resume_lookup = {rec["id"]: rec for rec in prior_records}
            already_ok = sum(1 for rec in prior_records if rec.get("target"))
            print(
                f"Resuming from {args.resume_from}: {already_ok}/{len(prior_records)} "
                f"prior records already succeeded and will be reused as-is."
            )
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            parser.error(f"--resume-from {args.resume_from}: could not load ({exc})")

    for input_path, language, tag in zip(inputs, languages, lang_tags):
        file_results = process_file(
            input_path=input_path,
            language=language,
            default_domain=args.domain,
            systems=args.systems,
            lang_tag=tag,
            stats=stats,
            resume_lookup=resume_lookup,
        )
        all_results.extend(file_results)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    total_wall_seconds = time.perf_counter() - run_start
    print(f"\nWrote {len(all_results)} records to {args.output}")
    print(f"Total wall-clock run time: {total_wall_seconds:.2f}s")

    print_and_save_summary(stats, args.output)


if __name__ == "__main__":
    main()