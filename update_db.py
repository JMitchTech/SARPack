import sys, sqlite3
sys.path.insert(0, '.')
from core.config import config

db = sqlite3.connect(config.SQLITE_PATH)
try:
    db.execute("ALTER TABLE gps_tracks ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
    print("Added created_at to gps_tracks")
except:
    print("gps_tracks already has created_at")
try:
    db.execute("ALTER TABLE radio_log ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
    print("Added created_at to radio_log")
except:
    print("radio_log already has created_at")
db.commit()
db.close()
print("Done")