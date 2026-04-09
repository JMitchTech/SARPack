import sys, sqlite3
sys.path.insert(0, '.')
from core.config import config

db = sqlite3.connect(config.SQLITE_PATH)
db.execute('''CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    personnel_id TEXT NOT NULL,
    shift_name TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    is_oncall INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL)''')
db.commit()
db.close()
print('schedules table added successfully')