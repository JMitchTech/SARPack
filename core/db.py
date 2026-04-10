"""
SARPack — core/db.py
Shared database module. Handles SQLite (local) and PostgreSQL (cloud),
schema creation, version-locked writes, and the outbox sync queue.

All apps import this module. Never import app-specific code here.
"""

import os
import uuid
import json
import sqlite3
import logging
import threading
from datetime import datetime, timezone
from contextlib import contextmanager

# PostgreSQL support — only required in hybrid/cloud mode
try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

from core.config import config

log = logging.getLogger("sarpack.db")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_utc() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_local = threading.local()  # thread-local SQLite connections


def get_local_conn() -> sqlite3.Connection:
    """
    Return a thread-local SQLite connection.
    Creates the connection on first access per thread.
    Row factory set to sqlite3.Row so columns are accessible by name.
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(
            config.SQLITE_PATH,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            check_same_thread=False,
        )
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")   # safe concurrent reads
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


@contextmanager
def local_db():
    """
    Context manager for SQLite transactions.
    Commits on clean exit, rolls back on exception.

    Usage:
        with local_db() as db:
            db.execute("INSERT INTO ...")
    """
    conn = get_local_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_cloud_conn():
    """
    Return a new PostgreSQL connection using config.DATABASE_URL.
    Caller is responsible for closing it.
    Raises RuntimeError if psycopg2 is not installed or mode is local-only.
    """
    if not PSYCOPG2_AVAILABLE:
        raise RuntimeError(
            "psycopg2 not installed. Run: pip install psycopg2-binary"
        )
    if config.MODE == "local":
        raise RuntimeError(
            "Cloud DB unavailable in local mode. Set MODE=hybrid or MODE=cloud in .env"
        )
    conn = psycopg2.connect(config.DATABASE_URL)
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn


@contextmanager
def cloud_db():
    """
    Context manager for PostgreSQL transactions.
    Commits on clean exit, rolls back and closes on exception.
    """
    conn = get_cloud_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

SCHEMA_SQL = """

-- -----------------------------------------------------------------------
-- Core operational tables
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS incidents (
    id                      TEXT PRIMARY KEY,
    incident_number         TEXT NOT NULL UNIQUE,
    incident_name           TEXT NOT NULL,
    incident_type           TEXT NOT NULL,          -- 'SAR' | 'disaster_relief' | 'training'
    status                  TEXT NOT NULL DEFAULT 'active', -- 'active' | 'closed' | 'standby'
    lat                     REAL,
    lng                     REAL,
    county                  TEXT,
    state                   TEXT,
    started_at              TEXT NOT NULL,
    closed_at               TEXT,
    incident_commander_id   TEXT REFERENCES personnel(id),
    notes                   TEXT,
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS personnel (
    id                      TEXT PRIMARY KEY,
    first_name              TEXT NOT NULL,
    last_name               TEXT NOT NULL,
    call_sign               TEXT UNIQUE,
    phone                   TEXT,
    email                   TEXT UNIQUE,
    blood_type              TEXT,
    allergies               TEXT,
    medical_notes           TEXT,
    emergency_contact_name  TEXT,
    emergency_contact_phone TEXT,
    is_active               INTEGER NOT NULL DEFAULT 1,  -- SQLite has no BOOLEAN
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deployments (
    id                      TEXT PRIMARY KEY,
    incident_id             TEXT NOT NULL REFERENCES incidents(id),
    personnel_id            TEXT NOT NULL REFERENCES personnel(id),
    role                    TEXT NOT NULL,           -- ICS role at this incident
    division                TEXT,
    team                    TEXT,
    checked_in_at           TEXT,
    checked_out_at          TEXT,
    status                  TEXT NOT NULL DEFAULT 'active', -- 'active' | 'checked_out' | 'unavailable'
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE(incident_id, personnel_id)               -- one deployment per person per incident
);

CREATE TABLE IF NOT EXISTS certifications (
    id                      TEXT PRIMARY KEY,
    personnel_id            TEXT NOT NULL REFERENCES personnel(id),
    cert_type               TEXT NOT NULL,           -- 'WFR' | 'EMT' | 'CPR' | 'FEMA_ICS' etc.
    cert_number             TEXT,
    issuing_body            TEXT,
    issued_date             TEXT,
    expiry_date             TEXT,
    is_verified             INTEGER NOT NULL DEFAULT 0,
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gps_tracks (
    id                      TEXT PRIMARY KEY,
    incident_id             TEXT NOT NULL REFERENCES incidents(id),
    personnel_id            TEXT NOT NULL REFERENCES personnel(id),
    lat                     REAL NOT NULL,
    lng                     REAL NOT NULL,
    elevation               REAL,
    accuracy                REAL,
    recorded_at             TEXT NOT NULL,
    source                  TEXT NOT NULL DEFAULT 'trailhead',
    created_at              TEXT NOT NULL
    -- append-only: no version column, never updated
);

CREATE TABLE IF NOT EXISTS search_segments (
    id                      TEXT PRIMARY KEY,
    incident_id             TEXT NOT NULL REFERENCES incidents(id),
    segment_id              TEXT NOT NULL,           -- human-readable: 'A1', 'B3' etc.
    assigned_team           TEXT,
    status                  TEXT NOT NULL DEFAULT 'unassigned', -- 'unassigned' | 'assigned' | 'cleared' | 'suspended'
    boundary_coords         TEXT,                    -- JSON array of [lat, lng] pairs
    probability_of_detection REAL DEFAULT 0.0,
    assigned_at             TEXT,
    cleared_at              TEXT,
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE(incident_id, segment_id)
);

CREATE TABLE IF NOT EXISTS radio_log (
    id                      TEXT PRIMARY KEY,
    incident_id             TEXT NOT NULL REFERENCES incidents(id),
    personnel_id            TEXT REFERENCES personnel(id),
    channel                 TEXT,
    message                 TEXT NOT NULL,
    logged_at               TEXT NOT NULL,
    is_missed_checkin       INTEGER NOT NULL DEFAULT 0,
    source                  TEXT NOT NULL DEFAULT 'manual',
    created_at              TEXT NOT NULL
    -- append-only: no version column, never updated
);

-- -----------------------------------------------------------------------
-- ICS form tables
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ics_201 (
    id                      TEXT PRIMARY KEY,
    incident_id             TEXT NOT NULL REFERENCES incidents(id),
    situation_summary       TEXT,
    initial_objectives      TEXT,
    current_actions         TEXT,
    resource_summary        TEXT,                    -- JSON
    prepared_by             TEXT REFERENCES personnel(id),
    prepared_at             TEXT,
    signed_by               TEXT REFERENCES personnel(id),
    signed_at               TEXT,
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ics_204 (
    id                      TEXT PRIMARY KEY,
    incident_id             TEXT NOT NULL REFERENCES incidents(id),
    operational_period      TEXT,
    branch                  TEXT,
    division                TEXT,
    group_name              TEXT,
    supervisor_id           TEXT REFERENCES personnel(id),
    assigned_resources      TEXT,                    -- JSON
    special_instructions    TEXT,
    signed_by               TEXT REFERENCES personnel(id),
    signed_at               TEXT,
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ics_205 (
    id                      TEXT PRIMARY KEY,
    incident_id             TEXT NOT NULL REFERENCES incidents(id),
    operational_period      TEXT,
    channel_assignments     TEXT,                    -- JSON
    special_instructions    TEXT,
    prepared_by             TEXT REFERENCES personnel(id),
    prepared_at             TEXT,
    signed_by               TEXT REFERENCES personnel(id),
    signed_at               TEXT,
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ics_206 (
    id                      TEXT PRIMARY KEY,
    incident_id             TEXT NOT NULL REFERENCES incidents(id),
    operational_period      TEXT,
    medical_aid_stations    TEXT,                    -- JSON
    medical_personnel       TEXT,                    -- JSON (auto-compiled from certifications)
    hospitals               TEXT,                    -- JSON
    medical_officer_id      TEXT REFERENCES personnel(id),
    signed_by               TEXT REFERENCES personnel(id),
    signed_at               TEXT,
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ics_209 (
    id                      TEXT PRIMARY KEY,
    incident_id             TEXT NOT NULL REFERENCES incidents(id),
    operational_period      TEXT,
    incident_phase          TEXT,
    total_personnel         INTEGER DEFAULT 0,
    current_situation       TEXT,
    primary_mission         TEXT,
    planned_actions         TEXT,
    resource_totals         TEXT,                    -- JSON
    prepared_by             TEXT REFERENCES personnel(id),
    prepared_at             TEXT,
    signed_by               TEXT REFERENCES personnel(id),
    signed_at               TEXT,
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ics_211 (
    id                      TEXT PRIMARY KEY,
    incident_id             TEXT NOT NULL REFERENCES incidents(id),
    personnel_id            TEXT NOT NULL REFERENCES personnel(id),
    assignment              TEXT,
    check_in_time           TEXT,
    check_out_time          TEXT,
    home_agency             TEXT,
    resource_type           TEXT,
    created_at              TEXT NOT NULL
    -- append-only: sourced from deployments, no version needed
);

CREATE TABLE IF NOT EXISTS ics_214 (
    id                      TEXT PRIMARY KEY,
    incident_id             TEXT NOT NULL REFERENCES incidents(id),
    personnel_id            TEXT REFERENCES personnel(id),
    operational_period      TEXT,
    unit_name               TEXT,
    activity_entries        TEXT,                    -- JSON array of timestamped entries
    prepared_by             TEXT REFERENCES personnel(id),
    prepared_at             TEXT,
    signed_by               TEXT REFERENCES personnel(id),
    signed_at               TEXT,
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ics_215 (
    id                      TEXT PRIMARY KEY,
    incident_id             TEXT NOT NULL REFERENCES incidents(id),
    operational_period      TEXT,
    branches                TEXT,                    -- JSON
    divisions               TEXT,                    -- JSON
    tactical_objectives     TEXT,                    -- JSON
    support_requirements    TEXT,                    -- JSON
    prepared_by             TEXT REFERENCES personnel(id),
    prepared_at             TEXT,
    signed_by               TEXT REFERENCES personnel(id),
    signed_at               TEXT,
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

-- -----------------------------------------------------------------------
-- Auth table
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id                      TEXT PRIMARY KEY,
    personnel_id            TEXT REFERENCES personnel(id),
    username                TEXT NOT NULL UNIQUE,
    password_hash           TEXT NOT NULL,
    role                    TEXT NOT NULL,           -- see auth.py ROLES
    is_active               INTEGER NOT NULL DEFAULT 1,
    last_login_at           TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL REFERENCES users(id),
    token                   TEXT NOT NULL UNIQUE,
    expires_at              TEXT NOT NULL,
    created_at              TEXT NOT NULL
);

-- -----------------------------------------------------------------------
-- Sync outbox — every write queued here for cloud replay
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS outbox (
    id                      TEXT PRIMARY KEY,
    table_name              TEXT NOT NULL,
    record_id               TEXT NOT NULL,
    operation               TEXT NOT NULL,           -- 'INSERT' | 'UPDATE' | 'DELETE'
    payload                 TEXT NOT NULL,            -- JSON snapshot of the record
    created_at              TEXT NOT NULL,
    synced_at               TEXT,                    -- NULL = pending
    sync_attempts           INTEGER NOT NULL DEFAULT 0,
    last_error              TEXT
);

-- -----------------------------------------------------------------------
-- Indexes
-- -----------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_deployments_incident    ON deployments(incident_id);
CREATE INDEX IF NOT EXISTS idx_deployments_personnel   ON deployments(personnel_id);
CREATE INDEX IF NOT EXISTS idx_gps_tracks_incident     ON gps_tracks(incident_id);
CREATE INDEX IF NOT EXISTS idx_gps_tracks_personnel    ON gps_tracks(personnel_id);
CREATE INDEX IF NOT EXISTS idx_gps_tracks_recorded     ON gps_tracks(recorded_at);
CREATE INDEX IF NOT EXISTS idx_radio_log_incident      ON radio_log(incident_id);
CREATE INDEX IF NOT EXISTS idx_search_segments_incident ON search_segments(incident_id);
CREATE INDEX IF NOT EXISTS idx_certifications_personnel ON certifications(personnel_id);
CREATE INDEX IF NOT EXISTS idx_outbox_synced           ON outbox(synced_at);
CREATE INDEX IF NOT EXISTS idx_sessions_token          ON sessions(token);
CREATE INDEX IF NOT EXISTS idx_sessions_user           ON sessions(user_id);

"""


def init_db():
    """
    Create all tables and indexes if they don't exist.
    Safe to call on every app startup — CREATE IF NOT EXISTS is idempotent.
    Alembic handles schema migrations after initial creation.
    """
    with local_db() as db:
        db.executescript(SCHEMA_SQL)
    log.info("Local SQLite schema initialized at %s", config.SQLITE_PATH)


# ---------------------------------------------------------------------------
# Version-locked writes
# ---------------------------------------------------------------------------

class VersionConflictError(Exception):
    """
    Raised when an UPDATE is attempted with a stale version number.
    The caller must re-fetch the record, show a diff to the user,
    and let them merge before retrying.
    """
    def __init__(self, table: str, record_id: str, expected: int, actual: int):
        self.table = table
        self.record_id = record_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Version conflict on {table}/{record_id}: "
            f"expected version {expected}, found {actual}. "
            f"Record was modified by another user. Re-fetch and merge."
        )


def versioned_update(table: str, record_id: str, fields: dict, expected_version: int):
    """
    Update a record only if its current version matches expected_version.
    Increments version on success. Queues the update in the outbox.

    Args:
        table:            Table name (must have id, version, updated_at columns)
        record_id:        UUID of the record to update
        fields:           Dict of column → new value (do not include version or updated_at)
        expected_version: The version the caller last read

    Raises:
        VersionConflictError: If the record has been modified since the caller last read it
        ValueError:           If record does not exist
    """
    fields = {k: v for k, v in fields.items() if k not in ("id", "version", "updated_at")}
    fields["updated_at"] = now_utc()
    new_version = expected_version + 1

    set_clause = ", ".join(f"{col} = ?" for col in fields)
    # values: field values + new_version + updated_at + record_id (WHERE) + expected_version (WHERE)
    values = list(fields.values()) + [new_version, fields["updated_at"], record_id, expected_version]

    with local_db() as db:
        # Check existence first for a clearer error
        row = db.execute(
            f"SELECT version FROM {table} WHERE id = ?", (record_id,)
        ).fetchone()

        if row is None:
            raise ValueError(f"Record {record_id} not found in {table}")

        if row["version"] != expected_version:
            raise VersionConflictError(table, record_id, expected_version, row["version"])

        db.execute(
            f"UPDATE {table} SET {set_clause}, version = ? "
            f"WHERE id = ? AND version = ?",
            list(fields.values()) + [new_version, record_id, expected_version],
        )

        # Fetch updated record for outbox snapshot
        updated = dict(
            db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
        )
        _queue_outbox(db, table, record_id, "UPDATE", updated)

    log.debug("Updated %s/%s → version %d", table, record_id, new_version)


def versioned_insert(table: str, record: dict) -> str:
    """
    Insert a new record. Adds id, version, created_at, updated_at if not present.
    Queues the insert in the outbox.

    Args:
        table:  Table name
        record: Dict of column → value

    Returns:
        The id of the inserted record
    """
    record = dict(record)
    if "id" not in record:
        record["id"] = new_id()
    if "version" not in record:
        record["version"] = 1
    ts = now_utc()
    record.setdefault("created_at", ts)
    record.setdefault("updated_at", ts)

    cols = ", ".join(record.keys())
    placeholders = ", ".join("?" * len(record))

    with local_db() as db:
        db.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
            list(record.values()),
        )
        _queue_outbox(db, table, record["id"], "INSERT", record)

    log.debug("Inserted %s/%s", table, record["id"])
    return record["id"]


def append_only_insert(table: str, record: dict) -> str:
    """
    Insert an append-only record (gps_tracks, radio_log, ics_211).
    These never get updated, so no version column is needed.
    Still queued in the outbox for cloud sync.

    Args:
        table:  Table name
        record: Dict of column → value

    Returns:
        The id of the inserted record
    """
    record = dict(record)
    if "id" not in record:
        record["id"] = new_id()
    record.setdefault("created_at", now_utc())

    cols = ", ".join(record.keys())
    placeholders = ", ".join("?" * len(record))

    with local_db() as db:
        db.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
            list(record.values()),
        )
        _queue_outbox(db, table, record["id"], "INSERT", record)

    return record["id"]


# ---------------------------------------------------------------------------
# Outbox queue
# ---------------------------------------------------------------------------

def _queue_outbox(db: sqlite3.Connection, table: str, record_id: str,
                  operation: str, payload: dict):
    """
    Internal. Write a pending sync entry to the outbox table.
    Must be called within an active transaction (db already open).
    """
    db.execute(
        "INSERT INTO outbox (id, table_name, record_id, operation, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), table, record_id, operation, json.dumps(payload, default=str), now_utc()),
    )


def get_pending_outbox(limit: int = 100) -> list[dict]:
    """
    Return up to `limit` unsynced outbox entries, oldest first.
    Used by sync.py to know what to replay to the cloud.
    """
    with local_db() as db:
        rows = db.execute(
            "SELECT * FROM outbox WHERE synced_at IS NULL "
            "ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_outbox_synced(outbox_id: str):
    """Mark an outbox entry as successfully synced."""
    with local_db() as db:
        db.execute(
            "UPDATE outbox SET synced_at = ? WHERE id = ?",
            (now_utc(), outbox_id),
        )


def mark_outbox_failed(outbox_id: str, error: str):
    """Record a sync failure on an outbox entry for retry."""
    with local_db() as db:
        db.execute(
            "UPDATE outbox SET sync_attempts = sync_attempts + 1, last_error = ? "
            "WHERE id = ?",
            (error, outbox_id),
        )


# ---------------------------------------------------------------------------
# Convenience read helpers
# ---------------------------------------------------------------------------

def get_record(table: str, record_id: str) -> dict | None:
    """Fetch a single record by id. Returns None if not found."""
    with local_db() as db:
        row = db.execute(
            f"SELECT * FROM {table} WHERE id = ?", (record_id,)
        ).fetchone()
    return dict(row) if row else None


def get_incident(incident_id: str) -> dict | None:
    return get_record("incidents", incident_id)


def get_active_incidents() -> list[dict]:
    """Return all incidents with status='active', newest first."""
    with local_db() as db:
        rows = db.execute(
            "SELECT * FROM incidents WHERE status = 'active' ORDER BY started_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_deployments(incident_id: str) -> list[dict]:
    """Return all active deployments for an incident, joined with personnel name."""
    with local_db() as db:
        rows = db.execute(
            """
            SELECT d.*, p.first_name, p.last_name, p.call_sign, p.blood_type
            FROM deployments d
            JOIN personnel p ON p.id = d.personnel_id
            WHERE d.incident_id = ?
            ORDER BY d.checked_in_at ASC
            """,
            (incident_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_personnel_certifications(personnel_id: str) -> list[dict]:
    """Return all certifications for a personnel record."""
    with local_db() as db:
        rows = db.execute(
            "SELECT * FROM certifications WHERE personnel_id = ? ORDER BY cert_type",
            (personnel_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_gps(incident_id: str, limit_per_person: int = 1) -> list[dict]:
    """
    Return the most recent GPS fix for each deployed operator on an incident.
    Used by BASECAMP map to show current field positions.
    """
    with local_db() as db:
        rows = db.execute(
            """
            SELECT g.*, p.first_name, p.last_name, p.call_sign
            FROM gps_tracks g
            JOIN personnel p ON p.id = g.personnel_id
            WHERE g.incident_id = ?
            AND g.recorded_at = (
                SELECT MAX(g2.recorded_at)
                FROM gps_tracks g2
                WHERE g2.personnel_id = g.personnel_id
                AND g2.incident_id = g.incident_id
            )
            ORDER BY p.last_name
            """,
            (incident_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_radio_log(incident_id: str, limit: int = 200) -> list[dict]:
    """Return radio log entries for an incident, newest first."""
    with local_db() as db:
        rows = db.execute(
            """
            SELECT r.*, p.call_sign, p.first_name, p.last_name
            FROM radio_log r
            LEFT JOIN personnel p ON p.id = r.personnel_id
            WHERE r.incident_id = ?
            ORDER BY r.logged_at DESC
            LIMIT ?
            """,
            (incident_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_medical_personnel(incident_id: str) -> list[dict]:
    """
    Return all deployed personnel with medical certifications for an incident.
    Used by the ICS-206 form compiler.
    """
    with local_db() as db:
        rows = db.execute(
            """
            SELECT p.id, p.first_name, p.last_name, p.call_sign,
                   p.blood_type, d.role, d.division, d.team,
                   c.cert_type, c.cert_number, c.expiry_date
            FROM deployments d
            JOIN personnel p ON p.id = d.personnel_id
            JOIN certifications c ON c.personnel_id = p.id
            WHERE d.incident_id = ?
            AND d.status = 'active'
            AND c.cert_type IN ('WFR', 'WEMT', 'EMT', 'Paramedic', 'RN', 'MD')
            ORDER BY p.last_name
            """,
            (incident_id,),
        ).fetchall()
    return [dict(r) for r in rows]
