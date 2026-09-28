"""
Resume-JD Matching Portal — Flask Application Entry Point.

Run with:
    conda activate cci
    python main.py

Then open: http://localhost:5000
"""
from flask import Flask

from config import SECRET_KEY
from routes.public import public
from routes.candidate import candidate
from routes.admin import admin
from db.seed import seed
from observability.request_hooks import register_request_hooks
from observability.logging_config import get_logger
from salary import format_salary
from timefmt import time_ago, iso_utc, utc_label


def create_app():
    """Application factory — creates and configures the Flask app."""
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    # Templates can write {{ jd|salary }} → "₹12L – ₹18L / yr" (or None).
    app.jinja_env.filters["salary"] = format_salary
    # Timestamps: {{ x|ago }} → "3 days ago", {{ x|iso_utc }} → "...Z" (see timefmt.py)
    app.jinja_env.filters["ago"] = time_ago
    app.jinja_env.filters["iso_utc"] = iso_utc
    app.jinja_env.filters["utc_label"] = utc_label

    # Initialise DB + seed model registry / roles, and turn on request tracing.
    seed()
    register_request_hooks(app)
    get_logger("app").info("app.startup", extra={"event_detail": "blueprints registered"})

    # Register blueprints
    app.register_blueprint(public)
    app.register_blueprint(candidate)
    app.register_blueprint(admin)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, port=5000, )
