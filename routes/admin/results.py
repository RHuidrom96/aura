import os
import json
from datetime import datetime

from flask import Blueprint, current_app, render_template, jsonify, Response

import results
import exporter
from models import Campaign, Rating
from utils.constants import get_criteria_for
from services.auth_service import require_admin

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
    c = Campaign.query.get_or_404(campaign_id)
    res, _ = _campaign_results(c)
    payload = json.dumps(res, ensure_ascii=False, indent=2)
    fname = exporter._safe_filename(c.name) + "_results.json"
    return Response(payload, mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@admin_results_bp.route("/campaign/<campaign_id>/results.html")
@require_admin
def admin_campaign_results_report(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
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
    c = Campaign.query.get_or_404(campaign_id)
    res, frozen = _campaign_results(c)
    charts = results.build_charts(res)
    return render_template("admin_campaign_results.html",
                        campaign=c, results=res, charts=charts, frozen=frozen,
                        results_version=_results_signature(c))

@admin_results_bp.route("/campaign/<campaign_id>/results-version")
@require_admin
def admin_campaign_results_version(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    return jsonify({"ok": True, "version": _results_signature(c), "frozen": c.is_closed})


@admin_results_bp.route("/campaign/<campaign_id>/results-fragment")
@require_admin
def admin_campaign_results_fragment(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    res, frozen = _campaign_results(c)
    charts = results.build_charts(res)
    html = render_template("_results_body.html",
                        campaign=c, results=res, charts=charts, frozen=frozen)
    return jsonify({"ok": True, "version": _results_signature(c), "frozen": frozen,
                    "html": html, "results": res})