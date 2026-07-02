"""Database models for the MT evaluation platform.

There is one global admin (credentials in env vars, no DB row).
Annotators are global (one account works across campaigns).
Each campaign has its own segments and ratings.
"""

import json
import uuid
from datetime import datetime, timedelta

import bcrypt
from extensions import db


def _uuid():
    return uuid.uuid4().hex


# Standard definitions for common criteria / error types, shown to annotators and used as
# the AI assistant's fallback guidance when the admin hasn't written a custom definition.
CRITERION_GUIDES = {
    # translation
    "Adequacy": "Does the output convey the full meaning of the input, without adding or losing information?",
    "Fluency": "Is the output grammatical and natural in the target language, readable on its own?",
    "Meaning preservation": "Is the original meaning fully preserved, with no distortion of intent?",
    # summarization
    "Coherence": "Is the text well-structured and logically organized, so it reads as a unified whole?",
    "Consistency": "Are all statements supported by the source, with no hallucinated or contradictory facts?",
    "Relevance": "Does it capture the most important information and leave out trivia?",
    # simplification
    "Simplicity": "Is the result genuinely easier to read (simpler words and sentence structure) than the original?",
    # dialogue / general
    "Helpfulness": "Does the response actually address the user's need usefully and completely?",
    "Safety": "Is the response free of harmful, unsafe, or inappropriate content?",
    "Overall quality": "Your overall judgment of how good this output is for its purpose.",
    # QA
    "Correctness": "Is the answer factually correct and responsive to the question?",
    "Completeness": "Does the answer cover all parts of the question, omitting nothing important?",
    # MT / MQM error types
    "Mistranslation": "The output says something different from the source.",
    "Omission": "Source content is missing from the output.",
    "Addition": "The output adds content not present in the source.",
    "Grammar": "Grammatical or morphological error in the output.",
    # factuality / hallucination error types
    "Unsupported (hallucination)": "A claim that is not supported by the source/context at all.",
    "Contradicts source": "A claim that directly conflicts with the source/context.",
    "Misattribution": "A fact, quote, or action attributed to the wrong entity.",
    "Incorrect fact": "A factual statement that is simply wrong.",
    "Fabricated detail": "An invented specific detail (name, number, date, citation) with no basis.",
}


def criterion_guide(name):
    return CRITERION_GUIDES.get(name, "")


# Supported evaluation task types. Each maps to default input/output panel labels and a
# set of example criteria the admin form can pre-fill. "translation" preserves the original
# MT behaviour; the rest let the same modes serve other tasks.
#
# ``cross_lingual`` records how many languages the task involves, which drives whether the
# source/target language fields are required and whether annotators are asked for their
# fluency in *both* the source and target languages:
#   "required" -> the task is inherently bilingual; source and target differ (e.g. MT).
#   "optional" -> usually monolingual but sometimes cross-lingual (e.g. cross-lingual
#                 summarization or QA); collect both-language fluency only when the admin
#                 has actually set two different languages.
#   "none"     -> strictly single-language; only one language matters (e.g. text
#                 simplification, dialogue/response).
# ``bilingual`` (kept for backwards compatibility) is True exactly when cross_lingual is
# "required".
TASK_TYPES = {
    "translation":   {"name": "Machine translation",   "input": "Source",        "output": "Translation",
                      "criteria": ["Adequacy", "Fluency"], "bilingual": True,  "cross_lingual": "required"},
    "summarization": {"name": "Summarization",          "input": "Document",      "output": "Summary",
                      "criteria": ["Coherence", "Consistency", "Fluency", "Relevance"],
                      "bilingual": False, "cross_lingual": "optional"},
    "simplification": {"name": "Text simplification",   "input": "Original text", "output": "Simplified text",
                      "criteria": ["Meaning preservation", "Simplicity", "Fluency"],
                      "bilingual": False, "cross_lingual": "none"},
    "dialogue":      {"name": "Dialogue / response",    "input": "Conversation",  "output": "Response",
                      "criteria": ["Helpfulness", "Coherence", "Safety"],
                      "bilingual": False, "cross_lingual": "none"},
    "qa":            {"name": "Question answering",     "input": "Question",      "output": "Answer",
                      "criteria": ["Correctness", "Completeness", "Fluency"],
                      "bilingual": False, "cross_lingual": "optional"},
    "factuality":    {"name": "Factuality / hallucination", "input": "Source / context", "output": "Output",
                      "criteria": ["Unsupported (hallucination)", "Contradicts source",
                                   "Misattribution", "Incorrect fact", "Fabricated detail"],
                      "bilingual": False, "cross_lingual": "optional"},
    "general":       {"name": "General LLM output",     "input": "Input",         "output": "Output",
                      "criteria": ["Overall quality"], "bilingual": False, "cross_lingual": "optional"},
    "custom":        {"name": "Custom",                 "input": "Input",         "output": "Output",
                      "criteria": [], "bilingual": False, "cross_lingual": "optional"},
}


def task_defaults(task_type):
    return TASK_TYPES.get(task_type or "translation", TASK_TYPES["translation"])


# Accepted spellings for an admin-provided segment "difficulty" label.
_DIFF_NORM = {
    "easy": "easy", "e": "easy", "low": "easy", "1": "easy", "simple": "easy",
    "medium": "medium", "med": "medium", "m": "medium", "2": "medium", "moderate": "medium",
    "hard": "hard", "h": "hard", "high": "hard", "3": "hard", "difficult": "hard", "complex": "hard",
}


def task_criteria(task_type):
    """Suggested criteria for a task as [{name, guide}], using the guide library."""
    return [{"name": n, "guide": CRITERION_GUIDES.get(n, "")}
            for n in task_defaults(task_type).get("criteria", [])]


# Self-rated language proficiency levels, collected at registration for the source and/or
# target language of a cross-lingual task. Ordered from most to least proficient. Stored on
# the annotator by id ("" = not provided).
FLUENCY_LEVELS = [
    {"id": "native",       "label": "Native speaker"},
    {"id": "fluent",       "label": "Fluent"},
    {"id": "advanced",     "label": "Advanced"},
    {"id": "intermediate", "label": "Intermediate"},
    {"id": "beginner",     "label": "Beginner"},
]
FLUENCY_IDS = {lvl["id"] for lvl in FLUENCY_LEVELS}
_FLUENCY_LABELS = {lvl["id"]: lvl["label"] for lvl in FLUENCY_LEVELS}


def normalize_fluency(value):
    """Return a valid fluency id or "" for anything unrecognised."""
    v = (value or "").strip().lower()
    return v if v in FLUENCY_IDS else ""


def fluency_label(value):
    """Human-readable label for a stored fluency id (empty string if unset/unknown)."""
    return _FLUENCY_LABELS.get((value or "").strip().lower(), "")


class Annotator(db.Model):
    """Global annotator account. Same login works for any campaign they join."""
    __tablename__ = "annotators"

    id = db.Column(db.String(32), primary_key=True, default=_uuid)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(200), nullable=False)

    otp_hash = db.Column(db.String(200), default="")
    otp_expires_at = db.Column(db.DateTime)
    otp_attempts = db.Column(db.Integer, default=0)

    native_language = db.Column(db.String(100), default="")
    expertise = db.Column(db.String(20), default="")   # "" | "easy" | "medium" | "hard"
    # Self-rated proficiency in the source and target languages of a cross-lingual task.
    # One of FLUENCY_IDS or "" (not provided). Collected once at registration for bilingual
    # tasks; carried on the global account like native_language / expertise.
    source_fluency = db.Column(db.String(20), default="")
    target_fluency = db.Column(db.String(20), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    ratings = db.relationship("Rating", backref="annotator", lazy="dynamic")

    @property
    def source_fluency_label(self):
        return fluency_label(self.source_fluency)

    @property
    def target_fluency_label(self):
        return fluency_label(self.target_fluency)

    def set_password(self, raw):
        self.password_hash = bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def check_password(self, raw):
        try:
            return bcrypt.checkpw(raw.encode("utf-8"), self.password_hash.encode("utf-8"))
        except (ValueError, AttributeError):
            return False
        
    def set_otp(self, code, ttl_minutes=15):
        self.otp_hash = bcrypt.hashpw(
            code.encode("utf-8"),
            bcrypt.gensalt()
        ).decode("utf-8")

        self.otp_expires_at = datetime.utcnow() + timedelta(minutes=ttl_minutes)
        self.otp_attempts = 0


    def check_otp(self, code):
        if not self.otp_hash or not self.otp_expires_at:
            return False

        if datetime.utcnow() > self.otp_expires_at:
            return False

        try:
            return bcrypt.checkpw(
                code.encode("utf-8"),
                self.otp_hash.encode("utf-8")
            )
        except (ValueError, AttributeError):
            return False
        
    def clear_otp(self):
        self.otp_hash = ""
        self.otp_expires_at = None
        self.otp_attempts = 0


class Admin(db.Model):
    """A self-registered admin account, verified by an emailed OTP.

    The environment-variable admin (MT_EVAL_ADMIN_EMAIL/PASSWORD) still works and does not
    need a row here; this table is for additional admins who sign up through the UI.
    """
    __tablename__ = "admins"

    id = db.Column(db.String(32), primary_key=True, default=_uuid)
    email = db.Column(db.String(200), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(200), nullable=False)
    is_verified = db.Column(db.Boolean, default=False)
    otp_hash = db.Column(db.String(200), default="")
    otp_expires_at = db.Column(db.DateTime)
    otp_attempts = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw):
        self.password_hash = bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def check_password(self, raw):
        try:
            return bcrypt.checkpw(raw.encode("utf-8"), self.password_hash.encode("utf-8"))
        except (ValueError, AttributeError):
            return False

    def set_otp(self, code, ttl_minutes=15):
        self.otp_hash = bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        self.otp_expires_at = datetime.utcnow() + timedelta(minutes=ttl_minutes)
        self.otp_attempts = 0

    def check_otp(self, code):
        if not self.otp_hash or not self.otp_expires_at:
            return False
        if datetime.utcnow() > self.otp_expires_at:
            return False
        try:
            return bcrypt.checkpw(code.encode("utf-8"), self.otp_hash.encode("utf-8"))
        except (ValueError, AttributeError):
            return False

    def clear_otp(self):
        self.otp_hash = ""
        self.otp_expires_at = None
        self.otp_attempts = 0

class CampaignGroup(db.Model):
    """A named collection of related campaigns (e.g. easy / medium / hard variants of one
    study, or several systems evaluated separately). Lets an admin view and export the
    campaigns' results together. Deleting a group never deletes its campaigns -- they are
    simply un-grouped (group_id set back to NULL)."""
    __tablename__ = "campaign_groups"

    id = db.Column(db.String(32), primary_key=True, default=_uuid)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    owner_email = db.Column(db.String(200), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    campaigns = db.relationship(
        "Campaign", backref="group", lazy="dynamic",
        # On group delete, detach campaigns rather than cascading the delete.
        passive_deletes=True,
    )

    def ordered_campaigns(self):
        """Campaigns in this group, oldest first (stable display order)."""
        return list(self.campaigns.order_by(Campaign.created_at.asc()))


class Campaign(db.Model):
    """One evaluation campaign created by the admin."""
    __tablename__ = "campaigns"

    id = db.Column(db.String(32), primary_key=True, default=_uuid)
    name = db.Column(db.String(200), nullable=False)
    source_language = db.Column(db.String(100), nullable=False, default="")
    target_language = db.Column(db.String(100), nullable=False, default="")
    # What kind of evaluation task this campaign is. "translation" keeps the original
    # MT behaviour; other values relabel the interface for non-MT tasks.
    task_type = db.Column(db.String(40), default="translation")
    # What to call the input/output panels shown to annotators. When blank, sensible
    # defaults are derived from task_type (see TASK_TYPES / io_labels()).
    input_label = db.Column(db.String(80), default="")
    output_label = db.Column(db.String(80), default="")
    # How segment difficulty (easy/medium/hard) is decided when not labelled per-segment.
    # "auto" = composite heuristic (terciles), "length" = character thresholds, "manual" =
    # only use explicit per-segment labels.
    difficulty_method = db.Column(db.String(20), default="auto")
    difficulty_easy_max = db.Column(db.Integer, default=0)   # length method: easy if <= this
    difficulty_hard_min = db.Column(db.Integer, default=0)   # length method: hard if >= this
    # Which difficulty levels to actually serve to annotators (comma-sep subset of
    # easy/medium/hard). Empty = serve everything. Lets an admin match a campaign's
    # difficulty to the annotators' expertise (e.g. an "easy only" set for newcomers).
    served_difficulties = db.Column(db.String(50), default="")
    # When on, each annotator picks an expertise level at registration and is served only
    # the matching difficulty (beginner→easy, intermediate→medium, advanced→hard). When off
    # (default), expertise isn't asked and the campaign-level served_difficulties filter applies.
    expertise_matching = db.Column(db.Boolean, default=False)
    # Rating-page layout: where the rating/preference panel and the span section sit.
    rating_position = db.Column(db.String(10), default="side")   # "side" | "below"
    span_position = db.Column(db.String(10), default="below")    # "below" | "beside"
    owner_email = db.Column(db.String(200), default="")          # who created it (results email)
    # Optional grouping: campaigns that belong together (e.g. easy/medium/hard variants of
    # the same study) share a group so their results can be viewed and exported together.
    group_id = db.Column(db.String(32), db.ForeignKey("campaign_groups.id"),
                         nullable=True, index=True, default=None)
    # Writing system(s). `script` is the legacy single field (kept for backward
    # compatibility and as a fallback); source_script / target_script record the script of
    # the input (source) and output (target) language separately, so e.g. a script-
    # conversion campaign can have the same language on both sides in different scripts.
    # Each is a SCRIPT_OPTIONS id or None.
    script = db.Column(db.String(50), default=None)
    source_script = db.Column(db.String(50), default=None)
    target_script = db.Column(db.String(50), default=None)
    # Segments as a JSON-encoded list of dicts {id, source, target, reference?, system?, domain?}
    segments_json = db.Column(db.Text, nullable=False)
    # Criteria as a JSON-encoded list of dicts {id, name, color, desc, guide}
    # If not set, the app falls back to the built-in defaults.
    criteria_json = db.Column(db.Text, default="")
    # Free-text annotation instructions shown to annotators (Markdown-ish, rendered as text).
    instructions = db.Column(db.Text, default="")
    # Free-text instructions shown in the span-annotation section (admin-provided).
    # If blank, the app falls back to the built-in default span help text.
    span_instructions = db.Column(db.Text, default="")
    # Where span annotation is allowed: "target" (default) or "both"
    span_scope = db.Column(db.String(20), default="target")
    # Whether span annotation is enabled at all
    enable_spans = db.Column(db.Boolean, default=True)
    # How many segments appear on each page of the annotator's rating screen.
    segments_per_page = db.Column(db.Integer, default=3)

    # ---- Evaluation mode -------------------------------------------------
    # "likert"    -> score each criterion on a scale (optionally + span annotation)
    # "pairwise"  -> choose between two candidate translations (admin-defined options)
    # "span_only" -> mark error spans only, no scoring
    eval_mode = db.Column(db.String(20), default="likert")
    # Pairwise preference options as a JSON list of {id, label}. Only used when
    # eval_mode == "pairwise".
    preferences_json = db.Column(db.Text, default="")

    # ---- Rating scale configuration -------------------------------------
    # "likert"     -> discrete N-point scale (default)
    # "continuous" -> a slider over an integer range (e.g. Direct Assessment 0-100)
    scale_type = db.Column(db.String(20), default="likert")
    # Number of discrete points for a likert scale (e.g. 5, 7).
    scale_points = db.Column(db.Integer, default=5)
    # Bounds for a continuous (slider) scale.
    scale_min = db.Column(db.Integer, default=0)
    scale_max = db.Column(db.Integer, default=100)
    # How the scale is drawn for annotators.
    #   likert:     "circles" | "buttons" | "radio" | "stars"
    #   continuous: "slider"
    scale_design = db.Column(db.String(20), default="circles")
    # Optional labels. For likert: JSON {"1": "Very poor", ...}; for continuous:
    # JSON {"min": "Worst", "max": "Perfect"}. May be partial or empty.
    scale_labels_json = db.Column(db.Text, default="")
    # Google Drive folder ID where data is mirrored
    drive_folder_id = db.Column(db.String(200), default="")
    # Most recent Drive sync status, for display in admin UI
    drive_last_status = db.Column(db.String(20), default="")     # "ok", "error", or ""
    drive_last_error = db.Column(db.Text, default="")
    drive_last_sync_at = db.Column(db.DateTime, default=None)
    # Frozen results snapshot (JSON) computed when the campaign is closed.
    results_snapshot_json = db.Column(db.Text, default="")

    # ---- AI assistant configuration (optional, per campaign) -------------
    ai_enabled = db.Column(db.Boolean, default=False)
    # Randomised AI experiment: when on, the assistant is available on a random ai_ab_fraction%
    # of (annotator, segment) pairs, so assisted vs not can be compared causally (ITT).
    ai_ab_enabled = db.Column(db.Boolean, default=False)
    ai_ab_fraction = db.Column(db.Integer, default=50)
    ai_provider = db.Column(db.String(40), default="")   # ollama|anthropic|openai|gemini|openai_compatible
    ai_model = db.Column(db.String(120), default="")
    ai_base_url = db.Column(db.String(300), default="")
    ai_api_key_enc = db.Column(db.Text, default="")      # encrypted at rest
    # Set when admin closes the campaign -- editing disabled afterward
    closed_at = db.Column(db.DateTime, default=None)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    ratings = db.relationship("Rating", backref="campaign", lazy="dynamic", cascade="all,delete-orphan")

    @property
    def is_closed(self):
        return self.closed_at is not None

    def io_labels(self):
        """(input_label, output_label) for the annotator UI, with task-aware defaults."""
        d = task_defaults(self.task_type)
        return (self.input_label or d["input"], self.output_label or d["output"])

    @property
    def source_script_label(self):
        from utils.constants import script_label
        return script_label(self.source_script or self.script)

    @property
    def target_script_label(self):
        from utils.constants import script_label
        return script_label(self.target_script or self.script)

    @property
    def scripts_summary(self):
        """Short human string for the campaign's script(s), or '' if none set.

        Examples: 'Bengali–Assamese → Latin', 'Latin' (monolingual/one side),
        'Meitei Mayek → Bengali–Assamese' (same language, different scripts)."""
        src = self.source_script_label
        tgt = self.target_script_label
        if src and tgt:
            return src if src == tgt else f"{src} → {tgt}"
        return src or tgt

    @property
    def lang_pair_label(self):
        """Language pair with per-side script in parentheses where set, e.g.
        'English (Latin) → Assamese (Bengali–Assamese)'. Falls back gracefully when a
        script or a side is missing."""
        def side(lang, script):
            lang = (lang or "").strip()
            if lang and script:
                return f"{lang} ({script})"
            return lang
        src = side(self.source_language, self.source_script_label)
        tgt = side(self.target_language, self.target_script_label)
        if src and tgt:
            return f"{src} → {tgt}"
        return src or tgt

    @property
    def is_bilingual(self):
        """True for tasks with distinct source/target languages (e.g. translation)."""
        return bool(task_defaults(self.task_type).get("bilingual"))

    @property
    def cross_lingual_mode(self):
        """"required" | "optional" | "none" -- how many languages the task type involves."""
        return task_defaults(self.task_type).get("cross_lingual", "none")

    @property
    def is_cross_lingual(self):
        """True when this specific campaign actually spans two languages.

        Always true for inherently bilingual tasks (MT). For "optional" tasks (e.g.
        cross-lingual summarization / QA) it's true only when the admin set two distinct
        source and target languages. Never true for strictly monolingual tasks.
        """
        mode = self.cross_lingual_mode
        if mode == "required":
            return True
        if mode == "none":
            return False
        src = (self.source_language or "").strip()
        tgt = (self.target_language or "").strip()
        return bool(src and tgt and src.casefold() != tgt.casefold())

    def has_explicit_difficulty(self):
        return any(_DIFF_NORM.get(str(s.get("difficulty", "")).strip().lower())
                   for s in self.segments)

    def served_difficulty_set(self):
        """Which difficulty levels to serve to annotators, or None for all.

        Returns a set like {"easy", "medium"} or None (serve everything). None is also
        returned when difficulty grouping is off, since there's nothing to filter by.
        """
        if (self.difficulty_method or "auto") == "none":
            return None
        raw = (self.served_difficulties or "").strip().lower()
        if not raw:
            return None
        levels = {p.strip() for p in raw.split(",") if p.strip() in ("easy", "medium", "hard")}
        # All three (or none parsed) means "no filtering".
        if not levels or levels == {"easy", "medium", "hard"}:
            return None
        return levels

    def served_segments(self):
        """Segments visible to annotators after applying the difficulty-serving filter.

        When a subset is selected, only segments whose computed/labelled difficulty is in
        that subset are served; segments with unknown difficulty are excluded while a filter
        is active. With no filter (or difficulty off), every segment is served.
        """
        levels = self.served_difficulty_set()
        if not levels:
            return self.segments
        diffs = self.difficulty_for_segments()
        return [s for s in self.segments if diffs.get(s.get("id")) in levels]

    def ai_ab_eligible(self, annotator_id, segment_id):
        """Whether the AI assistant is available for this (annotator, segment).

        With the randomised experiment off, AI is available for all (subject to ai_enabled).
        With it on, a deterministic per-pair hash puts ai_ab_fraction% of pairs in the
        AI-available arm, so eligibility is random but stable across reloads.
        """
        if not self.ai_ab_enabled:
            return True
        frac = max(0, min(100, self.ai_ab_fraction or 0))
        if frac <= 0:
            return False
        if frac >= 100:
            return True
        import hashlib
        h = hashlib.sha1(f"{self.id}\u0001{annotator_id}\u0001{segment_id}".encode("utf-8")).hexdigest()
        return (int(h[:8], 16) % 100) < frac

    def served_segments_for(self, annotator):
        """Segments served to a specific annotator.

        When ``expertise_matching`` is on and the annotator has chosen an expertise level,
        serve only segments of that difficulty (beginner→easy, intermediate→medium,
        advanced→hard). Otherwise fall back to the campaign-level served set.
        """
        if (self.expertise_matching and annotator is not None
                and getattr(annotator, "expertise", "")
                and (self.difficulty_method or "auto") != "none"):
            lvl = annotator.expertise
            diffs = self.difficulty_for_segments()
            return [s for s in self.segments if diffs.get(s.get("id")) == lvl]
        return self.served_segments()

    def difficulty_for_segments(self):
        """Return {segment_id: 'easy'|'medium'|'hard'}.

        An admin-provided ``difficulty`` field on a segment always wins. For the rest, the
        campaign's ``difficulty_method`` decides:
          - "manual": leave unlabelled segments out (no auto-classification);
          - "length": bucket by character length (custom easy/hard thresholds if set, else terciles);
          - "auto"  : bucket by a composite difficulty score (length + long-word ratio +
                      punctuation/number density + unusual-character ratio), via terciles.
        """
        import re as _re
        segs = self.segments
        method = (self.difficulty_method or "auto")
        if method == "none":
            return {}        # difficulty grouping switched off entirely

        def text_of(s):
            return s.get("source") or s.get("target") or s.get("target_a") or ""

        def composite_score(s):
            t = text_of(s) or ""
            n = len(t) or 1
            words = t.split()
            wlens = [len(w) for w in words] or [0]
            avg_wlen = sum(wlens) / len(wlens)
            long_ratio = sum(1 for w in words if len(w) >= 10) / (len(words) or 1)
            punct_digit = sum(1 for ch in t if ch.isdigit() or (not ch.isalnum() and not ch.isspace()))
            pd_ratio = punct_digit / n
            unusual = sum(1 for ch in t if ord(ch) > 0x2000) / n  # rare/uncommon code points
            return {"len": len(t), "avg_wlen": avg_wlen, "long_ratio": long_ratio,
                    "pd_ratio": pd_ratio, "unusual": unusual}

        out = {}
        need_auto = []
        for s in segs:
            d = _DIFF_NORM.get(str(s.get("difficulty", "")).strip().lower())
            if d:
                out[s.get("id")] = d
            else:
                need_auto.append(s)
        if not need_auto:
            return out
        if method == "manual":
            for s in need_auto:
                out[s.get("id")] = ""        # unknown / unlabelled
            return out

        if method == "length":
            emax = self.difficulty_easy_max or 0
            hmin = self.difficulty_hard_min or 0
            if emax and hmin and emax < hmin:
                for s in need_auto:
                    L = len(text_of(s))
                    out[s.get("id")] = "easy" if L <= emax else ("hard" if L >= hmin else "medium")
                return out
            # fall back to length terciles
            lengths = sorted(len(text_of(s)) for s in segs)
            n = len(lengths)
            q1 = lengths[n // 3] if n >= 3 else None
            q2 = lengths[(2 * n) // 3] if n >= 3 else None
            for s in need_auto:
                L = len(text_of(s))
                out[s.get("id")] = ("medium" if q1 is None else
                                    "easy" if L <= q1 else "medium" if L <= q2 else "hard")
            return out

        # method == "auto": composite score, min-max normalised across the campaign, terciles.
        feats = {s.get("id"): composite_score(s) for s in segs}
        keys = ["len", "avg_wlen", "long_ratio", "pd_ratio", "unusual"]
        ranges = {}
        for k in keys:
            vals = [feats[i][k] for i in feats]
            lo, hi = min(vals), max(vals)
            ranges[k] = (lo, hi - lo if hi > lo else 1.0)
        scores = {}
        for sid, f in feats.items():
            scores[sid] = sum((f[k] - ranges[k][0]) / ranges[k][1] for k in keys) / len(keys)
        ordered = sorted(scores.values())
        m = len(ordered)
        q1 = ordered[m // 3] if m >= 3 else None
        q2 = ordered[(2 * m) // 3] if m >= 3 else None
        for s in need_auto:
            sc = scores[s.get("id")]
            out[s.get("id")] = ("medium" if q1 is None else
                                "easy" if sc <= q1 else "medium" if sc <= q2 else "hard")
        return out

    @property
    def segments(self):
        try:
            return json.loads(self.segments_json or "[]")
        except json.JSONDecodeError:
            return []

    @property
    def criteria(self):
        """Return campaign-specific criteria if set, else None (caller should default)."""
        if not self.criteria_json:
            return None
        try:
            return json.loads(self.criteria_json)
        except json.JSONDecodeError:
            return None

    DEFAULT_LIKERT_LABELS = {
        "1": "Very poor", "2": "Poor", "3": "Acceptable", "4": "Good", "5": "Excellent",
    }

    @property
    def scale_labels(self):
        """Decoded scale label mapping (may be empty)."""
        if not self.scale_labels_json:
            return {}
        try:
            d = json.loads(self.scale_labels_json)
            return d if isinstance(d, dict) else {}
        except json.JSONDecodeError:
            return {}

    @property
    def score_bounds(self):
        """Return (lo, hi): the inclusive integer range a valid score must fall in."""
        if (self.scale_type or "likert") == "continuous":
            lo = self.scale_min if self.scale_min is not None else 0
            hi = self.scale_max if self.scale_max is not None else 100
            if hi < lo:
                lo, hi = hi, lo
            return (lo, hi)
        pts = self.scale_points or 5
        return (1, max(2, pts))

    @property
    def scale_config(self):
        """Normalized scale description consumed by the rating template and JS."""
        stype = self.scale_type or "likert"
        labels = self.scale_labels
        if stype == "continuous":
            lo, hi = self.score_bounds
            return {
                "type": "continuous",
                "design": self.scale_design or "slider",
                "min": lo,
                "max": hi,
                "min_label": labels.get("min", ""),
                "max_label": labels.get("max", ""),
            }
        # likert
        pts = self.scale_points or 5
        design = self.scale_design or "circles"
        # Fall back to the classic 1-5 wording only for the default 5-point scale.
        if not labels and pts == 5:
            labels = dict(self.DEFAULT_LIKERT_LABELS)
        return {
            "type": "likert",
            "design": design,
            "points": pts,
            "values": list(range(1, pts + 1)),
            "labels": {str(k): v for k, v in labels.items()},
        }

    DEFAULT_PREFERENCES = [
        {"id": "a_much_better", "label": "A is much better"},
        {"id": "a_better",      "label": "A is better"},
        {"id": "tie",           "label": "About the same"},
        {"id": "b_better",      "label": "B is better"},
        {"id": "b_much_better", "label": "B is much better"},
    ]

    @property
    def mode(self):
        return self.eval_mode or "likert"

    @property
    def preferences(self):
        """Decoded pairwise preference options, or the built-in default list."""
        if not self.preferences_json:
            return list(self.DEFAULT_PREFERENCES)
        try:
            d = json.loads(self.preferences_json)
            return d if isinstance(d, list) and d else list(self.DEFAULT_PREFERENCES)
        except json.JSONDecodeError:
            return list(self.DEFAULT_PREFERENCES)

    def rating_is_complete(self, rating, criteria=None):
        """True iff `rating` is complete for this campaign's evaluation mode.

        - likert:    every criterion has a valid in-range score
        - pairwise:  a preference option has been selected
        - span_only: the segment has been explicitly reviewed
        """
        mode = self.mode
        if mode == "pairwise":
            return bool((rating.preference or "").strip())
        if mode == "span_only":
            return bool(rating.reviewed)
        if mode == "post_edit":
            return bool((rating.edited_text or "").strip())
        # likert
        crit_ids = [c.get("id") for c in (criteria if criteria is not None else (self.criteria or []))]
        if not crit_ids:
            return False
        lo, hi = self.score_bounds
        return rating.is_complete_for(crit_ids, lo, hi)

    def config_fingerprint(self):
        """A short hash of all annotator-visible config, used to detect edits
        from the rating page so it can refresh itself live."""
        import hashlib
        parts = [
            self.mode, self.scale_type or "", str(self.scale_points),
            str(self.scale_min), str(self.scale_max), self.scale_design or "",
            self.scale_labels_json or "", self.criteria_json or "",
            self.preferences_json or "", str(self.enable_spans),
            self.span_scope or "", self.span_instructions or "",
            str(self.segments_per_page), self.instructions or "",
            self.rating_position or "", self.span_position or "",
            self.difficulty_method or "", str(self.difficulty_easy_max),
            str(self.difficulty_hard_min), self.served_difficulties or "",
            str(self.expertise_matching), self.segments_json or "",
            str(self.ai_ab_enabled), str(self.ai_ab_fraction),
            "closed" if self.is_closed else "open",
        ]
        return hashlib.sha1("\u0001".join(parts).encode("utf-8")).hexdigest()[:16]

    def pairwise_candidates(self, seg):
        """Normalize a pairwise segment to (text_a, text_b, system_a, system_b).

        Accepts either explicit target_a/target_b (+ optional system_a/system_b),
        or a `candidates` list of two strings/objects.
        """
        if seg.get("target_a") is not None or seg.get("target_b") is not None:
            return (seg.get("target_a", "") or "", seg.get("target_b", "") or "",
                    seg.get("system_a", "") or "", seg.get("system_b", "") or "")
        cands = seg.get("candidates")
        if isinstance(cands, list) and len(cands) >= 2:
            def _txt(x): return x.get("target", "") if isinstance(x, dict) else (x or "")
            def _sys(x): return x.get("system", "") if isinstance(x, dict) else ""
            return (_txt(cands[0]), _txt(cands[1]), _sys(cands[0]), _sys(cands[1]))
        # Last-resort fallback: target + target_b-style alternates
        return (seg.get("target", "") or "", seg.get("target2", "") or seg.get("target_b", "") or "", "", "")

    def segment_by_id(self, seg_id):
        for s in self.segments:
            if s.get("id") == seg_id:
                return s
        return None

    @property
    def num_segments(self):
        return len(self.segments)


class Rating(db.Model):
    """One annotator's rating of one segment in one campaign. Editable until close."""
    __tablename__ = "ratings"

    id = db.Column(db.String(32), primary_key=True, default=_uuid)
    campaign_id = db.Column(db.String(32), db.ForeignKey("campaigns.id"), nullable=False, index=True)
    annotator_id = db.Column(db.String(32), db.ForeignKey("annotators.id"), nullable=False, index=True)
    segment_id = db.Column(db.String(200), nullable=False, index=True)

    # JSON: {criterion_id: int(1-5)}
    scores_json = db.Column(db.Text, default="{}")
    # JSON: {criterion_id: [[start, end], ...]}
    spans_json = db.Column(db.Text, default="{}")

    comments = db.Column(db.Text, default="")
    # Pairwise mode: the selected preference option id.
    preference = db.Column(db.String(100), default="")
    # Span-only mode: whether the annotator has confirmed they reviewed the segment.
    reviewed = db.Column(db.Boolean, default=False)
    # Post-editing mode: the annotator's corrected version of the output.
    edited_text = db.Column(db.Text, default="")
    time_spent_seconds = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("campaign_id", "annotator_id", "segment_id",
                            name="uq_campaign_annotator_segment"),
    )

    def scores_dict(self):
        try:
            return json.loads(self.scores_json or "{}")
        except json.JSONDecodeError:
            return {}

    def spans_dict(self):
        try:
            return json.loads(self.spans_json or "{}")
        except json.JSONDecodeError:
            return {}

    def is_complete_for(self, criterion_ids, lo=1, hi=5):
        """Return True iff every criterion has a valid integer score in [lo, hi]."""
        scores = self.scores_dict()
        return all(isinstance(scores.get(cid), int) and lo <= scores[cid] <= hi
                   for cid in criterion_ids)

class AssistantLog(db.Model):
    """One AI-assistant interaction. Logged for provenance + influence analysis.
    The assistant never labels; this records what it said and what the human did."""
    __tablename__ = "assistant_logs"

    id = db.Column(db.String(32), primary_key=True, default=_uuid)
    campaign_id = db.Column(db.String(32), db.ForeignKey("campaigns.id"), index=True, nullable=False)
    annotator_id = db.Column(db.String(32), db.ForeignKey("annotators.id"), index=True, default=None)
    segment_id = db.Column(db.String(200), default="")
    role = db.Column(db.String(20), default="annotator")   # "annotator" | "admin"
    provider = db.Column(db.String(40), default="")
    model = db.Column(db.String(120), default="")
    question = db.Column(db.Text, default="")
    answer = db.Column(db.Text, default="")
    # what the human did with it: "" | "helpful" | "dismissed" | "reconsidered"
    action = db.Column(db.String(20), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
