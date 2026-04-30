"""
SARPack 2.0 — core/config.py
Central configuration. All settings read from environment variables.
Copy .env.template to .env and fill in values before running.
"""

import os
import sys
from pathlib import Path

# ── Project root ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    # ── Security ──────────────────────────────────────────────────────────────
    SECRET_KEY         = os.environ.get("SARPACK_SECRET_KEY", "")
    JWT_EXPIRY_HOURS   = int(os.environ.get("SARPACK_JWT_EXPIRY_HOURS", 12))
    MFA_ISSUER         = os.environ.get("SARPACK_MFA_ISSUER", "SARPack")

    # ── Database ──────────────────────────────────────────────────────────────
    DB_PATH            = os.environ.get(
        "SARPACK_DB_PATH",
        str(BASE_DIR / "sarpack.db")
    )

    # ── Server ────────────────────────────────────────────────────────────────
    HOST               = os.environ.get("SARPACK_HOST", "0.0.0.0")
    PORT_PORTAL        = int(os.environ.get("PORT_PORTAL", 8000))
    PORT_TRAILHEAD     = int(os.environ.get("PORT_TRAILHEAD", 8001))
    DEBUG              = os.environ.get("SARPACK_DEBUG", "false").lower() == "true"

    # ── CORS / allowed origins ─────────────────────────────────────────────────
    ALLOWED_ORIGINS    = os.environ.get(
        "SARPACK_ALLOWED_ORIGINS",
        "http://localhost:8000,http://localhost:8001"
    ).split(",")

    # ── File uploads (WARDEN training materials) ──────────────────────────────
    UPLOAD_FOLDER      = os.environ.get(
        "SARPACK_UPLOAD_FOLDER",
        str(BASE_DIR / "uploads")
    )
    MAX_UPLOAD_MB      = int(os.environ.get("SARPACK_MAX_UPLOAD_MB", 50))
    ALLOWED_EXTENSIONS = {"pdf", "pptx", "docx", "xlsx", "png", "jpg", "jpeg"}

    # ── SocketIO ──────────────────────────────────────────────────────────────
    SOCKETIO_ASYNC_MODE = os.environ.get("SARPACK_SOCKETIO_ASYNC_MODE", "threading")

    # ── Drone feed ────────────────────────────────────────────────────────────
    DRONE_STREAM_TIMEOUT = int(os.environ.get("SARPACK_DRONE_TIMEOUT", 30))

    @classmethod
    def validate(cls):
        """Called at startup. Hard-exits if critical config is missing."""
        errors = []

        if not cls.SECRET_KEY or len(cls.SECRET_KEY) < 32:
            errors.append(
                "SARPACK_SECRET_KEY is not set or is too short (min 32 chars).\n"
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )

        if not Path(cls.UPLOAD_FOLDER).exists():
            try:
                Path(cls.UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"Cannot create upload folder: {e}")

        if errors:
            print("\n[SARPack] CONFIGURATION ERRORS:")
            for err in errors:
                print(f"  ✗ {err}")
            print()
            sys.exit(1)

        return True

    @classmethod
    def summary(cls):
        """Print config summary at startup."""
        print(f"""
╔══════════════════════════════════════════════╗
║         SARPack 2.0 — Starting Up            ║
╠══════════════════════════════════════════════╣
║  Portal     → http://{cls.HOST}:{cls.PORT_PORTAL:<5}          ║
║  Trailhead  → http://{cls.HOST}:{cls.PORT_TRAILHEAD:<5}          ║
║  Database   → {str(Path(cls.DB_PATH).name):<30} ║
║  Debug      → {str(cls.DEBUG):<30} ║
╚══════════════════════════════════════════════╝
""")