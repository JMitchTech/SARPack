"""
SARPack 2.0 — run.py
Launch script. Run with: python run.py
"""

from dotenv import load_dotenv
load_dotenv()

from app import create_app, socketio
from core.config import Config

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