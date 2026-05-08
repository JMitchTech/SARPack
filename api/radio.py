"""
SARPack 2.0 — api/radio.py
Radio log entries, missed check-ins, and GPS position tracking.
"""

import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, g

from core.database import get_db, row_to_dict, rows_to_list
from core.auth import require_auth, require_ic, require_logistics, audit

bp = Blueprint("radio", __name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _broadcast_radio_entry(incident_id: str, entry: dict):
    """Broadcast a new radio entry to all connected windows."""
    try:
        from app import socketio
        socketio.emit("radio_entry", {
            "incident_id": incident_id,
            "entry":       entry,
        }, room=f"incident_{incident_id}")
    except Exception:
        pass


# ── Radio log ─────────────────────────────────────────────────────────────────

@bp.route("/<incident_id>", methods=["GET"])
@require_auth
def list_entries(incident_id):
    """
    List radio log entries for an incident.
    Query params: limit, offset, channel, is_missed, source
    """
    db        = get_db()
    limit     = min(int(request.args.get("limit",  100)), 500)
    offset    = int(request.args.get("offset", 0))
    channel   = request.args.get("channel")
    is_missed = request.args.get("is_missed")
    source    = request.args.get("source")

    query  = """SELECT r.*, p.first_name, p.last_name, p.call_sign
                FROM radio_entries r
                LEFT JOIN personnel p ON r.personnel_id = p.id
                WHERE r.incident_id = ?"""
    params = [incident_id]

    if channel:
        query += " AND r.channel = ?"
        params.append(channel)
    if is_missed in ("0", "1"):
        query += " AND r.is_missed = ?"
        params.append(int(is_missed))
    if source:
        query += " AND r.source = ?"
        params.append(source)

    query += " ORDER BY r.logged_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    rows = db.execute(query, params).fetchall()

    total = db.execute(
        "SELECT COUNT(*) as c FROM radio_entries WHERE incident_id = ?",
        (incident_id,)
    ).fetchone()["c"]

    missed_count = db.execute(
        "SELECT COUNT(*) as c FROM radio_entries WHERE incident_id = ? AND is_missed = 1",
        (incident_id,)
    ).fetchone()["c"]

    return jsonify({
        "entries":      rows_to_list(rows),
        "total":        total,
        "missed_count": missed_count,
        "limit":        limit,
        "offset":       offset,
    }), 200


@bp.route("/<incident_id>", methods=["POST"])
@require_auth
def log_entry(incident_id):
    """
    Log a radio entry.
    Called by BASECAMP operators and TRAILHEAD field devices.
    Broadcasts to all connected windows immediately.
    """
    db = get_db()

    incident = db.execute(
        "SELECT id FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    data    = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"error": "message is required"}), 400

    entry_id = str(uuid.uuid4())
    logged_at = data.get("logged_at") or datetime.now(timezone.utc).isoformat()

    db.execute(
        """INSERT INTO radio_entries
           (id, incident_id, personnel_id, message, channel,
            is_missed, source, logged_at)
           VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
        (
            entry_id, incident_id,
            data.get("personnel_id"),
            message,
            data.get("channel"),
            data.get("source", "basecamp"),
            logged_at,
        )
    )
    db.commit()

    entry = row_to_dict(db.execute(
        """SELECT r.*, p.first_name, p.last_name, p.call_sign
           FROM radio_entries r
           LEFT JOIN personnel p ON r.personnel_id = p.id
           WHERE r.id = ?""",
        (entry_id,)
    ).fetchone())

    _broadcast_radio_entry(incident_id, entry)
    return jsonify(entry), 201


@bp.route("/<incident_id>/bulk", methods=["POST"])
@require_auth
def log_bulk(incident_id):
    """
    Bulk log radio entries.
    Used by simulation scripts and offline sync from TRAILHEAD.
    """
    db = get_db()

    incident = db.execute(
        "SELECT id FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    data    = request.get_json(silent=True) or {}
    entries = data.get("entries", [])

    if not entries or not isinstance(entries, list):
        return jsonify({"error": "entries array is required"}), 400

    created = []
    for e in entries:
        message = (e.get("message") or "").strip()
        if not message:
            continue

        entry_id  = str(uuid.uuid4())
        logged_at = e.get("logged_at") or datetime.now(timezone.utc).isoformat()

        db.execute(
            """INSERT INTO radio_entries
               (id, incident_id, personnel_id, message, channel,
                is_missed, source, logged_at)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
            (
                entry_id, incident_id,
                e.get("personnel_id"),
                message,
                e.get("channel"),
                e.get("source", "basecamp"),
                logged_at,
            )
        )
        created.append(entry_id)

    db.commit()
    return jsonify({"created": len(created)}), 201


# ── Missed check-ins ──────────────────────────────────────────────────────────

@bp.route("/<incident_id>/missed", methods=["POST"])
@require_auth
def flag_missed(incident_id):
    """
    Flag a missed check-in for an operator.
    Creates a radio entry marked as missed and broadcasts
    a missed_checkin event to all connected windows.
    BASECAMP displays these prominently in the radio log.
    """
    db = get_db()

    incident = db.execute(
        "SELECT id FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    data         = request.get_json(silent=True) or {}
    personnel_id = data.get("personnel_id")

    if not personnel_id:
        return jsonify({"error": "personnel_id is required"}), 400

    person = db.execute(
        "SELECT first_name, last_name, call_sign FROM personnel WHERE id = ?",
        (personnel_id,)
    ).fetchone()
    if not person:
        return jsonify({"error": "Personnel not found"}), 404

    call_sign = person["call_sign"] or f"{person['first_name']} {person['last_name']}"
    message   = data.get("message") or f"MISSED CHECK-IN — {call_sign}"

    entry_id = str(uuid.uuid4())
    logged_at = datetime.now(timezone.utc).isoformat()

    db.execute(
        """INSERT INTO radio_entries
           (id, incident_id, personnel_id, message, channel,
            is_missed, source, logged_at)
           VALUES (?, ?, ?, ?, ?, 1, 'basecamp', ?)""",
        (
            entry_id, incident_id, personnel_id,
            message,
            data.get("channel", "OPS"),
            logged_at,
        )
    )
    db.commit()

    entry = row_to_dict(db.execute(
        """SELECT r.*, p.first_name, p.last_name, p.call_sign
           FROM radio_entries r
           LEFT JOIN personnel p ON r.personnel_id = p.id
           WHERE r.id = ?""",
        (entry_id,)
    ).fetchone())

    # Broadcast missed check-in alert to all windows
    try:
        from app import socketio
        socketio.emit("missed_checkin", {
            "incident_id": incident_id,
            "entry":       entry,
            "personnel":   row_to_dict(person),
        }, room=f"incident_{incident_id}")
    except Exception:
        pass

    audit("missed_checkin", target_type="radio", target_id=entry_id,
          detail=call_sign)
    return jsonify(entry), 201


@bp.route("/<incident_id>/missed", methods=["GET"])
@require_auth
def list_missed(incident_id):
    """List all missed check-ins for an incident."""
    db   = get_db()
    rows = db.execute(
        """SELECT r.*, p.first_name, p.last_name, p.call_sign
           FROM radio_entries r
           LEFT JOIN personnel p ON r.personnel_id = p.id
           WHERE r.incident_id = ? AND r.is_missed = 1
           ORDER BY r.logged_at DESC""",
        (incident_id,)
    ).fetchall()
    return jsonify(rows_to_list(rows)), 200


# ── Radio log summary ─────────────────────────────────────────────────────────

@bp.route("/<incident_id>/summary", methods=["GET"])
@require_auth
def radio_summary(incident_id):
    """
    Summary stats for the radio log panel.
    Returns total entries, missed count, entries by channel,
    and most recent entries per operator.
    """
    db = get_db()

    total = db.execute(
        "SELECT COUNT(*) as c FROM radio_entries WHERE incident_id = ?",
        (incident_id,)
    ).fetchone()["c"]

    missed = db.execute(
        "SELECT COUNT(*) as c FROM radio_entries WHERE incident_id = ? AND is_missed = 1",
        (incident_id,)
    ).fetchone()["c"]

    by_channel = rows_to_list(db.execute(
        """SELECT channel, COUNT(*) as count
           FROM radio_entries WHERE incident_id = ? AND channel IS NOT NULL
           GROUP BY channel ORDER BY count DESC""",
        (incident_id,)
    ).fetchall())

    by_source = rows_to_list(db.execute(
        """SELECT source, COUNT(*) as count
           FROM radio_entries WHERE incident_id = ?
           GROUP BY source""",
        (incident_id,)
    ).fetchall())

    last_contact = rows_to_list(db.execute(
        """SELECT p.first_name, p.last_name, p.call_sign,
                  MAX(r.logged_at) as last_contact
           FROM radio_entries r
           JOIN personnel p ON r.personnel_id = p.id
           WHERE r.incident_id = ? AND r.is_missed = 0
           GROUP BY r.personnel_id
           ORDER BY last_contact DESC""",
        (incident_id,)
    ).fetchall())

    return jsonify({
        "total_entries":  total,
        "missed_checkins": missed,
        "by_channel":     by_channel,
        "by_source":      by_source,
        "last_contact":   last_contact,
    }), 200


# ── GPS positions ─────────────────────────────────────────────────────────────

@bp.route("/gps/<incident_id>", methods=["POST"])
@require_auth
def log_position(incident_id):
    """
    Log a GPS position for an operator.
    Called frequently by TRAILHEAD while deployed.
    Broadcasts position update to all connected windows.
    """
    data = request.get_json(silent=True) or {}
    lat  = data.get("lat") or data.get("latitude")
    lng  = data.get("lng") or data.get("longitude")

    if lat is None or lng is None:
        return jsonify({"error": "lat and lng are required"}), 400

    # Resolve personnel_id from user account
    db   = get_db()
    user = db.execute(
        "SELECT personnel_id FROM users WHERE id = ?", (g.user_id,)
    ).fetchone()
    personnel_id = (user["personnel_id"] if user else None) or data.get("personnel_id")

    pos_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO gps_positions
           (id, incident_id, personnel_id, latitude, longitude,
            altitude, accuracy, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            pos_id, incident_id, personnel_id,
            lat, lng,
            data.get("elevation") or data.get("altitude"),
            data.get("accuracy"),
            data.get("recorded_at") or datetime.now(timezone.utc).isoformat(),
        )
    )
    db.commit()

    # Broadcast to BASECAMP map
    try:
        from app import socketio
        socketio.emit("position_update", {
            "incident_id":  incident_id,
            "personnel_id": personnel_id,
            "latitude":     lat,
            "longitude":    lng,
            "altitude":     data.get("elevation") or data.get("altitude"),
            "accuracy":     data.get("accuracy"),
            "recorded_at":  data.get("recorded_at"),
        }, room=f"incident_{incident_id}")
    except Exception:
        pass

    return jsonify({"id": pos_id}), 201


@bp.route("/gps/<incident_id>/sync", methods=["POST"])
@require_auth
def sync_positions(incident_id):
    """
    Bulk sync GPS positions from TRAILHEAD after coming back online.
    Accepts an array of position objects.
    """
    data      = request.get_json(silent=True) or {}
    positions = data if isinstance(data, list) else data.get("positions", [])

    if not positions:
        return jsonify({"error": "positions array is required"}), 400

    db   = get_db()
    user = db.execute(
        "SELECT personnel_id FROM users WHERE id = ?", (g.user_id,)
    ).fetchone()
    personnel_id = user["personnel_id"] if user else None

    created = 0
    for pos in positions:
        lat = pos.get("lat") or pos.get("latitude")
        lng = pos.get("lng") or pos.get("longitude")
        if lat is None or lng is None:
            continue

        db.execute(
            """INSERT INTO gps_positions
               (id, incident_id, personnel_id, latitude, longitude,
                altitude, accuracy, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                pos.get("incident_id") or incident_id,
                personnel_id or pos.get("personnel_id"),
                lat, lng,
                pos.get("elevation") or pos.get("altitude"),
                pos.get("accuracy"),
                pos.get("recorded_at") or datetime.now(timezone.utc).isoformat(),
            )
        )
        created += 1

    db.commit()
    return jsonify({"synced": created}), 201


@bp.route("/gps/<incident_id>/positions", methods=["GET"])
@require_auth
def get_positions(incident_id):
    """
    Get latest GPS position for each operator in an incident.
    Used by BASECAMP map to render operator dots.
    """
    db   = get_db()
    rows = db.execute(
        """SELECT g.personnel_id, g.latitude, g.longitude,
                  g.altitude, g.accuracy, g.recorded_at,
                  p.first_name, p.last_name, p.call_sign
           FROM gps_positions g
           JOIN personnel p ON g.personnel_id = p.id
           WHERE g.incident_id = ?
             AND g.recorded_at = (
               SELECT MAX(g2.recorded_at)
               FROM gps_positions g2
               WHERE g2.personnel_id = g.personnel_id
                 AND g2.incident_id = g.incident_id
             )
           ORDER BY p.call_sign""",
        (incident_id,)
    ).fetchall()
    return jsonify(rows_to_list(rows)), 200


@bp.route("/gps/<incident_id>/track/<personnel_id>", methods=["GET"])
@require_auth
def get_track(incident_id, personnel_id):
    """
    Get full GPS track for a specific operator.
    Used by BASECAMP map to draw movement trails.
    Query params: limit (default 500)
    """
    db    = get_db()
    limit = min(int(request.args.get("limit", 500)), 2000)

    rows = db.execute(
        """SELECT latitude, longitude, altitude, accuracy, recorded_at
           FROM gps_positions
           WHERE incident_id = ? AND personnel_id = ?
           ORDER BY recorded_at ASC
           LIMIT ?""",
        (incident_id, personnel_id, limit)
    ).fetchall()

    return jsonify({
        "personnel_id": personnel_id,
        "incident_id":  incident_id,
        "points":       rows_to_list(rows),
        "count":        len(rows),
    }), 200


# ── Channel plan ──────────────────────────────────────────────────────────────

@bp.route("/<incident_id>/channel-plan", methods=["GET"])
@require_auth
def channel_plan(incident_id):
    """
    Generate a radio channel plan for an incident.
    Pulls all deployed operators' radio registries and
    produces a compatibility summary — the foundation for
    cross-org radio interoperability planning.
    """
    db = get_db()

    # Get all active deployments with radio info
    rows = db.execute(
        """SELECT p.first_name, p.last_name, p.call_sign,
                  d.role, d.division, d.team,
                  rr.radio_make, rr.radio_model, rr.radio_type,
                  rr.programmed_channels
           FROM deployments d
           JOIN personnel p ON d.personnel_id = p.id
           LEFT JOIN radio_registry rr ON rr.personnel_id = d.personnel_id
           WHERE d.incident_id = ? AND d.status = 'active'
           ORDER BY d.division, d.team, p.call_sign""",
        (incident_id,)
    ).fetchall()

    operators   = rows_to_list(rows)
    radio_types = {}
    import json

    for op in operators:
        rtype = op.get("radio_type") or "unknown"
        if rtype not in radio_types:
            radio_types[rtype] = []
        radio_types[rtype].append(op.get("call_sign"))

        # Parse programmed channels
        if op.get("programmed_channels"):
            try:
                op["programmed_channels"] = json.loads(op["programmed_channels"])
            except Exception:
                op["programmed_channels"] = []

    # Standard ICS channel assignments
    standard_channels = [
        {"channel": "CMD-1",  "function": "Command",          "notes": "IC to Section Chiefs"},
        {"channel": "OPS-1",  "function": "Operations",       "notes": "Primary tactical"},
        {"channel": "OPS-2",  "function": "Operations Alt",   "notes": "Secondary tactical / overflow"},
        {"channel": "MED-1",  "function": "Medical",          "notes": "Medical coordination"},
        {"channel": "LOG-1",  "function": "Logistics",        "notes": "Resource requests"},
        {"channel": "EMRG",   "function": "Emergency",        "notes": "MAYDAY / SOS only"},
    ]

    return jsonify({
        "incident_id":       incident_id,
        "operators":         operators,
        "radio_types":       radio_types,
        "standard_channels": standard_channels,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "interop_note": (
            "Full digital interoperability requires compatible radio hardware. "
            "This plan covers channel assignments for analog and P25 systems. "
            "Meshtastic nodes connect via RELAY module."
        ),
    }), 200