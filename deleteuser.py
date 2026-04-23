"""
SARPack — clear_db.py
Wipes all data from the database, leaving the schema intact.
Run from the SARPack root directory:
    python clear_db.py
"""
import sys
sys.path.insert(0, ".")
from core.db import local_db

tables = [
    "outbox",
    "sessions",
    "users",
    "certifications",
    "gps_tracks",
    "radio_log",
    "search_segments",
    "deployments",
    "ics_201", "ics_204", "ics_205", "ics_206",
    "ics_209", "ics_211", "ics_214", "ics_215",
    "incidents",
    "personnel",
]

with local_db() as db:
    for table in tables:
        db.execute(f"DELETE FROM {table}")
        print(f"  cleared: {table}")

print("\nDatabase wiped. Run 'python manage_users.py add' to create your user.")