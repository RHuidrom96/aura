"""Pilot demo loader for Northeast India language MT evaluation.

Reads editable campaign definitions and segments from the ``demo_data/`` directory
(``campaigns.json`` + ``segments/*.csv``), creates the campaigns, creates a few demo
annotators, and synthesizes partially-agreeing ratings so the results dashboards are
populated out of the box.

Everything is idempotent: campaigns/annotators are only created if missing, and a synthetic
rating is only added if that annotator has none yet for that segment. Edit the files in
``demo_data/`` to pilot with your own data; see ``demo_data/README.md``.
"""

import csv
import json
import re
import hashlib
import random
from pathlib import Path

_DIR = Path(__file__).resolve().parent / "demo_data"
DEMO_PREFIX = "[DEMO] "
_COLORS = ["#d4537e", "#378add", "#ba7517", "#1d9e75", "#6b51b8", "#c84a4a"]
_SAMPLE_NOTE = ("  (Demo/sample data: the translations are illustrative example MT outputs; "
                "replace the segments with your own system outputs before a real evaluation.)")

_SEG_FIELDS = ["id", "source", "target", "target_a", "target_b", "reference",
               "system", "system_a", "system_b", "domain", "difficulty"]
_TOKEN_RE = re.compile(r"\S+")


def _slug(s, fallback="x"):
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s or "").strip("_").lower()
    return s or fallback


def _load_manifest():
    with open(_DIR / "campaigns.json", encoding="utf-8") as f:
        return json.load(f)


def _read_segments(rel_path):
    p = _DIR / rel_path
    if p.suffix.lower() == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    rows = []
    with open(p, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            seg = {}
            for k, v in row.items():
                if k in _SEG_FIELDS and v is not None and str(v).strip():
                    seg[k] = str(v).strip()
            if seg:
                rows.append(seg)
    return rows


def _criteria(items):
    out = []
    for i, it in enumerate(items or []):
        name = it["name"]
        out.append({"id": _slug(name, f"criterion_{i+1}"), "name": name,
                    "color": _COLORS[i % len(_COLORS)], "desc": "", "guide": it.get("guide", "")})
    return out


def _campaign_kwargs(cdef):
    kw = dict(
        name=DEMO_PREFIX + cdef["name"],
        task_type=cdef.get("task_type", "translation"),
        input_label=cdef.get("input_label", ""),
        output_label=cdef.get("output_label", ""),
        source_language=cdef.get("source_language", ""),
        target_language=cdef.get("target_language", ""),
        eval_mode=cdef.get("eval_mode", "likert"),
        segments_per_page=int(cdef.get("segments_per_page", 3)),
        enable_spans=bool(cdef.get("enable_spans", False)),
        span_scope=cdef.get("span_scope", "target"),
        instructions=(cdef.get("instructions", "") + _SAMPLE_NOTE),
        span_instructions=cdef.get("span_instructions", ""),
        criteria_json=json.dumps(_criteria(cdef.get("criteria")), ensure_ascii=False),
        segments_json=json.dumps(_read_segments(cdef["segments_file"]), ensure_ascii=False),
    )
    if cdef.get("eval_mode") == "pairwise":
        prefs = [{"id": _slug(lb, f"option_{i+1}"), "label": lb}
                 for i, lb in enumerate(cdef.get("preferences", []))]
        kw["preferences_json"] = json.dumps(prefs, ensure_ascii=False)
    if cdef.get("scale_type"):
        kw["scale_type"] = cdef["scale_type"]
    if cdef.get("scale_points"):
        kw["scale_points"] = int(cdef["scale_points"])
    if cdef.get("scale_design"):
        kw["scale_design"] = cdef["scale_design"]
    if cdef.get("scale_labels"):
        kw["scale_labels_json"] = json.dumps(cdef["scale_labels"], ensure_ascii=False)
    return kw


def create_demo_campaigns(db, Campaign):
    """Create any demo campaigns that don't already exist. Returns names created."""
    created = []
    for cdef in _load_manifest().get("campaigns", []):
        kw = _campaign_kwargs(cdef)
        if Campaign.query.filter_by(name=kw["name"]).first():
            continue
        db.session.add(Campaign(**kw))
        created.append(kw["name"])
    if created:
        db.session.commit()
    return created


def create_demo_annotators(db, Annotator):
    """Create the demo annotators (idempotent by email). Returns (objects, n_created)."""
    anns = []
    created = 0
    for a in _load_manifest().get("annotators", []):
        existing = Annotator.query.filter_by(email=a["email"]).first()
        if existing:
            anns.append(existing)
            continue
        ann = Annotator(name=a["name"], email=a["email"])
        ann.set_password(a.get("password", "demopass123"))
        db.session.add(ann)
        anns.append(ann)
        created += 1
    if created:
        db.session.commit()
    return anns, created


# ---------------------------------------------------------------------------
# synthetic ratings (deterministic; partial agreement so IAA is non-trivial)
# ---------------------------------------------------------------------------

def _seeded(*parts):
    h = hashlib.sha1("||".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return random.Random(int(h[:12], 16))


def _token_spans(text):
    return [(m.start(), m.end()) for m in _TOKEN_RE.finditer(text or "")]


def _synth_rating(campaign, ann_index, seg, criteria):
    """Return (scores, spans, preference, reviewed, edited_text) for one annotator/segment."""
    sid = seg.get("id", "")
    mode = campaign.mode
    base = _seeded(campaign.name, sid)              # shared base across annotators
    noise = _seeded(campaign.name, sid, ann_index)  # per-annotator deviation
    scores, spans, preference, reviewed, edited_text = {}, {}, "", False, None

    if mode == "pairwise":
        opts = [p["id"] for p in campaign.preferences]
        if opts:
            bi = base.randrange(len(opts))
            if noise.random() < 0.3:                # 30% drift to an adjacent option
                bi = min(len(opts) - 1, max(0, bi + noise.choice([-1, 1])))
            preference = opts[bi]
        return scores, spans, preference, True, None

    if mode == "post_edit":
        original = seg.get("target", "") or ""
        # ~55% of outputs get a small correction; the rest are left unchanged.
        if original and noise.random() < 0.55:
            edited = original
            if not edited.endswith("."):
                edited = edited + "."          # small, deterministic correction
            else:
                edited = edited[:-1] + " ।"     # tweak punctuation
            edited_text = edited
        else:
            edited_text = original
        return scores, spans, preference, True, edited_text

    lo, hi = campaign.score_bounds
    if mode == "likert":
        for c in criteria:
            b = lo + base.randrange(max(1, hi - lo))     # base in [lo, hi-1]
            b = b + base.randrange(2)                    # nudge up sometimes
            s = b + noise.choice([-1, 0, 0, 0, 1])
            scores[c["id"]] = int(min(hi, max(lo, s)))
        reviewed = True

    if mode == "span_only" or campaign.enable_spans:
        target = seg.get("target", "")
        toks = _token_spans(target)
        if toks and criteria and base.random() < 0.7:    # 70% of segments carry an error
            ttype = criteria[base.randrange(len(criteria))]["id"]
            ts, te = toks[base.randrange(len(toks))]
            if noise.random() < 0.85:                     # 85% chance this annotator marks it
                js = ts + noise.choice([0, 0, -1, 1])     # small boundary jitter
                je = te + noise.choice([0, 0, -1, 1])
                js = max(0, js); je = min(len(target), max(js + 1, je))
                spans[ttype] = [[js, je]]
        reviewed = True

    return scores, spans, preference, reviewed, edited_text


def synthesize_ratings(db, Campaign, Annotator, Rating):
    """Add synthetic ratings for the demo annotators on every demo campaign. Idempotent."""
    anns, _ = create_demo_annotators(db, Annotator)
    if not anns:
        return 0
    created = 0
    demo_campaigns = Campaign.query.filter(Campaign.name.like(DEMO_PREFIX + "%")).all()
    for c in demo_campaigns:
        criteria = c.criteria or []
        segs = c.segments
        for ai, ann in enumerate(anns):
            for si, seg in enumerate(segs):
                # Annotator 3 leaves the final segment of each campaign undone, to show
                # in-progress state and partial (still computable) overlap.
                if ai == len(anns) - 1 and si == len(segs) - 1:
                    continue
                sid = seg.get("id", "")
                if Rating.query.filter_by(campaign_id=c.id, annotator_id=ann.id,
                                          segment_id=sid).first():
                    continue
                scores, spans, pref, reviewed, edited = _synth_rating(c, ai, seg, criteria)
                r = Rating(campaign_id=c.id, annotator_id=ann.id, segment_id=sid)
                r.scores_json = json.dumps(scores, ensure_ascii=False)
                r.spans_json = json.dumps(spans, ensure_ascii=False)
                r.preference = pref
                r.reviewed = reviewed
                if edited is not None:
                    r.edited_text = edited
                is_elig = c.ai_ab_eligible(ann.id, sid) if c.ai_ab_enabled else bool(c.ai_enabled)
                r.ai_eligible = is_elig
                r.ai_arm = ("ai_available" if is_elig else "control") if c.ai_ab_enabled else ("ai_available" if c.ai_enabled else "no_ai")
                db.session.add(r)
                created += 1
    if created:
        db.session.commit()
    return created


def seed_all(db, Campaign, Annotator, Rating):
    """Load demo campaigns + annotators + synthetic ratings. Returns a summary dict."""
    campaigns = create_demo_campaigns(db, Campaign)
    _, annotators = create_demo_annotators(db, Annotator)
    ratings = synthesize_ratings(db, Campaign, Annotator, Rating)
    return {"campaigns": campaigns, "annotators": annotators, "ratings": ratings}


def demo_present(Campaign):
    """True if any demo campaign currently exists."""
    return Campaign.query.filter(Campaign.name.like(DEMO_PREFIX + "%")).first() is not None


def unload_demo(db, Campaign, Annotator, Rating, AssistantLog=None):
    """Remove all demo campaigns, the demo annotators, and their ratings/logs. Idempotent.

    Only touches the prefixed demo campaigns and the annotators listed in the manifest, so
    real campaigns and real annotators are never affected.
    """
    demo_campaigns = Campaign.query.filter(Campaign.name.like(DEMO_PREFIX + "%")).all()
    cids = [c.id for c in demo_campaigns]
    emails = [a["email"] for a in _load_manifest().get("annotators", [])]
    demo_anns = (Annotator.query.filter(Annotator.email.in_(emails)).all() if emails else [])
    aids = [a.id for a in demo_anns]

    n_ratings = 0
    # Delete dependent rows first (ratings + assistant logs), by campaign and by annotator.
    if cids:
        n_ratings += Rating.query.filter(Rating.campaign_id.in_(cids)).delete(synchronize_session=False)
        if AssistantLog is not None:
            AssistantLog.query.filter(AssistantLog.campaign_id.in_(cids)).delete(synchronize_session=False)
    if aids:
        n_ratings += Rating.query.filter(Rating.annotator_id.in_(aids)).delete(synchronize_session=False)
        if AssistantLog is not None:
            AssistantLog.query.filter(AssistantLog.annotator_id.in_(aids)).delete(synchronize_session=False)

    for c in demo_campaigns:
        db.session.delete(c)
    for a in demo_anns:
        db.session.delete(a)
    db.session.commit()
    return {"campaigns": len(demo_campaigns), "annotators": len(demo_anns), "ratings": n_ratings}
