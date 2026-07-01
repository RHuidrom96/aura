from flask import Flask

from config import Config
from extensions import init_extensions
from app_setup import register_components
from routes import register_blueprints
from commands.cli import register_cli


def create_app():
    app = Flask(__name__)

    app.config.from_object(Config)

    init_extensions(app)

    register_components(app)
    register_blueprints(app)
    register_cli(app)

    return app