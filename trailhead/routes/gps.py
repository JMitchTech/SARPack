"""
SARPack — trailhead/routes/gps.py
GPS position endpoints for TRAILHEAD field operators.

Two modes:
  1. Live push  — single position, sent immediately when online
  2. Bulk sync  — array of queued positions, sent on reconnect after offline period

Both write to gps_tracks via append_only_insert.
Live push also broadcasts via BASECAMP's SocketIO if BASECAMP is running.
Bulk sync preserves original recorded_at timestamps so the track
is accurate even if sync happens hours after the positions were collected.
"""

import logging
from flask import Blueprint, jsonify, request
from core.auth import require_role, get_current_user
from core.db import append_only_insert, get_record, local_db, now_utc

log = logging.getLogger("trailhead.gps")
gps_bp = Blueprint("gps", __name__)


# ---------------------------------------------------------------------------
# POST /api/gps/position
# Single GPS fix — live push when online
# Called by TRAILHEAD every 30 seconds when connected
# ---------------------------------------------------------------------------

@gps_bp.route("/position", methods=["POST"])
@require_role("IC", "ops_chief", "logistics", "field_op")
def push_position():
    user = get_current_user()
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    required = ("incident_id", "lat", "lng")
    missing = [f for f in required if data.get(f) is None]
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    try:
        lat = float(data["lat"])
        lng = float(data["lng"])
    except (ValueError, TypeError):
        return jsonify({"error": "lat and lng must be numeric"}), 400

    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({"error": "lat/lng out of valid range"}), 400

    # Use linked personnel_id, or allow explicit override (for IC logging)
    personnel_id = data.get("personnel_id") or user.get("personnel_id")
    if not personnel_id:
        return jsonify({"error": "No personnel record linked to this account"}), 400

    record = {
        "incident_id":  data["incident_id"],
        "personnel_id": personnel_id,
        "lat":          lat,
        "lng":          lng,
        "elevation":    data.get("elevation"),
        "accuracy":     data.get("accuracy"),
        "recorded_at":  data.get("recorded_at", now_utc()),
        "source":       "trailhead",
    }

    track_id = append_only_insert("gps_tracks", record)

    # Attempt to notify BASECAMP via SocketIO — non-fatal if BASECAMP not running
    try:
        from basecamp.app import socketio
        socketio.emit("gps_update", {
            "incident_id":  data["incident_id"],
            "personnel_id": personnel_id,
            "lat":          lat,
            "lng":          lng,
            "elevation":    data.get("elevation"),
            "recorded_at":  record["recorded_at"],
        }, room=data["incident_id"])
    except Exception:
        pass

    return jsonify({"message": "Position recorded", "id": track_id}), 201


# ---------------------------------------------------------------------------
# POST /api/gps/sync
# Bulk GPS sync — offline queue flush on reconnect
# TRAILHEAD sends all queued positions in one request.
# Positions are ordered by recorded_at — oldest first.
# Max 5000 per request to prevent timeout on long offline periods.
# ---------------------------------------------------------------------------

@gps_bp.route("/sync", methods=["POST"])
@require_role("IC", "ops_chief", "logistics", "field_op")
def bulk_sync():
    user = get_current_user()
    data = request.get_json(silent=True)

    if not data or not isinstance(data, list):
        return jsonify({"error": "Request body must be a JSON array of position objects"}), 400

    if len(data) > 5000:
        return jsonify({"error": "Maximum 5000 positions per sync request"}), 400

    personnel_id = user.get("personnel_id")
    created = 0
    failed  = 0
    errors  = []

    # Sort by recorded_at to preserve chronological order
    try:
        data = sorted(data, key=lambda x: x.get("recorded_at", ""))
    except Exception:
        pass

    for fix in data:
        try:
            lat = float(fix["lat"])
            lng = float(fix["lng"])

            if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
                raise ValueError(f"lat/lng out of range: {lat},{lng}")

            pid = fix.get("personnel_id") or personnel_id
            if not pid:
                raise ValueError("No personnel_id")

            record = {
                "incident_id":  fix["incident_id"],
                "personnel_id": pid,
                "lat":          lat,
                "lng":          lng,
                "elevation":    fix.get("elevation"),
                "accuracy":     fix.get("accuracy"),
                "recorded_at":  fix.get("recorded_at", now_utc()),
                "source":       "trailhead",
            }
            append_only_insert("gps_tracks", record)
            created += 1

        except Exception as e:
            failed += 1
            errors.append({"index": data.index(fix), "error": str(e)})

    log.info(
        "GPS bulk sync: %d created, %d failed (user: %s)",
        created, failed, user.get("username"),
    )

    return jsonify({
        "created": created,
        "failed":  failed,
        "errors":  errors[:10] if errors else [],  # cap error detail
    }), 207 if failed else 201


# ---------------------------------------------------------------------------
# GET /api/gps/track/<incident_id>
# Return this operator's full GPS track for an incident.
# Used by TRAILHEAD map to render the operator's own trail.
# ---------------------------------------------------------------------------

@gps_bp.route("/track/<incident_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "field_op", "observer")
def get_my_track(incident_id):
    user = get_current_user()
    personnel_id = user.get("personnel_id")

    if not personnel_id:
        return jsonify({"error": "No personnel record linked"}), 400

    limit = int(request.args.get("limit", 500))

    with local_db() as db:
        rows = db.execute(
            """
            SELECT lat, lng, elevation, accuracy, recorded_at
            FROM gps_tracks
            WHERE incident_id = ? AND personnel_id = ?
            ORDER BY recorded_at ASC
            LIMIT ?
            """,
            (incident_id, personnel_id, limit),
        ).fetchall()

    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# GET /api/gps/last-position/<incident_id>
# Return this operator's most recent GPS fix.
# Used by TRAILHEAD status screen.
# ---------------------------------------------------------------------------

@gps_bp.route("/last-position/<incident_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "field_op", "observer")
def last_position(incident_id):
    user = get_current_user()
    personnel_id = user.get("personnel_id")

    if not personnel_id:
        return jsonify({"error": "No personnel record linked"}), 400

    with local_db() as db:
        row = db.execute(
            """
            SELECT lat, lng, elevation, accuracy, recorded_at
            FROM gps_tracks
            WHERE incident_id = ? AND personnel_id = ?
            ORDER BY recorded_at DESC LIMIT 1
            """,
            (incident_id, personnel_id),
        ).fetchone()

    if not row:
        return jsonify({"position": None, "message": "No GPS fixes recorded yet"})

    return jsonify({"position": dict(row)})
