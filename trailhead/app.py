"""
SARPack — trailhead/app.py
TRAILHEAD: Offline-first mobile PWA for field operators.
Serves the PWA shell and backend API endpoints.
Field operators install this on their phones by visiting the URL
and tapping "Add to Home Screen" — no app store required.

Run directly:
    python -m trailhead.app

Or launched automatically by sarpack.py via system tray.
"""

import logging
from flask import Flask, jsonify, send_from_directory
from core import initialize
from core.config import config

from trailhead.routes.operator import operator_bp
from trailhead.routes.gps      import gps_bp
from trailhead.routes.patient  import patient_bp
from warden.routes.users       import users_bp

log = logging.getLogger("trailhead")


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY

    initialize()

    # API routes
    app.register_blueprint(operator_bp, url_prefix="/api/operator")
    app.register_blueprint(gps_bp,      url_prefix="/api/gps")
    app.register_blueprint(patient_bp,  url_prefix="/api/patient")
    app.register_blueprint(users_bp,    url_prefix="/api/users")

    # Serve PWA static files
    @app.route("/")
    def index():
        return send_from_directory("templates", "index.html")

    @app.route("/manifest.json")
    def manifest():
        return send_from_directory("static", "manifest.json")

    @app.route("/sw.js")
    def service_worker():
        # Service worker must be served from root scope
        response = send_from_directory("static/js", "sw.js")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.route("/health")
    def health():
        return jsonify({
            "app":    "TRAILHEAD",
            "status": "ok",
            "mode":   config.MODE,
        })

    @app.errorhandler(404)
    def not_found(e):
        # PWA single-page app — all unknown routes serve index.html
        # so the JS router handles navigation
        return send_from_directory("templates", "index.html")

    @app.errorhandler(500)
    def internal_error(e):
        log.exception("Internal server error: %s", e)
        return jsonify({"error": "Internal server error"}), 500

    log.info("TRAILHEAD initialized on port %d", config.PORT_TRAILHEAD)
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(
        host="0.0.0.0",
        port=config.PORT_TRAILHEAD,
        debug=False,
        ssl_context="adhoc",  # HTTPS required for GPS API on mobile browsers
    )
