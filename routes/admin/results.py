import os
import json
from datetime import datetime

from flask import Blueprint, current_app, render_template, jsonify, Response

import results
import exporter
from models import Campaign, Rating, CampaignGroup
from utils.constants import get_criteria_for
from services.auth_service import (require_admin, owned_campaign_or_404,
    owned_group_or_404, visible_campaigns_query, admin_owns)

admin_results_bp = Blueprint(
    "admin_results",
    __name__,
    url_prefix="/admin",
)

def _campaign_results(c):
    """Return (results_dict, is_frozen). Closed campaigns use the frozen snapshot
    if present; otherwise results are computed live."""
    if c.is_closed and c.results_snapshot_json:
        try:
            return json.loads(c.results_snapshot_json), True
        except json.JSONDecodeError:
            pass
    crit = get_criteria_for(c)
    all_ratings = Rating.query.filter_by(campaign_id=c.id).all()
    return results.compute_results(c, all_ratings, crit), c.is_closed


def _results_signature(c):
    """Cheap signature of the result-affecting state: rating count + latest edit + config."""
    ratings = list(c.ratings)
    n = len(ratings)
    last = max((r.updated_at for r in ratings), default=None)
    return f"{n}:{last.isoformat() if last else '-'}:{c.config_fingerprint()}:{'closed' if c.is_closed else 'open'}"

@admin_results_bp.route("/campaign/<campaign_id>/results.json")
@require_admin
def admin_campaign_results_json(campaign_id):
    c = owned_campaign_or_404(campaign_id)
    res, _ = _campaign_results(c)
    payload = json.dumps(res, ensure_ascii=False, indent=2)
    fname = exporter._safe_filename(c.name) + "_results.json"
    return Response(payload, mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@admin_results_bp.route("/campaign/<campaign_id>/results.html")
@require_admin
def admin_campaign_results_report(campaign_id):
    c = owned_campaign_or_404(campaign_id)
    res, frozen = _campaign_results(c)
    charts = results.build_charts(res)
    # Inline the export JS so the downloaded report works offline.
    try:
        with open(
            os.path.join(current_app.static_folder, "results.js"),
            encoding="utf-8",
        ) as fh:
            results_js = fh.read()
    except OSError:
        results_js = ""
    html = render_template("results_report.html",
                        campaign=c, results=res, charts=charts, frozen=frozen,
                        results_js=results_js,
                        generated=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    fname = exporter._safe_filename(c.name) + "_results.html"
    return Response(html, mimetype="text/html",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})

@admin_results_bp.route("/campaign/<campaign_id>/results")
@require_admin
def admin_campaign_results(campaign_id):
    c = owned_campaign_or_404(campaign_id)
    res, frozen = _campaign_results(c)
    charts = results.build_charts(res)
    return render_template("admin_campaign_results.html",
                        campaign=c, results=res, charts=charts, frozen=frozen,
                        results_version=_results_signature(c))

@admin_results_bp.route("/campaign/<campaign_id>/results-version")
@require_admin
def admin_campaign_results_version(campaign_id):
    c = owned_campaign_or_404(campaign_id)
    return jsonify({"ok": True, "version": _results_signature(c), "frozen": c.is_closed})


@admin_results_bp.route("/groups/<group_id>/results")
@require_admin
def admin_group_results(group_id):
    group = owned_group_or_404(group_id)
    campaigns = group.ordered_campaigns()
    items = []
    for c in campaigns:
        res, _ = _campaign_results(c)
        items.append({"id": c.id, "name": c.name, "mode": c.mode, "results": res})
    combined = results.combine_group_results(items)
    # Map campaign id -> campaign object for links in the template.
    campaign_by_id = {c.id: c for c in campaigns}
    return render_template("admin_group_results.html",
                        group=group, campaigns=campaigns, combined=combined,
                        campaign_by_id=campaign_by_id)


@admin_results_bp.route("/groups/<group_id>/download_csv")
@require_admin
def admin_group_download_csv(group_id):
    """Combined master CSV across every campaign in the group (completed ratings only)."""
    group = owned_group_or_404(group_id)
    items = []
    for c in group.ordered_campaigns():
        crit = get_criteria_for(c)
        all_ratings = Rating.query.filter_by(campaign_id=c.id).all()
        completed = [r for r in all_ratings if c.rating_is_complete(r, crit)]
        items.append((c, completed, crit))
    data = exporter.build_group_master_csv(items)
    filename = exporter._safe_filename(group.name) + "_group_master.csv"
    return Response(
        data, mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@admin_results_bp.route("/campaign/<campaign_id>/segment/<segment_id>")
@require_admin
def admin_campaign_segment(campaign_id, segment_id):
    """Inspect a single segment: its source/target and every annotator's rating for it
    (scores, marked error spans with the exact substrings, comments, and any post-edit).
    Linked from the 'Linguistic diagnosis' section so admins can jump straight to the
    segments annotators disagree on most."""
    c = owned_campaign_or_404(campaign_id)
    seg = c.segment_by_id(segment_id)
    if not seg:
        from flask import abort
        abort(404)
    crit = get_criteria_for(c)
    crit_names = {cr["id"]: cr["name"] for cr in crit}
    target = seg.get("target", "") or ""
    ratings = (Rating.query.filter_by(campaign_id=c.id, segment_id=segment_id)
               .all())

    rows = []
    for r in ratings:
        if not c.rating_is_complete(r, crit):
            # Still show partial ratings, but mark them.
            pass
        ann = r.annotator
        scores = r.scores_dict()
        spans = r.spans_dict()
        span_view = []
        for cr in crit:
            cid = cr["id"]
            marked = []
            for sp in spans.get(cid, []):
                if len(sp) >= 2:
                    a, b = sp[0], sp[1]
                    txt = target[a:b] if 0 <= a <= b <= len(target) else ""
                    marked.append({"start": a, "end": b, "text": txt,
                                   "chars": max(0, b - a)})
            if marked or cid in scores:
                span_view.append({
                    "criterion": crit_names.get(cid, cid),
                    "score": scores.get(cid),
                    "spans": marked,
                })
        ranking_view = None
        if c.mode == "preference_selection":
            cands = c.selection_candidates(seg)
            rk = r.ranking_dict()
            ranking_view = [{
                "label": (cd.get("system") or f"Candidate {cd['index']+1}"),
                "text": cd.get("text", ""),
                "rank": rk.get(cd["key"]),
            } for cd in cands]
            ranking_view.sort(key=lambda x: (x["rank"] is None, x["rank"] if x["rank"] is not None else 0))
        rows.append({
            "annotator": (ann.name if ann else r.annotator_id),
            "annotator_id": r.annotator_id,
            "email": (ann.email if ann else ""),
            "preference": (r.preference or ""),
            "ranking": ranking_view,
            "criteria": span_view,
            "comment": (r.comments or "").strip(),
            "edited_text": (r.edited_text or "").strip(),
            "complete": c.rating_is_complete(r, crit),
            "updated": r.updated_at.strftime("%Y-%m-%d %H:%M UTC") if r.updated_at else "",
        })

    pref_labels = {p["id"]: p["label"] for p in c.preferences}
    readability = results._readability(seg.get("source", "") or target)
    return render_template("admin_segment_detail.html",
                        campaign=c, seg=seg, rows=rows,
                        pref_labels=pref_labels, readability=readability,
                        n_ratings=len(rows))


@admin_results_bp.route("/campaign/<campaign_id>/results-fragment")
@require_admin
def admin_campaign_results_fragment(campaign_id):
    c = owned_campaign_or_404(campaign_id)
    res, frozen = _campaign_results(c)
    charts = results.build_charts(res)
    html = render_template("_results_body.html",
                        campaign=c, results=res, charts=charts, frozen=frozen)
    return jsonify({"ok": True, "version": _results_signature(c), "frozen": frozen,
                    "html": html, "results": res})