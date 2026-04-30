"""
SARPack 2.0 — app.py
Single Flask application. Registers all API blueprints and the portal.
Run with: python -m app  or  python run.py
"""

import os
from dotenv import load_dotenv

# Load .env before anything else
load_dotenv()

from flask import Flask, jsonify
from flask_socketio import SocketIO

from core.config import Config
from core.database import init_db

# ── SocketIO instance (shared across all modules) ────────────────────────────
socketio = SocketIO(
    cors_allowed_origins=Config.ALLOWED_ORIGINS,
    async_mode=Config.SOCKETIO_ASYNC_MODE,
    logger=False,
    engineio_logger=False,
)


def create_app() -> Flask:
    Config.validate()

    app = Flask(
        __name__,
        static_folder="portal/static",
        template_folder="portal/templates",
    )
    app.config["SECRET_KEY"]      = Config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_UPLOAD_MB * 1024 * 1024

    # ── Initialize database ───────────────────────────────────────────────────
    with app.app_context():
        init_db()

    # ── Register API blueprints ───────────────────────────────────────────────
    from api.users        import bp as users_bp
    from api.incidents    import bp as incidents_bp
    from api.deployments  import bp as deployments_bp
    from api.personnel    import bp as personnel_bp
    from api.radio        import bp as radio_bp
    from api.gps          import bp as gps_bp
    from api.forms        import bp as forms_bp
    from api.patients     import bp as patients_bp
    from api.relay        import bp as relay_bp

    app.register_blueprint(users_bp,       url_prefix="/api/users")
    app.register_blueprint(incidents_bp,   url_prefix="/api/incidents")
    app.register_blueprint(deployments_bp, url_prefix="/api/deployments")
    app.register_blueprint(personnel_bp,   url_prefix="/api/personnel")
    app.register_blueprint(radio_bp,       url_prefix="/api/radio")
    app.register_blueprint(gps_bp,         url_prefix="/api/gps")
    app.register_blueprint(forms_bp,       url_prefix="/api/forms")
    app.register_blueprint(patients_bp,    url_prefix="/api/patients")
    app.register_blueprint(relay_bp,       url_prefix="/api/relay")

    # ── Register portal routes ────────────────────────────────────────────────
    from portal.routes import bp as portal_bp
    app.register_blueprint(portal_bp)

    # ── Register SocketIO events ──────────────────────────────────────────────
    from portal import sockets  # noqa — registers all socket handlers

    # ── Health check ──────────────────────────────────────────────────────────
    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "version": "2.0"})

    # ── Global error handlers ─────────────────────────────────────────────────
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": "Bad request", "detail": str(e)}), 400

    @app.errorhandler(401)
    def unauthorized(e):
        return jsonify({"error": "Unauthorized"}), 401

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({"error": "Forbidden"}), 403

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify({"error": "Internal server error"}), 500

    return app


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = create_app()
    socketio.init_app(app)
    Config.summary()
    socketio.run(
        app,
        host=Config.HOST,
        port=Config.PORT_PORTAL,
        debug=Config.DEBUG,
        use_reloader=False,
    )