"""
SARPack — basecamp/routes/dashboard.py
Dashboard data — incident stats, on-call roster, and system status.
Aggregates data from across the schema for the BASECAMP overview panel.
"""

import logging
from flask import Blueprint, jsonify, request
from core.auth import require_role
from core.db import local_db, now_utc
from core.sync import sync_status

log = logging.getLogger("basecamp.dashboard")
dashboard_bp = Blueprint("dashboard", __name__)


# ---------------------------------------------------------------------------
# GET /api/dashboard/
# Top-level dashboard — active incidents with key stats
# Primary query when BASECAMP loads
# ---------------------------------------------------------------------------

@dashboard_bp.route("/", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def dashboard():
    with local_db() as db:
        # Active incidents with deployment counts and last radio activity
        incidents = db.execute(
            """
            SELECT i.id, i.incident_number, i.incident_name, i.incident_type,
                   i.status, i.started_at, i.county, i.state, i.lat, i.lng,
                   p.first_name || ' ' || p.last_name as commander_name,
                   COUNT(DISTINCT d.id) as deployed_count,
                   SUM(CASE WHEN d.status = 'active' THEN 1 ELSE 0 END) as active_count,
                   MAX(r.logged_at) as last_radio,
                   COUNT(DISTINCT CASE WHEN r.is_missed_checkin = 1 THEN r.id END) as missed_checkins
            FROM incidents i
            LEFT JOIN personnel p ON p.id = i.incident_commander_id
            LEFT JOIN deployments d ON d.incident_id = i.id
            LEFT JOIN radio_log r ON r.incident_id = i.id
            WHERE i.status = 'active'
            GROUP BY i.id
            ORDER BY i.started_at DESC
            """,
        ).fetchall()

        # System-wide totals
        totals = db.execute(
            """
            SELECT
                COUNT(DISTINCT CASE WHEN i.status = 'active' THEN i.id END) as active_incidents,
                COUNT(DISTINCT CASE WHEN d.status = 'active' THEN d.personnel_id END) as deployed_personnel,
                COUNT(DISTINCT CASE WHEN ss.status = 'assigned' THEN ss.id END) as assigned_segments,
                COUNT(DISTINCT CASE WHEN ss.status = 'cleared' THEN ss.id END) as cleared_segments
            FROM incidents i
            LEFT JOIN deployments d ON d.incident_id = i.id AND i.status = 'active'
            LEFT JOIN search_segments ss ON ss.incident_id = i.id AND i.status = 'active'
            """,
        ).fetchone()

    return jsonify({
        "incidents":  [dict(r) for r in incidents],
        "totals":     dict(totals),
        "sync":       sync_status(),
        "generated_at": now_utc(),
    })


# ---------------------------------------------------------------------------
# GET /api/dashboard/oncall
# Who is currently on-call — pulled from WARDEN schedules
# Shown on BASECAMP landing page so IC knows who to deploy
# ---------------------------------------------------------------------------

@dashboard_bp.route("/oncall", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def oncall_roster():
    now = now_utc()

    with local_db() as db:
        rows = db.execute(
            """
            SELECT s.shift_name, s.ends_at,
                   p.id as personnel_id, p.first_name, p.last_name,
                   p.call_sign, p.phone, p.blood_type,
                   GROUP_CONCAT(c.cert_type, ', ') as certifications,
                   d_active.incident_id as currently_deployed_to
            FROM schedules s
            JOIN personnel p ON p.id = s.personnel_id
            LEFT JOIN certifications c ON c.personnel_id = p.id
            LEFT JOIN deployments d_active ON (
                d_active.personnel_id = p.id
                AND d_active.status = 'active'
            )
            WHERE s.is_oncall = 1
            AND s.starts_at <= ?
            AND s.ends_at >= ?
            AND p.is_active = 1
            GROUP BY s.id
            ORDER BY p.last_name, p.first_name
            """,
            (now, now),
        ).fetchall()

    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# GET /api/dashboard/incident/<id>
# Single incident stats panel
# Called by BASECAMP when an incident is selected
# ---------------------------------------------------------------------------

@dashboard_bp.route("/incident/<incident_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def incident_stats(incident_id):
    with local_db() as db:
        # Deployment breakdown
        deployments = db.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN status = 'checked_out' THEN 1 ELSE 0 END) as checked_out
            FROM deployments WHERE incident_id = ?
            """,
            (incident_id,),
        ).fetchone()

        # Segment breakdown
        segments = db.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'unassigned' THEN 1 ELSE 0 END) as unassigned,
                SUM(CASE WHEN status = 'assigned'   THEN 1 ELSE 0 END) as assigned,
                SUM(CASE WHEN status = 'cleared'    THEN 1 ELSE 0 END) as cleared,
                SUM(CASE WHEN status = 'suspended'  THEN 1 ELSE 0 END) as suspended
            FROM search_segments WHERE incident_id = ?
            """,
            (incident_id,),
        ).fetchone()

        # Radio log summary
        radio = db.execute(
            """
            SELECT COUNT(*) as total_entries,
                   SUM(CASE WHEN is_missed_checkin = 1 THEN 1 ELSE 0 END) as missed_checkins,
                   MAX(logged_at) as last_entry
            FROM radio_log WHERE incident_id = ?
            """,
            (incident_id,),
        ).fetchone()

        # GPS track coverage — unique operators with a fix
        gps = db.execute(
            "SELECT COUNT(DISTINCT personnel_id) as operators_with_gps "
            "FROM gps_tracks WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()

        # ICS forms signed vs unsigned
        forms = {}
        for form in ("ics_201", "ics_204", "ics_205", "ics_206",
                     "ics_209", "ics_211", "ics_214", "ics_215"):
            try:
                row = db.execute(
                    f"SELECT COUNT(*) as total, "
                    f"SUM(CASE WHEN signed_at IS NOT NULL THEN 1 ELSE 0 END) as signed "
                    f"FROM {form} WHERE incident_id = ?",
                    (incident_id,),
                ).fetchone()
                if row:
                    forms[form] = dict(row)
            except Exception:
                forms[form] = {"total": 0, "signed": 0}

    return jsonify({
        "incident_id": incident_id,
        "deployments": dict(deployments) if deployments else {},
        "segments":    dict(segments)    if segments    else {},
        "radio":       dict(radio)       if radio       else {},
        "gps":         dict(gps)         if gps         else {},
        "ics_forms":   forms,
        "generated_at": now_utc(),
    })


# ---------------------------------------------------------------------------
# GET /api/dashboard/status
# System health — sync status, DB connectivity, app versions
# Used by the Toughbook tray icon and BASECAMP status bar
# ---------------------------------------------------------------------------

@dashboard_bp.route("/status", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def system_status():
    from core.config import config
    return jsonify({
        "status":  "ok",
        "mode":    config.MODE,
        "sync":    sync_status(),
        "checked_at": now_utc(),
    })
