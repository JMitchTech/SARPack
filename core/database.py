"""
SARPack 2.0 — core/database.py
SQLite connection management. Single database for the entire platform.
"""

import sqlite3
import threading
from pathlib import Path
from core.config import Config

# Thread-local storage so each thread gets its own connection
_local = threading.local()


def get_db() -> sqlite3.Connection:
    """Return a thread-local database connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(
            Config.DB_PATH,
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
    return _local.conn


def close_db():
    """Close the thread-local connection if open."""
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None


def init_db():
    """
    Create all tables if they don't exist.
    Safe to call on every startup — uses IF NOT EXISTS throughout.
    """
    db = get_db()

    db.executescript("""
    -- =========================================================
    --  USERS & AUTH
    -- =========================================================
    CREATE TABLE IF NOT EXISTS users (
        id                  TEXT PRIMARY KEY,
        username            TEXT UNIQUE NOT NULL,
        password_hash       TEXT NOT NULL,
        role                TEXT NOT NULL DEFAULT 'field_op',
        -- roles: super_admin | admin | ic | logistics | field_op | observer
        is_active           INTEGER NOT NULL DEFAULT 1,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        mfa_secret          TEXT,           -- TOTP secret (WARDEN MFA)
        mfa_enabled         INTEGER NOT NULL DEFAULT 0,
        personnel_id        TEXT,           -- links to personnel record
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        last_login_at       TEXT
    );

    -- =========================================================
    --  PERSONNEL (WARDEN)
    -- =========================================================
    CREATE TABLE IF NOT EXISTS personnel (
        id              TEXT PRIMARY KEY,
        first_name      TEXT NOT NULL,
        last_name       TEXT NOT NULL,
        call_sign       TEXT UNIQUE,
        blood_type      TEXT,
        phone           TEXT,
        email           TEXT,
        home_agency     TEXT,
        is_active       INTEGER NOT NULL DEFAULT 1,
        notes           TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS certifications (
        id              TEXT PRIMARY KEY,
        personnel_id    TEXT NOT NULL REFERENCES personnel(id) ON DELETE CASCADE,
        cert_type       TEXT NOT NULL,
        cert_number     TEXT,
        issued_date     TEXT,
        expiry_date     TEXT,
        issuing_body    TEXT,
        is_verified     INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS assets (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        asset_type      TEXT NOT NULL,
        -- types: vehicle | atv | drone | k9 | boat | equipment
        serial_number   TEXT,
        owner_agency    TEXT,
        status          TEXT NOT NULL DEFAULT 'available',
        -- statuses: available | deployed | maintenance | retired
        notes           TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- Drone assets get stream URLs for live feed
    CREATE TABLE IF NOT EXISTS drone_assets (
        id              TEXT PRIMARY KEY,
        asset_id        TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        stream_url      TEXT,           -- RTSP or HLS stream URL
        operator_id     TEXT REFERENCES personnel(id),
        is_registered   INTEGER NOT NULL DEFAULT 0,
        last_seen_at    TEXT
    );

    -- Training materials (WARDEN)
    CREATE TABLE IF NOT EXISTS training_materials (
        id              TEXT PRIMARY KEY,
        title           TEXT NOT NULL,
        description     TEXT,
        file_path       TEXT NOT NULL,
        file_type       TEXT NOT NULL,   -- pdf | pptx | docx | xlsx
        uploaded_by     TEXT REFERENCES users(id),
        category        TEXT,            -- ics | sar | medical | technical | general
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- =========================================================
    --  INCIDENTS (BASECAMP)
    -- =========================================================
    CREATE TABLE IF NOT EXISTS incidents (
        id              TEXT PRIMARY KEY,
        incident_number TEXT UNIQUE NOT NULL,
        incident_name   TEXT NOT NULL,
        incident_type   TEXT NOT NULL DEFAULT 'sar',
        -- types: sar | usar | disaster_relief | training | standby | medical
        status          TEXT NOT NULL DEFAULT 'active',
        -- statuses: active | closed | standby | suspended
        county          TEXT,
        state           TEXT DEFAULT 'PA',
        latitude        REAL,
        longitude       REAL,
        lkp_lat         REAL,
        lkp_lng         REAL,
        lkp_notes       TEXT,
        description     TEXT,
        ic_name         TEXT,
        started_at      TEXT NOT NULL DEFAULT (datetime('now')),
        closed_at       TEXT,
        signed_at       TEXT,
        signed_by       TEXT REFERENCES users(id),
        created_by      TEXT REFERENCES users(id),
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- =========================================================
    --  DEPLOYMENTS (BASECAMP)
    -- =========================================================
    CREATE TABLE IF NOT EXISTS deployments (
        id              TEXT PRIMARY KEY,
        incident_id     TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        personnel_id    TEXT NOT NULL REFERENCES personnel(id),
        role            TEXT NOT NULL DEFAULT 'field_op',
        division        TEXT,
        team            TEXT,
        checked_in_at   TEXT NOT NULL DEFAULT (datetime('now')),
        checked_out_at  TEXT,
        status          TEXT NOT NULL DEFAULT 'active'
        -- statuses: active | checked_out | standby
    );

    -- =========================================================
    --  SEARCH SEGMENTS (BASECAMP MAP)
    -- =========================================================
    CREATE TABLE IF NOT EXISTS search_segments (
        id              TEXT PRIMARY KEY,
        incident_id     TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        segment_id      TEXT NOT NULL,
        area_name       TEXT,
        description     TEXT,
        status          TEXT NOT NULL DEFAULT 'unassigned',
        -- statuses: unassigned | assigned | cleared | suspended
        assigned_to     TEXT,           -- division or team name
        pod             REAL,           -- probability of detection 0-100
        boundary_coords TEXT,           -- JSON array of [lat, lng] pairs
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- Map markers — LZ pins, DZ targets, points of interest
    CREATE TABLE IF NOT EXISTS map_markers (
        id              TEXT PRIMARY KEY,
        incident_id     TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        marker_type     TEXT NOT NULL,
        -- types: lz | dz | poi | hazard | camp | staging
        label           TEXT,
        latitude        REAL NOT NULL,
        longitude       REAL NOT NULL,
        notes           TEXT,
        created_by      TEXT REFERENCES users(id),
        -- For DZ targeting: target device
        target_device   TEXT,           -- trailhead device/personnel_id
        broadcast_at    TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- =========================================================
    --  GPS POSITIONS (BASECAMP MAP)
    -- =========================================================
    CREATE TABLE IF NOT EXISTS gps_positions (
        id              TEXT PRIMARY KEY,
        incident_id     TEXT REFERENCES incidents(id) ON DELETE CASCADE,
        personnel_id    TEXT REFERENCES personnel(id),
        latitude        REAL NOT NULL,
        longitude       REAL NOT NULL,
        altitude        REAL,
        accuracy        REAL,
        recorded_at     TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- =========================================================
    --  RADIO LOG (BASECAMP)
    -- =========================================================
    CREATE TABLE IF NOT EXISTS radio_entries (
        id              TEXT PRIMARY KEY,
        incident_id     TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        personnel_id    TEXT REFERENCES personnel(id),
        message         TEXT NOT NULL,
        channel         TEXT,
        is_missed       INTEGER NOT NULL DEFAULT 0,
        source          TEXT DEFAULT 'basecamp',
        -- sources: basecamp | trailhead | relay
        logged_at       TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- =========================================================
    --  PATIENTS (BASECAMP / TRAILHEAD)
    -- =========================================================
    CREATE TABLE IF NOT EXISTS patients (
        id                  TEXT PRIMARY KEY,
        incident_id         TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        reported_by         TEXT REFERENCES personnel(id),
        patient_name        TEXT,
        patient_age         INTEGER,
        patient_sex         TEXT,
        chief_complaint     TEXT,
        complaint_category  TEXT,
        mechanism           TEXT,       -- tap-select field from Trailhead
        body_region         TEXT,       -- tap-select field from Trailhead
        injury_type         TEXT,       -- tap-select field from Trailhead
        severity            TEXT,       -- tap-select: minor | moderate | serious | critical
        loc                 TEXT,       -- Alert | Verbal | Pain | Unresponsive
        vitals              TEXT,       -- JSON blob
        treatment_given     TEXT,
        scene_lat           REAL,
        scene_lng           REAL,
        transport_method    TEXT,
        receiving_facility  TEXT,
        assessed_at         TEXT NOT NULL DEFAULT (datetime('now')),
        created_at          TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- =========================================================
    --  ICS FORMS (LOGBOOK)
    -- =========================================================
    CREATE TABLE IF NOT EXISTS ics_forms (
        id              TEXT PRIMARY KEY,
        incident_id     TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        form_key        TEXT NOT NULL,
        -- keys: ics_201 | ics_204 | ics_205 | ics_206 | ics_209 | ics_211 | ics_214 | ics_215
        version         INTEGER NOT NULL DEFAULT 1,
        data            TEXT,           -- JSON blob of form fields
        narrative       TEXT,           -- IC narrative fields
        status          TEXT NOT NULL DEFAULT 'draft',
        -- statuses: draft | compiled | signed
        signed_at       TEXT,
        signed_by       TEXT REFERENCES users(id),
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- =========================================================
    --  RELAY / RADIO CONFIG
    -- =========================================================
    CREATE TABLE IF NOT EXISTS relay_nodes (
        id              TEXT PRIMARY KEY,
        node_id         TEXT UNIQUE NOT NULL,   -- Meshtastic node ID
        node_name       TEXT,
        node_type       TEXT DEFAULT 'field',
        -- types: field | base | repeater | gateway
        incident_id     TEXT REFERENCES incidents(id),
        last_seen_at    TEXT,
        battery_pct     INTEGER,
        snr             REAL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS radio_registry (
        id              TEXT PRIMARY KEY,
        personnel_id    TEXT REFERENCES personnel(id),
        radio_make      TEXT,           -- Motorola | Kenwood | BK | Baofeng | etc.
        radio_model     TEXT,
        radio_type      TEXT,           -- analog | digital | p25 | dmr | nxdn | meshtastic
        programmed_channels TEXT,       -- JSON array
        notes           TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- =========================================================
    --  SOS ALERTS
    -- =========================================================
    CREATE TABLE IF NOT EXISTS sos_alerts (
        id              TEXT PRIMARY KEY,
        incident_id     TEXT REFERENCES incidents(id),
        personnel_id    TEXT REFERENCES personnel(id),
        latitude        REAL,
        longitude       REAL,
        message         TEXT,
        acknowledged_by TEXT REFERENCES users(id),
        acknowledged_at TEXT,
        resolved_at     TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- =========================================================
    --  AUDIT LOG
    -- =========================================================
    CREATE TABLE IF NOT EXISTS audit_log (
        id              TEXT PRIMARY KEY,
        user_id         TEXT REFERENCES users(id),
        action          TEXT NOT NULL,
        target_type     TEXT,
        target_id       TEXT,
        detail          TEXT,
        ip_address      TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)

    db.commit()
    print("[SARPack] Database initialized.")


def row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row) if row else None


def rows_to_list(rows) -> list:
    """Convert a list of sqlite3.Row objects to a list of dicts."""
    return [dict(r) for r in rows] if rows else []