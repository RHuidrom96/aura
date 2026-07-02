"""Parse an uploaded/pasted segments file into a list of segment dicts.

Historically Aura accepted only a JSON array. This module widens that to the tabular and
line-delimited formats research data usually ships in -- CSV, TSV, and JSON Lines -- while
producing exactly the same list-of-dicts the rest of the app already validates and stores.

Recognised columns/keys map straight onto segment fields:
    id, source, target, reference, system, domain, target_a, target_b, candidates
Any other columns are preserved as-is (kept as extra metadata on the segment).

Public API:
    parse_segments_upload(raw_text, filename="") -> (segments, error)
        `segments` is a list of dicts on success (error is None); on failure `segments` is
        None and `error` is a human-readable message.
"""
import csv
import io
import json

# Columns whose empty values should be dropped rather than stored as "".
_KNOWN_KEYS = {"id", "source", "target", "reference", "system", "domain",
               "target_a", "target_b", "candidates"}

SUPPORTED_EXTS = ("json", "jsonl", "ndjson", "csv", "tsv", "txt")
ACCEPT_ATTR = ".json,.jsonl,.ndjson,.csv,.tsv,.txt"


def _ext(filename):
    return (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""


def _coerce_candidates(value):
    """A 'candidates' cell may be a JSON array or a delimited string; normalise to a list."""
    if isinstance(value, list):
        return value
    s = (value or "").strip()
    if not s:
        return None
    if s[0] == "[":
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return parsed
        except (ValueError, TypeError):
            pass
    for delim in ("|", ";", "\t"):
        if delim in s:
            return [p.strip() for p in s.split(delim) if p.strip()]
    return [s]


def _clean_row(raw):
    """Turn a raw dict (from CSV/JSON) into a segment dict, dropping empty known fields and
    coercing 'candidates'. Non-empty unknown columns are kept verbatim."""
    seg = {}
    for k, v in raw.items():
        if k is None:
            continue
        key = k.strip()
        if key == "candidates":
            cand = _coerce_candidates(v)
            if cand is not None:
                seg["candidates"] = cand
            continue
        if isinstance(v, str):
            v = v.strip()
        if key in _KNOWN_KEYS:
            if v not in ("", None):
                seg[key] = v
        else:
            if v not in ("", None):
                seg[key] = v
    return seg


def _parse_json(raw_text):
    data = json.loads(raw_text)
    if isinstance(data, dict):
        # Allow a single object or a wrapper like {"segments": [...]}.
        if isinstance(data.get("segments"), list):
            data = data["segments"]
        else:
            data = [data]
    if not isinstance(data, list):
        raise ValueError("JSON must be an array of segment objects.")
    return [s if isinstance(s, dict) else s for s in data]


def _parse_jsonl(raw_text):
    out = []
    for i, line in enumerate(raw_text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"Line {i} is not valid JSON: {e}")
        if not isinstance(obj, dict):
            raise ValueError(f"Line {i} must be a JSON object.")
        out.append(_clean_row(obj))
    return out


def _parse_delimited(raw_text, delimiter):
    reader = csv.DictReader(io.StringIO(raw_text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("The file has no header row.")
    # Strip whitespace from header names.
    reader.fieldnames = [(h or "").strip() for h in reader.fieldnames]
    if "id" not in reader.fieldnames or "source" not in reader.fieldnames:
        raise ValueError("A delimited file needs at least 'id' and 'source' columns "
                         f"(found: {', '.join(reader.fieldnames)}).")
    out = []
    for row in reader:
        # Skip fully blank lines.
        if not any((v or "").strip() for v in row.values()):
            continue
        out.append(_clean_row(row))
    return out


def _sniff_delimiter(raw_text):
    head = raw_text.lstrip()[:4096]
    first_line = head.splitlines()[0] if head.splitlines() else ""
    # Prefer tab if the header clearly uses tabs; else comma.
    if "\t" in first_line and ("," not in first_line or first_line.count("\t") >= first_line.count(",")):
        return "\t"
    return ","


def parse_segments_upload(raw_text, filename=""):
    """Return (segments, error). See module docstring."""
    if raw_text is None:
        return None, "No data provided."
    text = raw_text.lstrip("\ufeff")  # strip a UTF-8 BOM if present
    stripped = text.strip()
    if not stripped:
        return None, "The segments file is empty."

    ext = _ext(filename)
    try:
        if ext == "json":
            return [_clean_row(s) if isinstance(s, dict) else s for s in _parse_json(text)], None
        if ext in ("jsonl", "ndjson"):
            return _parse_jsonl(text), None
        if ext == "csv":
            return _parse_delimited(text, ","), None
        if ext == "tsv":
            return _parse_delimited(text, "\t"), None

        # Unknown / .txt / pasted: sniff from the content.
        if stripped[0] in "[{":
            return [_clean_row(s) if isinstance(s, dict) else s for s in _parse_json(text)], None
        # JSON Lines? every non-empty line an object.
        lines = [l for l in stripped.splitlines() if l.strip()]
        if lines and all(l.strip().startswith("{") for l in lines):
            return _parse_jsonl(text), None
        # Fall back to delimited with a sniffed delimiter.
        return _parse_delimited(text, _sniff_delimiter(text)), None
    except ValueError as e:
        return None, str(e)
    except json.JSONDecodeError as e:
        return None, f"File is not valid JSON: {e}"
