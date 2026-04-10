"""
SARPack — basecamp/app.py
BASECAMP: Incident command dashboard.
Real-time field operations, incident management, deployment tracking,
GPS positioning, search segment assignment, and radio log.

Run directly:
    python -m basecamp.app

Or launched automatically by sarpack.py via system tray.
"""

import logging
from flask import Flask
from flask_socketio import SocketIO
from core import initialize
from core.config import config

from basecamp.routes.incidents   import incidents_bp
from basecamp.routes.deployments import deployments_bp
from basecamp.routes.map         import map_bp
from basecamp.routes.radio       import radio_bp
from basecamp.routes.dashboard   import dashboard_bp
from warden.routes.users         import users_bp

log = logging.getLogger("basecamp")

# SocketIO instance — imported by events.py and routes that emit
socketio = SocketIO()


def create_app() -> Flask:
    """
    Flask + SocketIO application factory.
    Creates and configures BASECAMP, registers all blueprints,
    attaches SocketIO, and starts background services.
    """
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY

    # Initialize core — DB, sync engine, config validation
    initialize()

    # Register REST API blueprints
    app.register_blueprint(incidents_bp,   url_prefix="/api/incidents")
    app.register_blueprint(deployments_bp, url_prefix="/api/deployments")
    app.register_blueprint(map_bp,         url_prefix="/api/map")
    app.register_blueprint(radio_bp,       url_prefix="/api/radio")
    app.register_blueprint(dashboard_bp,   url_prefix="/api/dashboard")
    app.register_blueprint(users_bp,       url_prefix="/api/users")

    # Initialize SocketIO — async_mode=threading works with Flask dev server
    # and standard WSGI servers (gunicorn with --worker-class eventlet for prod)
    socketio.init_app(
        app,
        async_mode="threading",
        cors_allowed_origins="*",   # tighten in production
        logger=False,
        engineio_logger=False,
    )

    # Register SocketIO event handlers
    from basecamp import events  # noqa — registers handlers as side effect
    events.register(socketio)

    # Health check
    from flask import jsonify

    @app.route("/health")
    def health():
        from core.sync import sync_status
        return jsonify({
            "app":    "BASECAMP",
            "status": "ok",
            "mode":   config.MODE,
            "sync":   sync_status(),
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

    # Start background services after app context is ready
    with app.app_context():
        from basecamp.services import start_background_services
        start_background_services(socketio)

    log.info("BASECAMP initialized on port %d", config.PORT_BASECAMP)
    return app


if __name__ == "__main__":
    app = create_app()
    socketio.run(
        app,
        host="0.0.0.0",
        port=config.PORT_BASECAMP,
        debug=False,
        use_reloader=False,   # reloader conflicts with background threads
    )
