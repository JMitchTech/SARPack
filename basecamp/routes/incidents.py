"""
SARPack — basecamp/routes/incidents.py
Incident lifecycle — create, update, close, and query incidents.
Every other BASECAMP resource is scoped to an incident_id.
Supports simultaneous active incidents of different types.
"""

import logging
from flask import Blueprint, jsonify, request
from core.auth import require_role, require_ic, get_current_user
from core.db import (
    versioned_insert,
    versioned_update,
    get_record,
    get_active_incidents,
    local_db,
    now_utc,
    VersionConflictError,
)

log = logging.getLogger("basecamp.incidents")
incidents_bp = Blueprint("incidents", __name__)

INCIDENT_TYPES   = ("sar", "disaster_relief", "training", "standby", "other")
INCIDENT_STATUSES = ("active", "closed", "standby")


# ---------------------------------------------------------------------------
# GET /api/incidents/
# All active incidents — primary landing query for BASECAMP dashboard
# ---------------------------------------------------------------------------

@incidents_bp.route("/", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def list_incidents():
    status = request.args.get("status", "active")
    if status not in INCIDENT_STATUSES + ("all",):
        return jsonify({
            "error": f"Invalid status filter '{status}'",
            "valid": list(INCIDENT_STATUSES) + ["all"],
        }), 400

    with local_db() as db:
        if status == "all":
            rows = db.execute(
                """
                SELECT i.*,
                       p.first_name || ' ' || p.last_name as commander_name,
                       COUNT(DISTINCT d.id) as deployed_count
                FROM incidents i
                LEFT JOIN personnel p ON p.id = i.incident_commander_id
                LEFT JOIN deployments d ON d.incident_id = i.id AND d.status = 'active'
                GROUP BY i.id
                ORDER BY i.started_at DESC
                """
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT i.*,
                       p.first_name || ' ' || p.last_name as commander_name,
                       COUNT(DISTINCT d.id) as deployed_count
                FROM incidents i
                LEFT JOIN personnel p ON p.id = i.incident_commander_id
                LEFT JOIN deployments d ON d.incident_id = i.id AND d.status = 'active'
                WHERE i.status = ?
                GROUP BY i.id
                ORDER BY i.started_at DESC
                """,
                (status,),
            ).fetchall()

    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# GET /api/incidents/<id>
# Full incident detail — all related data joined
# ---------------------------------------------------------------------------

@incidents_bp.route("/<incident_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def get_incident(incident_id):
    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    with local_db() as db:
        # Active deployments with personnel detail
        deployments = db.execute(
            """
            SELECT d.*, p.first_name, p.last_name, p.call_sign,
                   p.blood_type, p.phone
            FROM deployments d
            JOIN personnel p ON p.id = d.personnel_id
            WHERE d.incident_id = ?
            ORDER BY d.checked_in_at ASC
            """,
            (incident_id,),
        ).fetchall()

        # Search segments
        segments = db.execute(
            "SELECT * FROM search_segments WHERE incident_id = ? ORDER BY segment_id",
            (incident_id,),
        ).fetchall()

        # Recent radio log (last 50)
        radio = db.execute(
            """
            SELECT r.*, p.call_sign, p.first_name, p.last_name
            FROM radio_log r
            LEFT JOIN personnel p ON p.id = r.personnel_id
            WHERE r.incident_id = ?
            ORDER BY r.logged_at DESC LIMIT 50
            """,
            (incident_id,),
        ).fetchall()

        # Latest GPS position per operator
        gps = db.execute(
            """
            SELECT g.*, p.first_name, p.last_name, p.call_sign
            FROM gps_tracks g
            JOIN personnel p ON p.id = g.personnel_id
            WHERE g.incident_id = ?
            AND g.recorded_at = (
                SELECT MAX(g2.recorded_at) FROM gps_tracks g2
                WHERE g2.personnel_id = g.personnel_id
                AND g2.incident_id = g.incident_id
            )
            """,
            (incident_id,),
        ).fetchall()

    incident["deployments"] = [dict(d) for d in deployments]
    incident["segments"]    = [dict(s) for s in segments]
    incident["radio_log"]   = [dict(r) for r in radio]
    incident["gps_tracks"]  = [dict(g) for g in gps]

    return jsonify(incident)


# ---------------------------------------------------------------------------
# POST /api/incidents/
# Create a new incident — IC or ops_chief only
# Auto-generates incident number: TYPE-YYYYMMDD-NNN
# ---------------------------------------------------------------------------

@incidents_bp.route("/", methods=["POST"])
@require_role("IC", "ops_chief")
def create_incident():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    required = ("incident_name", "incident_type")
    missing = [f for f in required if not data.get(f, "").strip()]
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    incident_type = data["incident_type"].strip().lower()
    if incident_type not in INCIDENT_TYPES:
        return jsonify({
            "error": f"Invalid incident_type",
            "valid_types": INCIDENT_TYPES,
        }), 400

    # Auto-generate incident number
    ts = now_utc()[:10].replace("-", "")  # YYYYMMDD
    with local_db() as db:
        count = db.execute(
            "SELECT COUNT(*) as n FROM incidents WHERE incident_number LIKE ?",
            (f"{incident_type}-{ts}-%",),
        ).fetchone()["n"]
    incident_number = f"{incident_type}-{ts}-{str(count + 1).zfill(3)}"

    # Validate commander if provided
    commander_id = data.get("incident_commander_id")
    if commander_id:
        if not get_record("personnel", commander_id):
            return jsonify({"error": "Incident commander personnel record not found"}), 404

    # Default commander to the creating user's linked personnel record
    if not commander_id:
        user = get_current_user()
        commander_id = user.get("personnel_id")

    record = {
        "incident_number":        incident_number,
        "incident_name":          data["incident_name"].strip(),
        "incident_type":          incident_type,
        "status":                 "active",
        "lat":                    data.get("lat"),
        "lng":                    data.get("lng"),
        "county":                 data.get("county", "").strip() or None,
        "state":                  data.get("state", "PA").strip(),
        "started_at":             data.get("started_at", now_utc()),
        "incident_commander_id":  commander_id,
        "notes":                  data.get("notes", "").strip() or None,
    }

    incident_id = versioned_insert("incidents", record)

    log.info(
        "Incident created: %s — %s (%s)",
        incident_number, record["incident_name"], incident_id,
    )

    # Broadcast to all connected BASECAMP clients
    try:
        from basecamp.app import socketio
        socketio.emit("incident_created", {
            "incident_id":     incident_id,
            "incident_number": incident_number,
            "incident_name":   record["incident_name"],
            "incident_type":   record["incident_type"],
        })
    except Exception:
        pass  # non-fatal if socketio not yet bound

    return jsonify({
        "message":         "Incident created",
        "id":              incident_id,
        "incident_number": incident_number,
    }), 201


# ---------------------------------------------------------------------------
# PATCH /api/incidents/<id>
# Update incident details — name, location, commander, notes
# ---------------------------------------------------------------------------

@incidents_bp.route("/<incident_id>", methods=["PATCH"])
@require_role("IC", "ops_chief")
def update_incident(incident_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    version = data.get("version")
    if version is None:
        return jsonify({"error": "version is required for updates"}), 400

    protected = ("id", "incident_number", "status", "version",
                 "created_at", "updated_at", "started_at", "closed_at")
    fields = {k: v for k, v in data.items() if k not in protected}

    if not fields:
        return jsonify({"error": "No updatable fields provided"}), 400

    if "incident_type" in fields:
        if fields["incident_type"].lower() not in INCIDENT_TYPES:
            return jsonify({"error": "Invalid incident_type"}), 400
        fields["incident_type"] = fields["incident_type"].lower()

    try:
        versioned_update("incidents", incident_id, fields, expected_version=int(version))
    except VersionConflictError as e:
        return jsonify({
            "error":            "Version conflict",
            "expected_version": e.expected,
            "current_version":  e.actual,
        }), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    log.info("Updated incident: %s", incident_id)
    return jsonify({"message": "Incident updated", "id": incident_id})


# ---------------------------------------------------------------------------
# POST /api/incidents/<id>/close
# Close an active incident — IC only
# Checks out all active deployments automatically
# ---------------------------------------------------------------------------

@incidents_bp.route("/<incident_id>/close", methods=["POST"])
@require_ic
def close_incident(incident_id):
    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    if incident["status"] == "closed":
        return jsonify({"error": "Incident is already closed"}), 400

    ts = now_utc()

    # Auto checkout all still-active deployments
    with local_db() as db:
        active = db.execute(
            "SELECT id, version FROM deployments "
            "WHERE incident_id = ? AND status = 'active'",
            (incident_id,),
        ).fetchall()

        for dep in active:
            db.execute(
                "UPDATE deployments SET status = 'checked_out', "
                "checked_out_at = ?, updated_at = ?, version = version + 1 "
                "WHERE id = ?",
                (ts, ts, dep["id"]),
            )

    try:
        versioned_update(
            "incidents", incident_id,
            {"status": "closed", "closed_at": ts},
            expected_version=incident["version"],
        )
    except VersionConflictError:
        return jsonify({"error": "Version conflict — re-fetch and try again"}), 409

    log.info(
        "Incident closed: %s (%d deployments checked out)",
        incident_id, len(active),
    )

    try:
        from basecamp.app import socketio
        socketio.emit("incident_closed", {
            "incident_id":   incident_id,
            "incident_name": incident["incident_name"],
            "closed_at":     ts,
        })
    except Exception:
        pass

    return jsonify({
        "message":              "Incident closed",
        "id":                   incident_id,
        "deployments_closed":   len(active),
        "closed_at":            ts,
    })


# ---------------------------------------------------------------------------
# POST /api/incidents/<id>/reopen
# Reopen a closed incident — IC only
# ---------------------------------------------------------------------------

@incidents_bp.route("/<incident_id>/reopen", methods=["POST"])
@require_ic
def reopen_incident(incident_id):
    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    if incident["status"] == "active":
        return jsonify({"error": "Incident is already active"}), 400

    try:
        versioned_update(
            "incidents", incident_id,
            {"status": "active", "closed_at": None},
            expected_version=incident["version"],
        )
    except VersionConflictError:
        return jsonify({"error": "Version conflict — re-fetch and try again"}), 409

    log.info("Incident reopened: %s", incident_id)
    return jsonify({"message": "Incident reopened", "id": incident_id})


# ---------------------------------------------------------------------------
# GET /api/incidents/types
# Valid incident types — for frontend dropdowns
# ---------------------------------------------------------------------------

@incidents_bp.route("/types", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def incident_types():
    return jsonify(list(INCIDENT_TYPES))
