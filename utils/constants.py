# -- Evaluation rubric (shared with the rating template + JS) -----------------
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
     "desc": "Annotators compare two candidate translations and choose which is better, using preference options you define."},
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