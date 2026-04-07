"""
SARPack — migrations/env.py
Alembic runtime environment. Connects migrations to SARPack's config
and database setup so `alembic upgrade head` works from the command line.

This file is executed by Alembic for every migration command.
Do not rename or move it.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make sure SARPack root is on the path so `from core.config import config` works
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import config as sarpack_config

# Alembic Config object — gives access to values in alembic.ini
alembic_config = context.config

# Set up Python logging from alembic.ini
if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

# -----------------------------------------------------------------------
# Database URL resolution
# -----------------------------------------------------------------------
# SARPack is a hybrid system. Migrations run against whichever backend
# is configured in .env:
#   MODE=local   → SQLite
#   MODE=hybrid  → PostgreSQL (schema should match SQLite)
#   MODE=cloud   → PostgreSQL
#
# For local/hybrid Toughbook deployments, we always migrate SQLite.
# For cloud-only deployments, we migrate PostgreSQL.

def get_migration_url() -> str:
    if sarpack_config.MODE == "cloud":
        if not sarpack_config.DATABASE_URL:
            raise RuntimeError(
                "SARPACK_DATABASE_URL must be set when MODE=cloud"
            )
        return sarpack_config.DATABASE_URL
    else:
        # local and hybrid both maintain SQLite as the primary DB
        return f"sqlite:///{sarpack_config.SQLITE_PATH}"


# Inject the URL into Alembic's config at runtime
alembic_config.set_main_option("sqlalchemy.url", get_migration_url())

# We do not use SQLAlchemy models (we use raw SQL in db.py),
# so target_metadata is None. Autogenerate will produce empty migrations
# unless you add SQLAlchemy model definitions — that's fine, we use
# --autogenerate only as a diff tool when we know what changed.
target_metadata = None


# -----------------------------------------------------------------------
# Migration runners
# -----------------------------------------------------------------------

def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode — generates SQL without a live DB.
    Useful for reviewing what a migration will do before running it.
    Called when: alembic upgrade head --sql
    """
    url = alembic_config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode — connects to the live database.
    Called when: alembic upgrade head
    """
    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # no connection pooling for migrations
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite-specific: enable batch mode for ALTER TABLE support
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
