"""
SARPack migration — 0005_add_must_change_password.py

Adds `must_change_password` flag to the users table.
When set to 1, the login API returns a `must_change_password: true` signal
and the frontend forces a password-change screen before proceeding.

Run from the SARPack root:
    python 0005_add_must_change_password.py

Safe to run multiple times (uses ALTER TABLE IF NOT EXISTS pattern via
column existence check).
"""

import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, ".")
from core.config import config

DB_PATH = config.SQLITE_PATH


def column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def run():
    if not Path(DB_PATH).exists():
        print(f"  ✖  Database not found at {DB_PATH}")
        print("     Start the SARPack app once to initialise the schema, then run this migration.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    if column_exists(conn, "users", "must_change_password"):
        print("  ✔  Column 'must_change_password' already exists — nothing to do.")
        conn.close()
        return

    print(f"  Migrating {DB_PATH} ...")
    conn.execute(
        "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
    )
    conn.commit()
    conn.close()
    print("  ✔  Added 'must_change_password' column to users table.")
    print("     All existing users default to 0 (no forced change required).")
    print()
    print("  To require a user to change their password on next login:")
    print("     python manage_users.py force-password-change <username>")


if __name__ == "__main__":
    run()