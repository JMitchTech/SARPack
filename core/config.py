"""
SARPack — core/config.py
Central configuration. Reads from environment variables / .env file.
All apps import `config` from here — never hardcode paths or secrets.

MODE controls which database backend is active:
  local  — SQLite only. No cloud sync. Toughbook running standalone.
  hybrid — SQLite primary, PostgreSQL synced in background. Normal ops.
  cloud  — PostgreSQL direct. For cloud-hosted admin access.
"""

import os
import logging
from pathlib import Path

# Load .env file if present (dev convenience — production sets env vars directly)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv optional — prod sets env vars at the OS level


class _Config:
    # ------------------------------------------------------------------
    # Operating mode
    # ------------------------------------------------------------------
    MODE: str = os.getenv("SARPACK_MODE", "local").lower()
    # local | hybrid | cloud

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    _base_dir = Path(__file__).parent.parent
    SQLITE_PATH: str = os.getenv(
        "SARPACK_SQLITE_PATH",
        str(_base_dir / "database" / "sarpack.db"),
    )
    DATABASE_URL: str = os.getenv("SARPACK_DATABASE_URL", "")
    # Format: postgresql://user:password@host:5432/sarpack

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    SECRET_KEY: str = os.getenv("SARPACK_SECRET_KEY", "")
    SESSION_EXPIRY_HOURS: int = int(os.getenv("SARPACK_SESSION_HOURS", "12"))

    # ------------------------------------------------------------------
    # Sync engine
    # ------------------------------------------------------------------
    SYNC_INTERVAL_SECONDS: int = int(os.getenv("SARPACK_SYNC_INTERVAL", "30"))
    SYNC_BATCH_SIZE: int = int(os.getenv("SARPACK_SYNC_BATCH", "100"))
    SYNC_MAX_RETRIES: int = int(os.getenv("SARPACK_SYNC_RETRIES", "5"))

    # ------------------------------------------------------------------
    # App ports (mirrors ADS port layout convention)
    # ------------------------------------------------------------------
    PORT_BASECAMP: int = int(os.getenv("PORT_BASECAMP", "6000"))
    PORT_TRAILHEAD: int = int(os.getenv("PORT_TRAILHEAD", "6001"))
    PORT_RELAY: int = int(os.getenv("PORT_RELAY", "6002"))
    PORT_LOGBOOK: int = int(os.getenv("PORT_LOGBOOK", "6003"))
    PORT_WARDEN: int = int(os.getenv("PORT_WARDEN", "6004"))

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    LOG_LEVEL: str = os.getenv("SARPACK_LOG_LEVEL", "INFO").upper()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(self):
        """
        Call on startup. Raises ValueError for any misconfiguration
        that would cause a runtime failure later.
        """
        errors = []

        if self.MODE not in ("local", "hybrid", "cloud"):
            errors.append(f"SARPACK_MODE must be local|hybrid|cloud, got '{self.MODE}'")

        if self.MODE in ("hybrid", "cloud") and not self.DATABASE_URL:
            errors.append(
                "SARPACK_DATABASE_URL is required when MODE is hybrid or cloud. "
                "Set it in .env or as an environment variable."
            )

        if not self.SECRET_KEY:
            errors.append(
                "SARPACK_SECRET_KEY is not set. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        elif len(self.SECRET_KEY) < 32:
            errors.append("SARPACK_SECRET_KEY must be at least 32 characters.")

        # Ensure SQLite directory exists
        from pathlib import Path
        sqlite_dir = Path(self.SQLITE_PATH).parent
        if not sqlite_dir.exists():
            try:
                sqlite_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                errors.append(f"Cannot create SQLite directory {sqlite_dir}: {e}")

        if errors:
            raise ValueError(
                "SARPack configuration errors:\n" +
                "\n".join(f"  - {e}" for e in errors)
            )

    def summary(self) -> str:
        """Return a human-readable config summary for startup logging."""
        return (
            f"SARPack config | MODE={self.MODE} | "
            f"SQLite={self.SQLITE_PATH} | "
            f"CloudDB={'configured' if self.DATABASE_URL else 'not set'} | "
            f"Sync every {self.SYNC_INTERVAL_SECONDS}s"
        )


config = _Config()
