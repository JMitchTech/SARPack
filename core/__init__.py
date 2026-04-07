"""
SARPack core module.
Import order matters — config first, then db (depends on config), then auth and sync.
"""

from core.config import config
from core.db import init_db
from core.sync import start_sync_engine

import logging

log = logging.getLogger("sarpack.core")


def initialize(validate: bool = True):
    """
    Full system initialization. Call once at startup of any SARPack app.

    1. Validates configuration
    2. Initializes the local SQLite schema
    3. Starts the background sync engine (if MODE != local)

    Args:
        validate: Set False in tests to skip config validation.
    """
    if validate:
        config.validate()

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log.info(config.summary())
    init_db()
    start_sync_engine()
    log.info("SARPack core initialized")