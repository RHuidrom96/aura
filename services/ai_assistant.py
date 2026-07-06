# ============================================================================
# AI guideline assistant (grounded, advisory-only, logged)
# ============================================================================

import json

import models


def task_label(campaign):
    return models.task_defaults(getattr(campaign, "task_type", "translation"))["name"]


def _assist_system_prompt():
    return (
        "You are a helpful assistant embedded in a HUMAN evaluation platform where annotators "
        "judge the quality of system outputs (such as translations, summaries, or answers). "
        "You support the annotator in two ways:\n"
        "1) COMPREHENSION: explaining the meaning of words, phrases, or whole sentences in the "
        "input or output text, clarifying terminology, idioms, grammar, register, or nuance. For "
        "this you may freely use your general knowledge of the languages involved; you are not "
        "limited to the campaign guidelines here. Translate or gloss text when asked.\n"
        "2) GUIDELINES: explaining the campaign's instructions, the evaluation criteria and their "
        "definitions, and how the rating interface works. Ground these answers in the provided "
        "GUIDELINES and INTERFACE notes, and if they don't cover something, say so rather than inventing rules.\n"
        "Boundaries: never tell the annotator which score, rating, preference, or span to choose, "
        "and never declare the output definitively correct or incorrect or whether it contains "
        "an error; explaining what the text means is fine, but the quality judgment is the human's. "
        "Be concise and clear."
    )


def _interface_help(campaign):
    mode = campaign.mode
    per = campaign.segments_per_page or 3
    in_label, out_label = campaign.io_labels()
    L = [f"The annotator works through segments page by page ({per} per page); "
        "use \"Save & next\" to continue, and progress is saved automatically."]
    if mode == "likert":
        how = {"circles": "click the numbered circle for each criterion",
            "buttons": "click the labelled button for each criterion",
            "radio": "select the radio option for each criterion",
            "stars": "click the star rating for each criterion",
            "slider": "drag the slider (or click a number) for each criterion"}.get(
                campaign.scale_design or "circles", "record a rating for each criterion")
        L.append("This is a Likert-rating task: " + how + ".")
        if campaign.enable_spans:
            L.append("Error spans can also be marked: pick an error type, then select the words in the text.")
    elif mode == "pairwise":
        L.append(f"This is a pairwise task: two candidate {out_label.lower()} outputs (A and B) "
                "are shown; the annotator picks one preference option comparing them.")
    elif mode == "preference_selection":
        L.append(f"This is a preference-selection task: one source with several candidate "
                f"{out_label.lower()} outputs is shown; the annotator ranks the candidates from "
                "best (1) to worst (ties allowed).")
    elif mode == "span_only":
        L.append("This is a span-annotation task: pick an error type, select the words that "
                "contain the error, then tick \"reviewed\" when done (even if there are no errors).")
    elif mode == "post_edit":
        L.append("This is a post-editing task: the annotator edits the output text directly to "
                "correct any errors (leaving it unchanged is fine if it is already correct).")
    if campaign.is_bilingual:
        L.append("The human reference is hidden until the annotator chooses to reveal it, "
                "to avoid biasing their own judgment.")
    return " ".join(L)


def _assist_user_prompt(campaign, segctx, criteria, question):
    """segctx: ordered list of {"n": int, "seg": dict, "judgment": str} for the
    segments the annotator can currently see (referenced by their number)."""
    in_label, out_label = campaign.io_labels()
    L = []
    L.append("GUIDELINES")
    if campaign.is_bilingual and campaign.source_language and campaign.target_language:
        L.append(f"Task: evaluate {campaign.source_language} → {campaign.target_language} "
                f"{out_label.lower()} outputs ({task_label(campaign)}).")
    else:
        L.append(f"Task: {task_label(campaign)}, evaluate the {out_label.lower()} produced "
                f"for each {in_label.lower()}.")
    if campaign.instructions:
        L.append("Instructions: " + campaign.instructions)
    mode = campaign.mode
    if mode in ("likert", "span_only") and criteria:
        L.append(("Criteria:" if mode == "likert" else "Error types:"))
        for c in criteria:
            d = c.get("guide") or c.get("desc") or models.criterion_guide(c["name"]) or ""
            L.append(f"- {c['name']}" + (f": {d}" if d else ""))
    if mode == "likert":
        sc = campaign.scale_config
        if sc.get("labels"):
            L.append("Scale labels: " + json.dumps(sc["labels"], ensure_ascii=False))
        L.append(f"Score range: {campaign.score_bounds[0]}–{campaign.score_bounds[1]}")
    if mode == "pairwise":
        L.append("Preference options: " + ", ".join(p["label"] for p in campaign.preferences))
    if campaign.span_instructions:
        L.append("Span guidance: " + campaign.span_instructions)
    L.append("")
    L.append("INTERFACE")
    L.append(_interface_help(campaign))
    L.append("")
    if segctx:
        L.append("SEGMENTS THE ANNOTATOR CAN SEE (refer to them by their number, "
                "e.g. \"segment %d\"):" % segctx[0]["n"])
        for item in segctx:
            seg = item["seg"]
            L.append(f"--- Segment {item['n']} ---")
            L.append(f"{in_label}: " + (seg.get("source") or ""))
            if mode == "pairwise":
                a, b, _sa, _sb = campaign.pairwise_candidates(seg)
                L.append(f"{out_label} A: " + (a or ""))
                L.append(f"{out_label} B: " + (b or ""))
            else:
                L.append(f"{out_label}: " + (seg.get("target") or ""))
            if seg.get("reference"):
                L.append("Reference: " + seg.get("reference"))
            if item.get("judgment"):
                L.append("Annotator's current judgment (their own, do not override): " + item["judgment"])
        L.append("")
    L.append("QUESTION FROM THE ANNOTATOR")
    L.append(question)
    return "\n".join(L)

