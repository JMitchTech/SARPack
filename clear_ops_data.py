"""
SARPack — clear_ops_data.py
Wipes all operational data from the database while preserving user accounts.
Run from the SARPack root directory:
    python clear_ops_data.py
"""
import sys
sys.path.insert(0, ".")
from core.db import local_db

tables = [
    "patient_assessments",
    "gps_tracks",
    "radio_log",
    "deployments",
    "search_segments",
    "certifications",
    "sessions",
    "ics_201", "ics_204", "ics_205", "ics_206",
    "ics_209", "ics_211", "ics_214", "ics_215",
    "incidents",
    "personnel",
]

with local_db() as db:
    for table in tables:
        try:
            db.execute(f"DELETE FROM {table}")
            print(f"  cleared: {table}")
        except Exception as e:
            print(f"  skipped: {table} ({e})")

print("\nOperational data cleared. User accounts preserved.")
print("You may need to log out and back in to refresh your session.")