"""
SARPack migration — 0006_add_lkp_to_incidents.py

Adds lkp_lat, lkp_lng, and lkp_notes columns to the incidents table
to support the Last Known Position tool in BASECAMP.

Run from the SARPack root:
    python 0006_add_lkp_to_incidents.py

Safe to run multiple times.
"""

import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, ".")
from core.config import config

DB_PATH = config.SQLITE_PATH


def column_exists(conn, table, column):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def run():
    if not Path(DB_PATH).exists():
        print(f"  ✖  Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    added = []

    for col, definition in [
        ("lkp_lat",   "REAL"),
        ("lkp_lng",   "REAL"),
        ("lkp_notes", "TEXT"),
    ]:
        if not column_exists(conn, "incidents", col):
            conn.execute(f"ALTER TABLE incidents ADD COLUMN {col} {definition}")
            added.append(col)

    conn.commit()
    conn.close()

    if added:
        print(f"  ✔  Added columns to incidents: {', '.join(added)}")
    else:
        print("  ✔  LKP columns already exist — nothing to do.")


if __name__ == "__main__":
    run()