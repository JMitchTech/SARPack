"""
SARPack — warden/app.py
WARDEN: Personnel administration portal.
Manages personnel records, certifications, equipment, scheduling,
user accounts, and deployment history for Keystone Rescue Service.

Run directly:
    python -m warden.app

Or launched automatically by sarpack.py via system tray.
"""

import logging
from flask import Flask, jsonify
from core import initialize
from core.config import config

from warden.routes.personnel import personnel_bp
from warden.routes.certifications import certifications_bp
from warden.routes.equipment import equipment_bp
from warden.routes.users import users_bp
from warden.routes.schedules import schedules_bp

log = logging.getLogger("warden")


def create_app() -> Flask:
    """
    Flask application factory.
    Creates and configures the WARDEN app, registers all blueprints.
    """
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY

    # Initialize core — DB, sync engine, config validation
    initialize()

    # Register route blueprints
    app.register_blueprint(personnel_bp,       url_prefix="/api/personnel")
    app.register_blueprint(certifications_bp,  url_prefix="/api/certifications")
    app.register_blueprint(equipment_bp,       url_prefix="/api/equipment")
    app.register_blueprint(users_bp,           url_prefix="/api/users")
    app.register_blueprint(schedules_bp,       url_prefix="/api/schedules")

    # Health check — sarpack.py watchdog and future monitoring use this
    @app.route("/health")
    def health():
        return jsonify({
            "app": "WARDEN",
            "status": "ok",
            "mode": config.MODE,
        })

    # Global error handlers
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(500)
    def internal_error(e):
        log.exception("Internal server error: %s", e)
        return jsonify({"error": "Internal server error"}), 500

    log.info("WARDEN initialized on port %d", config.PORT_WARDEN)
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(
        host="0.0.0.0",
        port=config.PORT_WARDEN,
        debug=False,
    )
