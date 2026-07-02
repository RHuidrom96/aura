"""Build downloadable / emailable exports for a campaign: a per-rating master CSV and a
complete results ZIP (CSV + chart PNGs + results JSON + HTML report). Replaces the old
No external sync; everything is produced in-memory and emailed to the campaign owner.
"""

import io
import csv
import json
import base64
import zipfile


def _safe_filename(name):
    keep = []
    for ch in name or "":
        keep.append(ch if (ch.isalnum() or ch in "-_ ") else "_")
    return "".join(keep).strip() or "campaign"


def _csv_header(campaign, criteria):
    mode = campaign.mode
    header = [
        "response_id", "timestamp_utc",
        "annotator_id", "annotator_name", "annotator_email", "annotator_native_lang",
        "annotator_source_fluency", "annotator_target_fluency",
        "campaign_id", "campaign_name", "eval_mode",
        "source_language", "target_language", "source_script", "target_script",
        "segment_id", "system", "domain", "source",
    ]
    if mode == "pairwise":
        header += ["candidate_a", "candidate_b", "system_a", "system_b", "reference",
                   "preference_id", "preference_label"]
    else:
        header += ["target", "reference"]
        if mode == "likert":
            for c in criteria:
                header.append(c["id"])
        for c in criteria:
            header.append(c["id"] + "_spans")
        if mode == "span_only":
            header.append("reviewed")
        if mode == "post_edit":
            header.append("edited_text")
    header += ["comments", "time_spent_seconds", "updated_at_utc"]
    return header


def master_rows(campaign, ratings, criteria):
    """Yield each rating as a list aligned to _csv_header(campaign, criteria)."""
    mode = campaign.mode
    pref_labels = {p["id"]: p["label"] for p in campaign.preferences}
    for r in ratings:
        seg = campaign.segment_by_id(r.segment_id) or {}
        ann = r.annotator
        scores = r.scores_dict()
        spans = r.spans_dict()
        ts = r.updated_at.isoformat(timespec="seconds") + "Z"
        row = [
            r.id, ts,
            r.annotator_id,
            ann.name if ann else "",
            ann.email if ann else "",
            (ann.native_language or "") if ann else "",
            (ann.source_fluency or "") if ann else "",
            (ann.target_fluency or "") if ann else "",
            campaign.id, campaign.name, mode,
            campaign.source_language, campaign.target_language,
            (campaign.source_script or campaign.script or ""),
            (campaign.target_script or campaign.script or ""),
            r.segment_id, seg.get("system", ""), seg.get("domain", ""), seg.get("source", ""),
        ]
        if mode == "pairwise":
            a, b, sa, sb = campaign.pairwise_candidates(seg)
            row += [a, b, sa, sb, seg.get("reference", ""),
                    r.preference or "", pref_labels.get(r.preference or "", "")]
        else:
            row += [seg.get("target", ""), seg.get("reference", "")]
            if mode == "likert":
                for c in criteria:
                    row.append(scores.get(c["id"], ""))
            for c in criteria:
                row.append(json.dumps(spans.get(c["id"], [])))
            if mode == "span_only":
                row.append(1 if r.reviewed else 0)
            if mode == "post_edit":
                row.append(r.edited_text or "")
        row += [r.comments or "", r.time_spent_seconds or 0, ts]
        yield row


def build_master_csv(campaign, ratings, criteria):
    """Per-campaign master CSV (one row per completed rating)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_csv_header(campaign, criteria))
    for row in master_rows(campaign, ratings, criteria):
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def build_group_master_csv(items):
    """Combined master CSV for a group of campaigns.

    ``items`` is a list of ``(campaign, ratings, criteria)`` tuples. Because different
    campaigns (and evaluation modes) have different columns, the combined file uses the
    union of every campaign's columns in a stable order; cells absent for a given row are
    left blank. Every row still carries ``campaign_id`` / ``campaign_name`` / ``eval_mode``,
    so the source campaign of each row is unambiguous.
    """
    ordered_cols = []
    seen = set()
    per_campaign_dicts = []
    for campaign, ratings, criteria in items:
        header = _csv_header(campaign, criteria)
        for col in header:
            if col not in seen:
                seen.add(col)
                ordered_cols.append(col)
        rows = [dict(zip(header, row)) for row in master_rows(campaign, ratings, criteria)]
        per_campaign_dicts.append(rows)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(ordered_cols)
    for rows in per_campaign_dicts:
        for rd in rows:
            writer.writerow([rd.get(col, "") for col in ordered_cols])
    return buf.getvalue().encode("utf-8")


def _table_to_csv(rows, columns):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        w.writerow([r.get(c, "") for c in columns])
    return buf.getvalue()


def _summary_tables_csv(results):
    """Flatten the most useful results tables into a single readable CSV text."""
    out = io.StringIO()
    mode = results.get("mode")
    summ = results.get("summary", {})
    out.write("# Summary\n")
    for k, v in summ.items():
        out.write(f"{k},{v}\n")
    out.write("\n")
    if mode == "likert" and results.get("likert"):
        L = results["likert"]
        if L.get("criteria"):
            out.write("# Per-criterion (mean, n)\n")
            rows = [{"name": r.get("name"), "mean": r.get("mean"), "n": r.get("n_ratings")}
                    for r in L["criteria"]]
            out.write(_table_to_csv(rows, ["name", "mean", "n"]))
            out.write("\n")
    if mode == "pairwise" and results.get("pairwise", {}).get("systems"):
        out.write("# Pairwise win rates\n")
        out.write(_table_to_csv(results["pairwise"]["systems"], ["system", "win_rate", "n"]))
        out.write("\n")
    if mode == "span_only" and results.get("span_only"):
        S = results["span_only"]
        out.write("# Span agreement\n")
        out.write(f"mean_span_f1_char,{S.get('mean_span_f1')}\n")
        out.write(f"mean_span_f1_token,{S.get('mean_span_f1_token')}\n\n")
        if S.get("criteria"):
            out.write("# Error spans (count + density)\n")
            out.write(_table_to_csv(S["criteria"],
                      ["name", "n_spans", "char_count", "mean_span_chars",
                       "mean_span_words", "char_density", "marked_instances"]))
            out.write("\n")
    if mode == "likert" and results.get("likert", {}).get("spans"):
        SP = results["likert"]["spans"]
        if SP.get("criteria"):
            out.write("# Error spans (count + density)\n")
            out.write(_table_to_csv(SP["criteria"],
                      ["name", "n_spans", "char_count", "mean_span_chars",
                       "mean_span_words", "char_density", "marked_instances"]))
            out.write("\n")
    if mode == "post_edit" and results.get("post_edit"):
        P = results["post_edit"]
        out.write("# Post-editing\n")
        for k in ("percent_changed", "mean_norm_distance", "mean_pairwise_similarity",
                  "change_agreement_pct"):
            out.write(f"{k},{P.get(k)}\n")
        out.write("\n")
        if P.get("systems"):
            out.write(_table_to_csv(P["systems"], ["system", "n", "mean_norm_distance"]))
            out.write("\n")
    if results.get("difficulty"):
        out.write("# By difficulty\n")
        out.write(_table_to_csv(results["difficulty"]["rows"],
                                ["level", "n_segments", "n_ratings", "metric"]))
        out.write("\n")
    if results.get("disagreement", {}).get("contested_segments"):
        rows = []
        for seg in results["disagreement"]["contested_segments"]:
            rd = seg.get("readability") or {}
            rows.append({
                "segment_id": seg.get("segment_id"),
                "n_raters": seg.get("n_raters"),
                "disagreement": seg.get("disagreement"),
                "reading_ease": rd.get("reading_ease", ""),
                "n_comments": seg.get("n_comments", 0),
                "source": seg.get("source", ""),
            })
        out.write("# Most contested segments (linguistic diagnosis)\n")
        out.write(_table_to_csv(rows, ["segment_id", "n_raters", "disagreement",
                                       "reading_ease", "n_comments", "source"]))
        out.write("\n")
    return out.getvalue()


def build_results_zip(campaign, ratings, criteria, results, charts, report_html=None):
    """Return ZIP bytes containing the CSV, chart PNGs, results JSON, summary CSV, and report."""
    base = _safe_filename(campaign.name)
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{base}/ratings.csv", build_master_csv(campaign, ratings, criteria))
        z.writestr(f"{base}/results_summary.csv", _summary_tables_csv(results))
        z.writestr(f"{base}/results.json",
                   json.dumps(results, ensure_ascii=False, indent=2).encode("utf-8"))
        if report_html:
            z.writestr(f"{base}/results_report.html",
                       report_html.encode("utf-8") if isinstance(report_html, str) else report_html)
        for name, uri in (charts or {}).items():
            if not uri:
                continue
            try:
                b64 = uri.split(",", 1)[1] if "," in uri else uri
                z.writestr(f"{base}/figures/{name}.png", base64.b64decode(b64))
            except Exception:
                pass
    return mem.getvalue()
