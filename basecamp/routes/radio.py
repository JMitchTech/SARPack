"""
SARPack — basecamp/routes/radio.py
Radio log management for active incidents.
Logs are append-only — never updated or deleted.
Missed check-ins are flagged and broadcast via SocketIO immediately.
Feeds ICS-214 (activity log) in LOGBOOK.
"""

import logging
from flask import Blueprint, jsonify, request
from core.auth import require_role
from core.db import (
    append_only_insert,
    get_record,
    get_radio_log,
    local_db,
    now_utc,
)

log = logging.getLogger("basecamp.radio")
radio_bp = Blueprint("radio", __name__)


# ---------------------------------------------------------------------------
# GET /api/radio/<incident_id>
# Radio log for an incident — newest first, paginated
# ---------------------------------------------------------------------------

@radio_bp.route("/<incident_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def list_radio_log(incident_id):
    if not get_record("incidents", incident_id):
        return jsonify({"error": "Incident not found"}), 404

    limit  = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))

    with local_db() as db:
        rows = db.execute(
            """
            SELECT r.*, p.call_sign, p.first_name, p.last_name
            FROM radio_log r
            LEFT JOIN personnel p ON p.id = r.personnel_id
            WHERE r.incident_id = ?
            ORDER BY r.logged_at DESC
            LIMIT ? OFFSET ?
            """,
            (incident_id, limit, offset),
        ).fetchall()

    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# GET /api/radio/<incident_id>/missed
# All missed check-in entries for an incident
# Used by IC to review overdue operators
# ---------------------------------------------------------------------------

@radio_bp.route("/<incident_id>/missed", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def missed_checkins(incident_id):
    with local_db() as db:
        rows = db.execute(
            """
            SELECT r.*, p.call_sign, p.first_name, p.last_name, p.phone
            FROM radio_log r
            LEFT JOIN personnel p ON p.id = r.personnel_id
            WHERE r.incident_id = ?
            AND r.is_missed_checkin = 1
            ORDER BY r.logged_at DESC
            """,
            (incident_id,),
        ).fetchall()

    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# POST /api/radio/<incident_id>
# Log a radio entry manually from BASECAMP
# ---------------------------------------------------------------------------

@radio_bp.route("/<incident_id>", methods=["POST"])
@require_role("IC", "ops_chief", "logistics")
def log_entry(incident_id):
    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    if incident["status"] != "active":
        return jsonify({"error": "Cannot log radio entries on a non-active incident"}), 400

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    if not data.get("message", "").strip():
        return jsonify({"error": "message is required"}), 400

    record = {
        "incident_id":      incident_id,
        "personnel_id":     data.get("personnel_id"),
        "channel":          data.get("channel", "").strip() or None,
        "message":          data["message"].strip(),
        "logged_at":        data.get("logged_at", now_utc()),
        "is_missed_checkin": 0,
        "source":           "manual",
    }

    entry_id = append_only_insert("radio_log", record)

    try:
        from basecamp.app import socketio
        socketio.emit("radio_entry", {
            "incident_id": incident_id,
            "id":          entry_id,
            **record,
        })
    except Exception:
        pass

    return jsonify({"message": "Radio entry logged", "id": entry_id}), 201


# ---------------------------------------------------------------------------
# POST /api/radio/<incident_id>/missed
# Flag a missed check-in — called by the check-in watcher service
# or manually by IC when operator goes silent
# ---------------------------------------------------------------------------

@radio_bp.route("/<incident_id>/missed", methods=["POST"])
@require_role("IC", "ops_chief", "logistics")
def flag_missed_checkin(incident_id):
    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    personnel_id = data.get("personnel_id")
    if not personnel_id:
        return jsonify({"error": "personnel_id is required"}), 400

    person = get_record("personnel", personnel_id)
    if not person:
        return jsonify({"error": "Personnel record not found"}), 404

    ts = now_utc()
    message = (
        data.get("message") or
        f"MISSED CHECK-IN — {person['call_sign'] or person['first_name'] + ' ' + person['last_name']}"
    )

    record = {
        "incident_id":       incident_id,
        "personnel_id":      personnel_id,
        "channel":           data.get("channel"),
        "message":           message,
        "logged_at":         ts,
        "is_missed_checkin": 1,
        "source":            data.get("source", "manual"),
    }

    entry_id = append_only_insert("radio_log", record)

    log.warning(
        "MISSED CHECK-IN: %s %s on incident %s",
        person["first_name"], person["last_name"], incident_id,
    )

    # Broadcast missed check-in alert immediately
    try:
        from basecamp.app import socketio
        socketio.emit("missed_checkin", {
            "incident_id":  incident_id,
            "personnel_id": personnel_id,
            "name":         f"{person['first_name']} {person['last_name']}",
            "call_sign":    person["call_sign"],
            "phone":        person["phone"],
            "logged_at":    ts,
            "entry_id":     entry_id,
        })
    except Exception:
        pass

    return jsonify({
        "message":  "Missed check-in flagged",
        "id":       entry_id,
        "logged_at": ts,
    }), 201


# ---------------------------------------------------------------------------
# GET /api/radio/<incident_id>/summary
# Radio log stats — total entries, missed count, last activity
# For BASECAMP overview panel
# ---------------------------------------------------------------------------

@radio_bp.route("/<incident_id>/summary", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def radio_summary(incident_id):
    with local_db() as db:
        total = db.execute(
            "SELECT COUNT(*) as n FROM radio_log WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()["n"]

        missed = db.execute(
            "SELECT COUNT(*) as n FROM radio_log "
            "WHERE incident_id = ? AND is_missed_checkin = 1",
            (incident_id,),
        ).fetchone()["n"]

        last = db.execute(
            "SELECT logged_at FROM radio_log WHERE incident_id = ? "
            "ORDER BY logged_at DESC LIMIT 1",
            (incident_id,),
        ).fetchone()

    return jsonify({
        "total_entries":    total,
        "missed_checkins":  missed,
        "last_activity":    last["logged_at"] if last else None,
    })
