import re
import json

from utils.constants import SCALE_TYPES, CRITERION_COLOR_POOL, _valid_design_for


def _slugify(s, fallback):
    """Lowercase, ascii, underscores. For deriving criterion IDs from names."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s or fallback

def parse_scale_from_form(form, errors):
    """Read the rating-scale fields from a submitted form.

    Returns a dict of Campaign kwargs (scale_type, scale_points, scale_min,
    scale_max, scale_design, scale_labels_json). Appends to `errors` on problems.
    """
    scale_type = (form.get("scale_type") or "likert").strip()
    if scale_type not in {t["id"] for t in SCALE_TYPES}:
        errors.append("Invalid rating scale type.")
        scale_type = "likert"

    design = (form.get("scale_design") or "").strip()
    labels = {}
    scale_points, scale_min, scale_max = 5, 0, 100

    if scale_type == "likert":
        try:
            scale_points = int(form.get("scale_points") or 5)
        except (TypeError, ValueError):
            scale_points = 5
        if scale_points < 2 or scale_points > 11:
            errors.append("A Likert scale must have between 2 and 11 points.")
            scale_points = max(2, min(11, scale_points))
        if not _valid_design_for("likert", design):
            design = "circles"
        # Optional per-point labels: fields scale_label_1 .. scale_label_N
        for v in range(1, scale_points + 1):
            txt = (form.get(f"scale_label_{v}") or "").strip()
            if txt:
                labels[str(v)] = txt
        # If the admin left every point label blank, persist sensible graded
        # defaults so annotators always see a worded scale. Partial labelling is
        # respected as-is (we don't mix admin wording with defaults).
        if not labels:
            from models import Campaign
            labels = Campaign.default_likert_labels(scale_points)
    else:  # continuous
        try:
            scale_min = int(form.get("scale_min") or 0)
            scale_max = int(form.get("scale_max") or 100)
        except (TypeError, ValueError):
            errors.append("Continuous scale bounds must be whole numbers.")
            scale_min, scale_max = 0, 100
        if scale_max <= scale_min:
            errors.append("The continuous scale's maximum must be greater than its minimum.")
        if not _valid_design_for("continuous", design):
            design = "slider"
        min_lbl = (form.get("scale_min_label") or "").strip()
        max_lbl = (form.get("scale_max_label") or "").strip()
        if min_lbl:
            labels["min"] = min_lbl
        if max_lbl:
            labels["max"] = max_lbl

    return {
        "scale_type": scale_type,
        "scale_points": scale_points,
        "scale_min": scale_min,
        "scale_max": scale_max,
        "scale_design": design,
        "scale_labels_json": json.dumps(labels, ensure_ascii=False) if labels else "",
    }

def parse_criteria_from_form(form, field_prefix=""):
    """Parse the parallel crit_name[]/crit_desc[] arrays into a criteria list.

    Returns (criteria, criteria_missing_desc).
    """
    prefix = field_prefix or ""
    crit_names = form.getlist(prefix + "crit_name")
    crit_descs = form.getlist(prefix + "crit_desc")
    criteria = []
    criteria_missing_desc = []
    used_ids = set()
    for i, nm in enumerate(crit_names):
        nm = (nm or "").strip()
        if not nm:
            continue
        desc = (crit_descs[i] if i < len(crit_descs) else "").strip()
        if not desc:
            criteria_missing_desc.append(nm)
        base_id = _slugify(nm, f"criterion_{i+1}")
        cid = base_id
        n = 2
        while cid in used_ids:
            cid = f"{base_id}_{n}"; n += 1
        used_ids.add(cid)
        color = CRITERION_COLOR_POOL[len(criteria) % len(CRITERION_COLOR_POOL)]
        criteria.append({"id": cid, "name": nm, "color": color, "desc": "", "guide": desc})
    return criteria, criteria_missing_desc

def parse_preferences_from_form(form):
    """Parse parallel pref_label[] inputs into an ordered options list [{id,label}]."""
    labels = form.getlist("pref_label")
    prefs = []
    used = set()
    for i, lb in enumerate(labels):
        lb = (lb or "").strip()
        if not lb:
            continue
        base = _slugify(lb, f"option_{i+1}")
        pid = base
        n = 2
        while pid in used:
            pid = f"{base}_{n}"; n += 1
        used.add(pid)
        prefs.append({"id": pid, "label": lb})
    return prefs