
from __future__ import annotations

from flask import Flask
from flask_cors import CORS

from .routes.web_routes import web_bp


def create_app() -> Flask:
    app = Flask(__name__)
    # Open CORS for /api/* as requested
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Register blueprint
    app.register_blueprint(web_bp)
    return app


app = create_app()
