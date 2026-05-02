"""
SARPack 2.0 — api/incidents.py
Incident management — create, update, close, LKP, status.
"""

import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, g

from core.database import get_db, row_to_dict, rows_to_list
from core.auth import require_auth, require_ic, require_logistics, audit

bp = Blueprint("incidents", __name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _incident_or_404(incident_id: str) -> dict:
    db  = get_db()
    row = db.execute(
        "SELECT * FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if not row:
        return None
    return row_to_dict(row)


def _enrich_incident(inc: dict) -> dict:
    """Add deployment count and segment count to an incident dict."""
    db = get_db()

    inc["deployed_count"] = db.execute(
        "SELECT COUNT(*) as c FROM deployments "
        "WHERE incident_id = ? AND status = 'active'",
        (inc["id"],)
    ).fetchone()["c"]

    inc["segment_count"] = db.execute(
        "SELECT COUNT(*) as c FROM search_segments WHERE incident_id = ?",
        (inc["id"],)
    ).fetchone()["c"]

    inc["cleared_count"] = db.execute(
        "SELECT COUNT(*) as c FROM search_segments "
        "WHERE incident_id = ? AND status = 'cleared'",
        (inc["id"],)
    ).fetchone()["c"]

    inc["missed_checkins"] = db.execute(
        "SELECT COUNT(*) as c FROM radio_entries "
        "WHERE incident_id = ? AND is_missed = 1",
        (inc["id"],)
    ).fetchone()["c"]

    inc["radio_count"] = db.execute(
        "SELECT COUNT(*) as c FROM radio_entries WHERE incident_id = ?",
        (inc["id"],)
    ).fetchone()["c"]

    return inc


# ── List incidents ────────────────────────────────────────────────────────────

@bp.route("/", methods=["GET"])
@require_auth
def list_incidents():
    """
    List all incidents. Supports filtering by status and type.
    Query params: status, incident_type, limit, offset
    """
    db     = get_db()
    status = request.args.get("status")
    itype  = request.args.get("incident_type")
    limit  = min(int(request.args.get("limit",  100)), 500)
    offset = int(request.args.get("offset", 0))

    query  = "SELECT * FROM incidents WHERE 1=1"
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)
    if itype:
        query += " AND incident_type = ?"
        params.append(itype)

    query += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    rows = db.execute(query, params).fetchall()
    incidents = [_enrich_incident(row_to_dict(r)) for r in rows]

    total = db.execute(
        "SELECT COUNT(*) as c FROM incidents"
    ).fetchone()["c"]

    return jsonify({
        "incidents": incidents,
        "total":     total,
        "limit":     limit,
        "offset":    offset,
    }), 200


# ── Get single incident ───────────────────────────────────────────────────────

@bp.route("/<incident_id>", methods=["GET"])
@require_auth
def get_incident(incident_id):
    """Get a single incident with full details."""
    inc = _incident_or_404(incident_id)
    if not inc:
        return jsonify({"error": "Incident not found"}), 404

    inc = _enrich_incident(inc)

    # Include active deployments
    db  = get_db()
    inc["deployments"] = rows_to_list(db.execute(
        """SELECT d.*, p.first_name, p.last_name, p.call_sign
           FROM deployments d
           JOIN personnel p ON d.personnel_id = p.id
           WHERE d.incident_id = ? AND d.status = 'active'
           ORDER BY d.checked_in_at""",
        (incident_id,)
    ).fetchall())

    # Include search segments
    inc["segments"] = rows_to_list(db.execute(
        "SELECT * FROM search_segments WHERE incident_id = ? ORDER BY segment_id",
        (incident_id,)
    ).fetchall())

    # Include map markers
    inc["markers"] = rows_to_list(db.execute(
        "SELECT * FROM map_markers WHERE incident_id = ? ORDER BY created_at",
        (incident_id,)
    ).fetchall())

    return jsonify(inc), 200


# ── Create incident ───────────────────────────────────────────────────────────

@bp.route("/", methods=["POST"])
@require_ic
def create_incident():
    """Create a new incident."""
    data = request.get_json(silent=True) or {}

    required = ["incident_name", "incident_number", "incident_type"]
    missing  = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    valid_types = {"sar","usar","disaster_relief","training","standby","medical"}
    if data["incident_type"] not in valid_types:
        return jsonify({"error": f"Invalid incident_type. Must be one of: {', '.join(valid_types)}"}), 400

    db = get_db()

    # Check incident number uniqueness
    existing = db.execute(
        "SELECT id FROM incidents WHERE incident_number = ?",
        (data["incident_number"],)
    ).fetchone()
    if existing:
        return jsonify({"error": "Incident number already exists"}), 409

    incident_id = str(uuid.uuid4())
    now         = datetime.now(timezone.utc).isoformat()

    db.execute(
        """INSERT INTO incidents
           (id, incident_number, incident_name, incident_type,
            status, county, state, latitude, longitude,
            description, ic_name, started_at, created_by,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            incident_id,
            data["incident_number"].strip(),
            data["incident_name"].strip(),
            data["incident_type"],
            data.get("county"),
            data.get("state", "PA"),
            data.get("latitude"),
            data.get("longitude"),
            data.get("description"),
            data.get("ic_name"),
            data.get("started_at", now),
            g.user_id,
            now, now,
        )
    )
    db.commit()

    audit("create_incident", target_type="incident", target_id=incident_id,
          detail=data["incident_number"])

    inc = _incident_or_404(incident_id)
    return jsonify(inc), 201


# ── Update incident ───────────────────────────────────────────────────────────

@bp.route("/<incident_id>", methods=["PATCH"])
@require_ic
def update_incident(incident_id):
    """Update incident fields."""
    inc = _incident_or_404(incident_id)
    if not inc:
        return jsonify({"error": "Incident not found"}), 404

    data    = request.get_json(silent=True) or {}
    db      = get_db()
    updates = []
    params  = []

    updatable = [
        "incident_name", "incident_type", "status", "county", "state",
        "latitude", "longitude", "description", "ic_name",
        "started_at", "closed_at",
    ]

    for field in updatable:
        if field in data:
            updates.append(f"{field} = ?")
            params.append(data[field])

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    updates.append("updated_at = datetime('now')")
    params.append(incident_id)

    db.execute(
        f"UPDATE incidents SET {', '.join(updates)} WHERE id = ?", params
    )
    db.commit()

    audit("update_incident", target_type="incident", target_id=incident_id)
    return jsonify(_enrich_incident(_incident_or_404(incident_id))), 200


# ── Close incident ────────────────────────────────────────────────────────────

@bp.route("/<incident_id>/close", methods=["POST"])
@require_ic
def close_incident(incident_id):
    """Close an incident."""
    inc = _incident_or_404(incident_id)
    if not inc:
        return jsonify({"error": "Incident not found"}), 404
    if inc["status"] == "closed":
        return jsonify({"error": "Incident is already closed"}), 400

    db  = get_db()
    now = datetime.now(timezone.utc).isoformat()

    db.execute(
        """UPDATE incidents
           SET status = 'closed', closed_at = ?, updated_at = ?
           WHERE id = ?""",
        (now, now, incident_id)
    )

    # Check out all still-active deployments
    db.execute(
        """UPDATE deployments
           SET status = 'checked_out', checked_out_at = ?
           WHERE incident_id = ? AND status = 'active'""",
        (now, incident_id)
    )

    db.commit()
    audit("close_incident", target_type="incident", target_id=incident_id)
    return jsonify({"message": "Incident closed"}), 200


# ── LKP (Last Known Position) ─────────────────────────────────────────────────

@bp.route("/<incident_id>/lkp", methods=["POST"])
@require_ic
def set_lkp(incident_id):
    """
    Set or update the Last Known Position for a subject.
    Broadcasts to all connected windows via SocketIO.
    """
    inc = _incident_or_404(incident_id)
    if not inc:
        return jsonify({"error": "Incident not found"}), 404

    data = request.get_json(silent=True) or {}
    lat  = data.get("latitude")
    lng  = data.get("longitude")

    if lat is None or lng is None:
        return jsonify({"error": "latitude and longitude are required"}), 400

    db = get_db()
    db.execute(
        """UPDATE incidents
           SET lkp_lat = ?, lkp_lng = ?, lkp_notes = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (lat, lng, data.get("notes"), incident_id)
    )
    db.commit()

    # Broadcast to all connected windows
    try:
        from app import socketio
        socketio.emit("lkp_updated", {
            "incident_id": incident_id,
            "lkp_lat":     lat,
            "lkp_lng":     lng,
            "lkp_notes":   data.get("notes"),
        }, room=f"incident_{incident_id}")
    except Exception:
        pass

    audit("set_lkp", target_type="incident", target_id=incident_id,
          detail=f"{lat},{lng}")
    return jsonify({"message": "LKP updated", "lkp_lat": lat, "lkp_lng": lng}), 200


@bp.route("/<incident_id>/lkp", methods=["DELETE"])
@require_ic
def clear_lkp(incident_id):
    """Clear the LKP for an incident."""
    db = get_db()
    db.execute(
        """UPDATE incidents
           SET lkp_lat = NULL, lkp_lng = NULL, lkp_notes = NULL,
               updated_at = datetime('now')
           WHERE id = ?""",
        (incident_id,)
    )
    db.commit()

    try:
        from app import socketio
        socketio.emit("lkp_cleared", {
            "incident_id": incident_id,
        }, room=f"incident_{incident_id}")
    except Exception:
        pass

    audit("clear_lkp", target_type="incident", target_id=incident_id)
    return jsonify({"message": "LKP cleared"}), 200


# ── Map markers ───────────────────────────────────────────────────────────────

@bp.route("/<incident_id>/markers", methods=["GET"])
@require_auth
def list_markers(incident_id):
    """List all map markers for an incident."""
    db   = get_db()
    rows = db.execute(
        "SELECT * FROM map_markers WHERE incident_id = ? ORDER BY created_at",
        (incident_id,)
    ).fetchall()
    return jsonify(rows_to_list(rows)), 200


@bp.route("/<incident_id>/markers", methods=["POST"])
@require_auth
def add_marker(incident_id):
    """
    Add a map marker (LZ pin, DZ target, POI, hazard, staging area).
    TRAILHEAD uses this to drop LZ pins that appear on BASECAMP map.
    BASECAMP uses this for DZ targeting — broadcasts to specific TRAILHEAD device.
    """
    inc = _incident_or_404(incident_id)
    if not inc:
        return jsonify({"error": "Incident not found"}), 404

    data         = request.get_json(silent=True) or {}
    marker_type  = data.get("marker_type", "poi")
    lat          = data.get("latitude")
    lng          = data.get("longitude")

    valid_types = {"lz", "dz", "poi", "hazard", "camp", "staging"}
    if marker_type not in valid_types:
        return jsonify({"error": f"Invalid marker_type. Must be one of: {', '.join(valid_types)}"}), 400

    if lat is None or lng is None:
        return jsonify({"error": "latitude and longitude are required"}), 400

    marker_id = str(uuid.uuid4())
    db        = get_db()

    db.execute(
        """INSERT INTO map_markers
           (id, incident_id, marker_type, label, latitude, longitude,
            notes, created_by, target_device, broadcast_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
        (
            marker_id, incident_id, marker_type,
            data.get("label"), lat, lng,
            data.get("notes"), g.user_id,
            data.get("target_device"),
        )
    )
    db.commit()

    marker = row_to_dict(db.execute(
        "SELECT * FROM map_markers WHERE id = ?", (marker_id,)
    ).fetchone())

    # Broadcast to all windows in the incident
    try:
        from app import socketio
        socketio.emit("marker_added", {
            "incident_id": incident_id,
            "marker":      marker,
        }, room=f"incident_{incident_id}")

        # DZ targeting — also emit directly to target device room
        if marker_type == "dz" and data.get("target_device"):
            socketio.emit("dz_target", {
                "incident_id":   incident_id,
                "marker":        marker,
            }, room=f"operator_{data['target_device']}")
    except Exception:
        pass

    audit("add_marker", target_type="incident", target_id=incident_id,
          detail=f"{marker_type} at {lat},{lng}")
    return jsonify(marker), 201


@bp.route("/<incident_id>/markers/<marker_id>", methods=["DELETE"])
@require_auth
def remove_marker(incident_id, marker_id):
    """Remove a map marker."""
    db     = get_db()
    marker = db.execute(
        "SELECT id FROM map_markers WHERE id = ? AND incident_id = ?",
        (marker_id, incident_id)
    ).fetchone()

    if not marker:
        return jsonify({"error": "Marker not found"}), 404

    db.execute("DELETE FROM map_markers WHERE id = ?", (marker_id,))
    db.commit()

    try:
        from app import socketio
        socketio.emit("marker_removed", {
            "incident_id": incident_id,
            "marker_id":   marker_id,
        }, room=f"incident_{incident_id}")
    except Exception:
        pass

    return jsonify({"message": "Marker removed"}), 200


# ── SOS alerts ────────────────────────────────────────────────────────────────

@bp.route("/<incident_id>/sos", methods=["POST"])
@require_auth
def trigger_sos(incident_id):
    """
    Trigger an SOS alert for an operator in distress.
    Called by TRAILHEAD when operator holds the SOS button.
    Broadcasts a flashing banner to ALL connected windows.
    """
    inc = _incident_or_404(incident_id)
    if not inc:
        return jsonify({"error": "Incident not found"}), 404

    data = request.get_json(silent=True) or {}
    sos_id = str(uuid.uuid4())
    db   = get_db()

    db.execute(
        """INSERT INTO sos_alerts
           (id, incident_id, personnel_id, latitude, longitude,
            message, created_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            sos_id, incident_id,
            data.get("personnel_id"),
            data.get("latitude"),
            data.get("longitude"),
            data.get("message", "OPERATOR IN DISTRESS"),
        )
    )
    db.commit()

    # Get personnel info for the banner
    personnel = None
    if data.get("personnel_id"):
        personnel = row_to_dict(db.execute(
            "SELECT first_name, last_name, call_sign FROM personnel WHERE id = ?",
            (data["personnel_id"],)
        ).fetchone())

    sos_payload = {
        "sos_id":      sos_id,
        "incident_id": incident_id,
        "personnel":   personnel,
        "latitude":    data.get("latitude"),
        "longitude":   data.get("longitude"),
        "message":     data.get("message", "OPERATOR IN DISTRESS"),
        "created_at":  datetime.now(timezone.utc).isoformat(),
    }

    # Broadcast to ALL windows — portal + all popouts simultaneously
    try:
        from app import socketio
        socketio.emit("sos_alert", sos_payload,
                      room=f"incident_{incident_id}")
    except Exception:
        pass

    audit("sos_triggered", target_type="incident", target_id=incident_id,
          detail=data.get("personnel_id"))
    return jsonify(sos_payload), 201


@bp.route("/<incident_id>/sos/<sos_id>/acknowledge", methods=["POST"])
@require_auth
def acknowledge_sos(incident_id, sos_id):
    """IC acknowledges an SOS — clears the banner on all windows."""
    db = get_db()
    db.execute(
        """UPDATE sos_alerts
           SET acknowledged_by = ?, acknowledged_at = datetime('now')
           WHERE id = ? AND incident_id = ?""",
        (g.user_id, sos_id, incident_id)
    )
    db.commit()

    try:
        from app import socketio
        socketio.emit("sos_acknowledged", {
            "incident_id": incident_id,
            "sos_id":      sos_id,
        }, room=f"incident_{incident_id}")
    except Exception:
        pass

    audit("sos_acknowledged", target_type="incident", target_id=incident_id)
    return jsonify({"message": "SOS acknowledged"}), 200


@bp.route("/<incident_id>/sos", methods=["GET"])
@require_auth
def list_sos(incident_id):
    """List all SOS alerts for an incident."""
    db   = get_db()
    rows = db.execute(
        """SELECT s.*, p.first_name, p.last_name, p.call_sign
           FROM sos_alerts s
           LEFT JOIN personnel p ON s.personnel_id = p.id
           WHERE s.incident_id = ?
           ORDER BY s.created_at DESC""",
        (incident_id,)
    ).fetchall()
    return jsonify(rows_to_list(rows)), 200


# ── Stats summary (for BASECAMP dashboard) ────────────────────────────────────

@bp.route("/stats/summary", methods=["GET"])
@require_auth
def stats_summary():
    """Quick stats for the portal dashboard."""
    db = get_db()

    return jsonify({
        "total_incidents":  db.execute("SELECT COUNT(*) as c FROM incidents").fetchone()["c"],
        "active_incidents": db.execute("SELECT COUNT(*) as c FROM incidents WHERE status='active'").fetchone()["c"],
        "total_personnel":  db.execute("SELECT COUNT(*) as c FROM personnel WHERE is_active=1").fetchone()["c"],
        "active_deployments": db.execute("SELECT COUNT(*) as c FROM deployments WHERE status='active'").fetchone()["c"],
        "open_sos":         db.execute("SELECT COUNT(*) as c FROM sos_alerts WHERE acknowledged_at IS NULL").fetchone()["c"],
    }), 200