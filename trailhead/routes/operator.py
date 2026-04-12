"""
SARPack — trailhead/routes/operator.py
Field operator routes. Gives operators read access to their
active incident, deployment assignment, and search segment.
Field operators have no write access to incident management —
they can only push GPS, submit patient forms, and log radio contacts.
"""

import logging
from flask import Blueprint, jsonify, request
from core.auth import require_role, get_current_user
from core.db import get_record, local_db, now_utc, append_only_insert

log = logging.getLogger("trailhead.operator")
operator_bp = Blueprint("operator", __name__)


# ---------------------------------------------------------------------------
# GET /api/operator/me
# Return the current operator's active deployment and incident.
# This is the primary bootstrap call when TRAILHEAD loads.
# ---------------------------------------------------------------------------

@operator_bp.route("/me", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "field_op", "observer")
def get_my_status():
    user = get_current_user()
    personnel_id = user.get("personnel_id")

    if not personnel_id:
        return jsonify({
            "error": "Your user account is not linked to a personnel record. "
                     "Contact your IC or logistics officer.",
        }), 400

    person = get_record("personnel", personnel_id)
    if not person:
        return jsonify({"error": "Personnel record not found"}), 404

    with local_db() as db:
        # Find active deployment
        deployment = db.execute(
            """
            SELECT d.*, i.incident_name, i.incident_number, i.incident_type,
                   i.lat as incident_lat, i.lng as incident_lng,
                   i.county, i.state, i.started_at, i.status as incident_status
            FROM deployments d
            JOIN incidents i ON i.id = d.incident_id
            WHERE d.personnel_id = ? AND d.status = 'active'
            AND i.status = 'active'
            ORDER BY d.checked_in_at DESC LIMIT 1
            """,
            (personnel_id,),
        ).fetchone()

        if not deployment:
            return jsonify({
                "deployed": False,
                "personnel": {
                    "id":         person["id"],
                    "name":       f"{person['first_name']} {person['last_name']}",
                    "call_sign":  person["call_sign"],
                },
                "message": "You are not currently checked in to any active incident.",
            })

        deployment = dict(deployment)

        # Find assigned search segment
        segment = None
        if deployment.get("team"):
            seg_row = db.execute(
                """
                SELECT * FROM search_segments
                WHERE incident_id = ? AND assigned_team = ?
                AND status IN ('assigned', 'unassigned')
                ORDER BY segment_id LIMIT 1
                """,
                (deployment["incident_id"], deployment["team"]),
            ).fetchone()
            if seg_row:
                segment = dict(seg_row)

        # Last known GPS fix for this operator
        last_gps = db.execute(
            """
            SELECT lat, lng, elevation, recorded_at
            FROM gps_tracks
            WHERE personnel_id = ? AND incident_id = ?
            ORDER BY recorded_at DESC LIMIT 1
            """,
            (personnel_id, deployment["incident_id"]),
        ).fetchone()

    return jsonify({
        "deployed":   True,
        "personnel": {
            "id":        person["id"],
            "name":      f"{person['first_name']} {person['last_name']}",
            "call_sign": person["call_sign"],
            "blood_type": person["blood_type"],
        },
        "deployment": {
            "id":            deployment["id"],
            "incident_id":   deployment["incident_id"],
            "incident_name": deployment["incident_name"],
            "incident_number": deployment["incident_number"],
            "incident_type": deployment["incident_type"],
            "incident_lat":  deployment["incident_lat"],
            "incident_lng":  deployment["incident_lng"],
            "county":        deployment["county"],
            "state":         deployment["state"],
            "started_at":    deployment["started_at"],
            "role":          deployment["role"],
            "division":      deployment["division"],
            "team":          deployment["team"],
            "checked_in_at": deployment["checked_in_at"],
        },
        "segment":  segment,
        "last_gps": dict(last_gps) if last_gps else None,
    })


# ---------------------------------------------------------------------------
# GET /api/operator/incident/<id>
# Lightweight incident summary for TRAILHEAD map screen.
# Returns incident location, segments, and team positions.
# Field operators get a read-only view — no management data.
# ---------------------------------------------------------------------------

@operator_bp.route("/incident/<incident_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "field_op", "observer")
def get_incident_summary(incident_id):
    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    with local_db() as db:
        # All active deployments with last known position
        operators = db.execute(
            """
            SELECT d.role, d.division, d.team,
                   p.first_name, p.last_name, p.call_sign,
                   g.lat, g.lng, g.recorded_at
            FROM deployments d
            JOIN personnel p ON p.id = d.personnel_id
            LEFT JOIN gps_tracks g ON (
                g.personnel_id = d.personnel_id
                AND g.incident_id = d.incident_id
                AND g.recorded_at = (
                    SELECT MAX(g2.recorded_at) FROM gps_tracks g2
                    WHERE g2.personnel_id = d.personnel_id
                    AND g2.incident_id = d.incident_id
                )
            )
            WHERE d.incident_id = ? AND d.status = 'active'
            ORDER BY p.last_name
            """,
            (incident_id,),
        ).fetchall()

        # Search segments for map overlay
        segments = db.execute(
            "SELECT segment_id, status, boundary_coords, assigned_team "
            "FROM search_segments WHERE incident_id = ? ORDER BY segment_id",
            (incident_id,),
        ).fetchall()

    return jsonify({
        "incident": {
            "id":             incident["id"],
            "name":           incident["incident_name"],
            "number":         incident["incident_number"],
            "type":           incident["incident_type"],
            "lat":            incident["lat"],
            "lng":            incident["lng"],
            "county":         incident["county"],
            "state":          incident["state"],
            "started_at":     incident["started_at"],
        },
        "operators": [dict(o) for o in operators],
        "segments":  [dict(s) for s in segments],
    })


# ---------------------------------------------------------------------------
# POST /api/operator/radio
# Log a radio contact from the field.
# Field operators can log their own transmissions.
# Append-only — no editing radio log entries.
# ---------------------------------------------------------------------------

@operator_bp.route("/radio", methods=["POST"])
@require_role("IC", "ops_chief", "logistics", "field_op")
def log_radio():
    user = get_current_user()
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    required = ("incident_id", "message")
    missing = [f for f in required if not data.get(f, "").strip()]
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    record = {
        "incident_id":       data["incident_id"],
        "personnel_id":      user.get("personnel_id"),
        "channel":           data.get("channel", "").strip() or None,
        "message":           data["message"].strip(),
        "logged_at":         data.get("logged_at", now_utc()),
        "is_missed_checkin": 0,
        "source":            "trailhead",
    }

    entry_id = append_only_insert("radio_log", record)
    return jsonify({"message": "Radio entry logged", "id": entry_id}), 201


# ---------------------------------------------------------------------------
# GET /api/operator/checkin-status/<incident_id>
# Check whether the current operator is checked in to a specific incident.
# Used by TRAILHEAD on startup to confirm deployment status.
# ---------------------------------------------------------------------------

@operator_bp.route("/checkin-status/<incident_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "field_op", "observer")
def checkin_status(incident_id):
    user = get_current_user()
    personnel_id = user.get("personnel_id")

    if not personnel_id:
        return jsonify({"checked_in": False, "reason": "No personnel record linked"})

    with local_db() as db:
        dep = db.execute(
            "SELECT id, role, division, team, checked_in_at, status "
            "FROM deployments WHERE incident_id = ? AND personnel_id = ?",
            (incident_id, personnel_id),
        ).fetchone()

    if not dep:
        return jsonify({"checked_in": False, "reason": "No deployment record found"})

    dep = dict(dep)
    return jsonify({
        "checked_in":    dep["status"] == "active",
        "deployment_id": dep["id"],
        "role":          dep["role"],
        "division":      dep["division"],
        "team":          dep["team"],
        "checked_in_at": dep["checked_in_at"],
        "status":        dep["status"],
    })
