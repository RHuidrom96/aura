# -- Evaluation rubric (shared with the rating template + JS) -----------------
import re

CRITERIA_DEFAULTS = [
    {"id": "adequacy", "name": "Adequacy",
     "color": "#0072B2",
     "desc": "Source information preserved in translation",
     "guide": "Measures how much information from the source sentence is correctly preserved in the translation. A high adequacy score means the translated sentence conveys the same content and intent as the source. Penalize omissions, additions, and mistranslations."},
    {"id": "fluency", "name": "Fluency",
     "color": "#E69F00",
     "desc": "Natural and grammatical target text",
     "guide": "Measures how natural, grammatically correct, and readable the translated sentence is in the target language. Judge this independently of the source -- ignore meaning for a moment and ask whether a native speaker would find the sentence well-formed."},
    {"id": "meaning_preservation", "name": "Meaning preservation",
     "color": "#009E73",
     "desc": "Overall meaning intact, no hallucination",
     "guide": "Measures whether the overall meaning and context of the source sentence are maintained without distortion or misunderstanding in the translation, and whether any hallucinated content (information not present in the source) has been introduced. Even a fluent and superficially adequate translation should score low here if it subtly changes the meaning or invents details."},
]

# Pool of colors auto-assigned to admin-defined criteria
CRITERION_COLOR_POOL = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442"]



def get_criteria_for(campaign):
    """Return the effective criteria list for a campaign."""
    return campaign.criteria or CRITERIA_DEFAULTS

def get_scale_for(campaign):
    """Return the normalized scale config dict for a campaign (template/JS friendly)."""
    return campaign.scale_config

# Valid rating-scale designs, grouped by scale type. Used to validate admin input
# and to populate the campaign form selectors.
SCALE_DESIGNS = {
    "likert": [
        {"id": "circles", "name": "Numbered circles", "desc": "Round 1–N buttons (the classic look)."},
        {"id": "buttons", "name": "Labelled buttons", "desc": "Wider pills showing the number and its label."},
        {"id": "radio",   "name": "Radio buttons",    "desc": "Classic radio dials with a label under each."},
        {"id": "stars",   "name": "Star rating",      "desc": "Click a star from 1 to N."},
    ],
    "continuous": [
        {"id": "slider",  "name": "Slider",           "desc": "A draggable slider across the range (e.g. Direct Assessment 0–100)."},
    ],
}

SCALE_TYPES = [
    {"id": "likert", "name": "Likert (discrete points)",
     "desc": "Annotators pick one of a fixed number of points (e.g. a 1–5 quality scale)."},
    {"id": "continuous", "name": "Continuous (slider)",
     "desc": "Annotators pick any value on a range, e.g. Direct Assessment from 0 to 100."},
]

# Top-level evaluation modes the admin can choose between.
EVAL_MODES = [
    {"id": "likert", "name": "Likert scale rating",
     "desc": "Annotators score each criterion on a scale. Optionally also mark error spans (target only, or both source and target)."},
    {"id": "pairwise", "name": "Pairwise preference",
     "desc": "Annotators compare two candidate outputs and choose which is better, using preference options you define."},
    {"id": "span_only", "name": "Span annotation only",
     "desc": "Annotators only mark error spans (no scoring). Your criteria become the error-type categories."},
    {"id": "post_edit", "name": "Post-editing",
     "desc": "Annotators correct the output by editing it directly. Results report how much editing each system needed (edit rate / distance), a lighter-is-better quality signal."},
]


def _valid_design_for(scale_type, design):
    return design in {d["id"] for d in SCALE_DESIGNS.get(scale_type, [])}




SCALE_LABELS = {1: "Very poor", 2: "Poor", 3: "Acceptable", 4: "Good", 5: "Excellent"}

SCRIPT_OPTIONS = [
    {"id": "bengali",     "name": "Bengali–Assamese (Eastern Nagari)", "short": "Bengali–Assamese"},
    {"id": "meetei",      "name": "Meitei Mayek",                       "short": "Meitei Mayek"},
    {"id": "devanagari",  "name": "Devanagari",                         "short": "Devanagari"},
    {"id": "latin",       "name": "Latin / Roman",                      "short": "Latin"},
    {"id": "tibetan",     "name": "Tibetan",                            "short": "Tibetan"},
    {"id": "ol_chiki",    "name": "Ol Chiki (Santali)",                 "short": "Ol Chiki"},
    {"id": "tai",         "name": "Tai (Ahom / Tai Le)",                "short": "Tai"},
    {"id": "wancho",      "name": "Wancho",                             "short": "Wancho"},
    {"id": "other",       "name": "Other",                              "short": "Other"},
]

_SCRIPT_IDS = {s["id"] for s in SCRIPT_OPTIONS}
_SCRIPT_SHORT = {s["id"]: s["short"] for s in SCRIPT_OPTIONS}


def script_label(script_id):
    """Human-readable short name for a stored script id ("" if unset/unknown)."""
    return _SCRIPT_SHORT.get((script_id or "").strip().lower(), "")


# Which scripts are plausible for a given language. Used to restrict the script dropdowns
# and to reject impossible language/script combinations at save time. Every mapped set
# includes "latin" (romanization / transliteration is broadly possible) and "other" (an
# escape hatch), plus the language's native script(s) where Aura has a matching option.
# Languages NOT listed here are treated as unrestricted (any script allowed) so that
# free-typed languages never trigger a false error.
_L = "latin"
_O = "other"
LANGUAGE_SCRIPTS = {
    # Northeast India
    "assamese":              ["bengali", _L, _O],
    "manipuri (meitei)":     ["meetei", "bengali", _L, _O],
    "manipuri":              ["meetei", "bengali", _L, _O],
    "meitei":                ["meetei", "bengali", _L, _O],
    "bishnupriya manipuri":  ["bengali", _L, _O],
    "bodo":                  ["devanagari", _L, _O],
    "mizo":                  [_L, _O],
    "khasi":                 [_L, _O],
    "khasi (pnar)":          [_L, _O],
    "nyishi":                [_L, _O],
    "kokborok":              [_L, "bengali", _O],
    "nagamese":              [_L, "bengali", _O],
    "garo":                  [_L, "bengali", _O],
    "ao":                    [_L, _O],
    "angami":                [_L, _O],
    "sumi":                  [_L, _O],
    "lotha":                 [_L, _O],
    "tangkhul":              [_L, _O],
    "hmar":                  [_L, _O],
    "paite":                 [_L, _O],
    "thadou (kuki)":         [_L, _O],
    "thadou":                [_L, _O],
    "karbi":                 [_L, "bengali", _O],
    "dimasa":                [_L, "bengali", _O],
    "rabha":                 [_L, "bengali", _O],
    "adi":                   [_L, _O],
    "apatani":               [_L, _O],
    "rongmei":               [_L, _O],
    "tiwa":                  [_L, "bengali", _O],
    "deori":                 [_L, "bengali", _O],
    "chakma":                ["bengali", _L, _O],
    "wancho":                ["wancho", _L, _O],
    # Other Indian + common
    "english":               [_L, _O],
    "hindi":                 ["devanagari", _L, _O],
    "bengali":               ["bengali", _L, _O],
    "nepali":                ["devanagari", _L, _O],
    "marathi":               ["devanagari", _L, _O],
    "sanskrit":              ["devanagari", _L, _O],
    "maithili":              ["devanagari", _L, _O],
    "santali":               ["ol_chiki", "devanagari", _L, _O],
    "tamil":                 [_L, _O],
    "telugu":                [_L, _O],
    "kannada":               [_L, _O],
    "malayalam":             [_L, _O],
    "gujarati":              [_L, _O],
    "punjabi":               [_L, _O],
    "odia":                  [_L, _O],
    "urdu":                  [_L, _O],
}


def _normalize_language(name):
    return (name or "").strip().lower()


def allowed_scripts_for_language(name):
    """List of allowed script ids for a language, or None if the language is unknown
    (meaning: no restriction). Matches the full name first, then the name with any
    parenthetical qualifier removed (e.g. "Manipuri (Meitei)" -> "manipuri")."""
    key = _normalize_language(name)
    if not key:
        return None
    if key in LANGUAGE_SCRIPTS:
        return list(LANGUAGE_SCRIPTS[key])
    base = re.sub(r"\(.*?\)", "", key).strip()
    if base and base in LANGUAGE_SCRIPTS:
        return list(LANGUAGE_SCRIPTS[base])
    return None


def script_allowed_for_language(script_id, language):
    """True if `script_id` is a valid choice for `language`. Unknown languages allow any
    valid script; an empty script is always allowed (it's optional)."""
    sid = (script_id or "").strip().lower()
    if not sid:
        return True
    if sid not in _SCRIPT_IDS:
        return False
    allowed = allowed_scripts_for_language(language)
    if allowed is None:
        return True
    return sid in allowed