
from __future__ import annotations

from flask import Flask, render_template
from flask_cors import CORS

from .routes.web_routes import web_bp
from pathlib import Path


def create_app() -> Flask:
    base_dir = Path(__file__).resolve().parent.parent
    templates = base_dir / 'frontend' / 'templates'
    static = base_dir / 'frontend' / 'static'

    app = Flask(__name__, template_folder=str(templates), static_folder=str(static))
    # Open CORS for /api/* as requested
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Register blueprint
    app.register_blueprint(web_bp)

    # Serve chat UI
    @app.get('/')
    def index():
        return render_template('chat.html')
    return app


app = create_app()
