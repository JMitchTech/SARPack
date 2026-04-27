"""
SARPack — logbook/app.py
LOGBOOK: ICS form generation, compliance validation, IC sign-off, and export.

Run directly:
    python -m logbook.app

Or launched automatically by sarpack.py via system tray.
"""

import logging
from flask import Flask, jsonify
from flask import send_from_directory
from core import initialize
from core.config import config

from logbook.routes.forms   import forms_bp
from logbook.routes.history import history_bp
from warden.routes.users    import users_bp

log = logging.getLogger("logbook")


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY

    initialize()

    app.register_blueprint(forms_bp,   url_prefix="/api/forms")
    app.register_blueprint(history_bp, url_prefix="/api/history")
    app.register_blueprint(users_bp,   url_prefix="/api/users")

    @app.route("/")
    def index():
        return send_from_directory("templates", "index.html")

    @app.route("/static/<path:filename>")
    def static_files(filename):
        return send_from_directory("static", filename)

    @app.route("/health")
    def health():
        from logbook.generator import REPORTLAB_AVAILABLE
        return jsonify({
            "app":               "LOGBOOK",
            "status":            "ok",
            "mode":              config.MODE,
            "reportlab":         REPORTLAB_AVAILABLE,
        })

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def internal_error(e):
        log.exception("Internal server error: %s", e)
        return jsonify({"error": "Internal server error"}), 500

    log.info("LOGBOOK initialized on port %d", config.PORT_LOGBOOK)
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(
        host="0.0.0.0",
        port=config.PORT_LOGBOOK,
        debug=False,
    )