from .public import public_bp

from .admin.auth import admin_auth_bp
from .admin.campaigns import admin_campaign_bp
from .admin.results import admin_results_bp
from .admin.qualification import admin_qualification_bp

from .annotator.auth import annotator_auth_bp
from .annotator.campaign import annotator_campaign_bp
from .annotator.dashboard import annotator_dashboard_bp
from .annotator.rating import annotator_rating_bp
from .annotator.qualification import annotator_qualification_bp


def register_blueprints(app):
    app.register_blueprint(public_bp)

    app.register_blueprint(admin_auth_bp)
    app.register_blueprint(admin_campaign_bp)
    app.register_blueprint(admin_results_bp)
    app.register_blueprint(admin_qualification_bp)

    app.register_blueprint(annotator_auth_bp)
    app.register_blueprint(annotator_campaign_bp)
    app.register_blueprint(annotator_dashboard_bp)
    app.register_blueprint(annotator_rating_bp)
    app.register_blueprint(annotator_qualification_bp)