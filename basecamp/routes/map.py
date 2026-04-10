"""
SARPack — basecamp/routes/map.py
GPS position tracking and search segment management.
GPS tracks are append-only — never updated, just inserted.
Search segments are versioned — status and assignments change over time.
Feeds the Leaflet.js map on the BASECAMP frontend.
"""

import json
import logging
from flask import Blueprint, jsonify, request
from core.auth import require_role, require_ic
from core.db import (
    versioned_insert,
    versioned_update,
    append_only_insert,
    get_record,
    get_recent_gps,
    local_db,
    now_utc,
    VersionConflictError,
)

log = logging.getLogger("basecamp.map")
map_bp = Blueprint("map", __name__)

SEGMENT_STATUSES = ("unassigned", "assigned", "cleared", "suspended")


# ---------------------------------------------------------------------------
# GPS TRACKS
# ---------------------------------------------------------------------------

# GET /api/map/<incident_id>/positions
# Latest GPS fix per operator — primary map update query
# Called by BASECAMP every 10s and on gps_update SocketIO event

@map_bp.route("/<incident_id>/positions", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def current_positions(incident_id):
    if not get_record("incidents", incident_id):
        return jsonify({"error": "Incident not found"}), 404
    positions = get_recent_gps(incident_id)
    return jsonify(positions)


# GET /api/map/<incident_id>/track/<personnel_id>
# Full GPS track for one operator — renders trail on map

@map_bp.route("/<incident_id>/track/<personnel_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def operator_track(incident_id, personnel_id):
    limit = int(request.args.get("limit", 500))

    with local_db() as db:
        rows = db.execute(
            """
            SELECT lat, lng, elevation, accuracy, recorded_at, source
            FROM gps_tracks
            WHERE incident_id = ? AND personnel_id = ?
            ORDER BY recorded_at ASC
            LIMIT ?
            """,
            (incident_id, personnel_id, limit),
        ).fetchall()

    return jsonify([dict(r) for r in rows])


# POST /api/map/<incident_id>/position
# Ingest a GPS fix from TRAILHEAD or RELAY
# Append-only — no version needed

@map_bp.route("/<incident_id>/position", methods=["POST"])
@require_role("IC", "ops_chief", "logistics", "field_op")
def post_position(incident_id):
    if not get_record("incidents", incident_id):
        return jsonify({"error": "Incident not found"}), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    required = ("personnel_id", "lat", "lng")
    missing = [f for f in required if data.get(f) is None]
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    try:
        lat = float(data["lat"])
        lng = float(data["lng"])
    except (ValueError, TypeError):
        return jsonify({"error": "lat and lng must be numeric"}), 400

    # Rough bounds check — Pennsylvania + surrounding region
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({"error": "lat/lng out of valid range"}), 400

    record = {
        "incident_id":  incident_id,
        "personnel_id": data["personnel_id"],
        "lat":          lat,
        "lng":          lng,
        "elevation":    data.get("elevation"),
        "accuracy":     data.get("accuracy"),
        "recorded_at":  data.get("recorded_at", now_utc()),
        "source":       data.get("source", "trailhead"),
    }

    track_id = append_only_insert("gps_tracks", record)

    # Broadcast updated position to all BASECAMP clients
    try:
        from basecamp.app import socketio
        socketio.emit("gps_update", {
            "incident_id":  incident_id,
            "personnel_id": data["personnel_id"],
            "lat":          lat,
            "lng":          lng,
            "elevation":    data.get("elevation"),
            "recorded_at":  record["recorded_at"],
        })
    except Exception:
        pass

    return jsonify({"message": "Position recorded", "id": track_id}), 201


# POST /api/map/<incident_id>/positions/bulk
# Bulk GPS ingest — TRAILHEAD syncing offline-collected tracks on reconnect

@map_bp.route("/<incident_id>/positions/bulk", methods=["POST"])
@require_role("IC", "ops_chief", "logistics", "field_op")
def bulk_positions(incident_id):
    if not get_record("incidents", incident_id):
        return jsonify({"error": "Incident not found"}), 404

    data = request.get_json(silent=True)
    if not data or not isinstance(data, list):
        return jsonify({"error": "Request body must be a JSON array of position objects"}), 400

    if len(data) > 5000:
        return jsonify({"error": "Maximum 5000 positions per bulk request"}), 400

    created = 0
    failed  = 0

    for fix in data:
        try:
            lat = float(fix["lat"])
            lng = float(fix["lng"])
            record = {
                "incident_id":  incident_id,
                "personnel_id": fix["personnel_id"],
                "lat":          lat,
                "lng":          lng,
                "elevation":    fix.get("elevation"),
                "accuracy":     fix.get("accuracy"),
                "recorded_at":  fix.get("recorded_at", now_utc()),
                "source":       fix.get("source", "trailhead"),
            }
            append_only_insert("gps_tracks", record)
            created += 1
        except Exception:
            failed += 1

    log.info("Bulk GPS ingest: %d created, %d failed", created, failed)
    return jsonify({"created": created, "failed": failed}), 207 if failed else 201


# ---------------------------------------------------------------------------
# SEARCH SEGMENTS
# ---------------------------------------------------------------------------

# GET /api/map/<incident_id>/segments
# All search segments for an incident

@map_bp.route("/<incident_id>/segments", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def list_segments(incident_id):
    if not get_record("incidents", incident_id):
        return jsonify({"error": "Incident not found"}), 404

    status = request.args.get("status")
    query  = "SELECT * FROM search_segments WHERE incident_id = ?"
    params = [incident_id]

    if status:
        if status not in SEGMENT_STATUSES:
            return jsonify({
                "error": f"Invalid status '{status}'",
                "valid": list(SEGMENT_STATUSES),
            }), 400
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY segment_id"

    with local_db() as db:
        rows = db.execute(query, params).fetchall()

    return jsonify([dict(r) for r in rows])


# POST /api/map/<incident_id>/segments
# Create a new search segment

@map_bp.route("/<incident_id>/segments", methods=["POST"])
@require_role("IC", "ops_chief")
def create_segment(incident_id):
    if not get_record("incidents", incident_id):
        return jsonify({"error": "Incident not found"}), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    if not data.get("segment_id", "").strip():
        return jsonify({"error": "segment_id is required (e.g. 'A1', 'B3')"}), 400

    # Validate boundary_coords if provided — must be list of [lat, lng] pairs
    boundary = data.get("boundary_coords")
    if boundary:
        try:
            if isinstance(boundary, str):
                boundary = json.loads(boundary)
            assert isinstance(boundary, list)
            assert all(len(pt) == 2 for pt in boundary)
            boundary = json.dumps(boundary)
        except Exception:
            return jsonify({
                "error": "boundary_coords must be a JSON array of [lat, lng] pairs"
            }), 400

    # Check for duplicate segment_id on this incident
    with local_db() as db:
        existing = db.execute(
            "SELECT id FROM search_segments WHERE incident_id = ? AND segment_id = ?",
            (incident_id, data["segment_id"].strip().upper()),
        ).fetchone()

    if existing:
        return jsonify({
            "error": f"Segment '{data['segment_id']}' already exists on this incident",
            "existing_id": existing["id"],
        }), 409

    record = {
        "incident_id":              incident_id,
        "segment_id":               data["segment_id"].strip().upper(),
        "assigned_team":            data.get("assigned_team", "").strip() or None,
        "status":                   "unassigned",
        "boundary_coords":          boundary,
        "probability_of_detection": float(data.get("probability_of_detection", 0.0)),
    }

    segment_id = versioned_insert("search_segments", record)

    try:
        from basecamp.app import socketio
        socketio.emit("segment_created", {
            "incident_id": incident_id,
            "segment_id":  record["segment_id"],
            "id":          segment_id,
        })
    except Exception:
        pass

    log.info("Created segment %s for incident %s", record["segment_id"], incident_id)
    return jsonify({"message": "Segment created", "id": segment_id}), 201


# PATCH /api/map/<incident_id>/segments/<id>
# Update segment — status, team assignment, POD

@map_bp.route("/<incident_id>/segments/<segment_db_id>", methods=["PATCH"])
@require_role("IC", "ops_chief")
def update_segment(incident_id, segment_db_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    version = data.get("version")
    if version is None:
        return jsonify({"error": "version is required for updates"}), 400

    segment = get_record("search_segments", segment_db_id)
    if not segment:
        return jsonify({"error": "Segment not found"}), 404

    if segment["incident_id"] != incident_id:
        return jsonify({"error": "Segment does not belong to this incident"}), 400

    allowed = ("assigned_team", "status", "boundary_coords",
               "probability_of_detection", "assigned_at", "cleared_at")
    fields = {k: v for k, v in data.items() if k in allowed}

    if "status" in fields and fields["status"] not in SEGMENT_STATUSES:
        return jsonify({
            "error": f"Invalid status",
            "valid": list(SEGMENT_STATUSES),
        }), 400

    # Auto-set timestamps based on status transitions
    ts = now_utc()
    if fields.get("status") == "assigned" and not fields.get("assigned_at"):
        fields["assigned_at"] = ts
    if fields.get("status") == "cleared" and not fields.get("cleared_at"):
        fields["cleared_at"] = ts

    try:
        versioned_update("search_segments", segment_db_id, fields, expected_version=int(version))
    except VersionConflictError as e:
        return jsonify({
            "error":            "Version conflict",
            "expected_version": e.expected,
            "current_version":  e.actual,
        }), 409

    try:
        from basecamp.app import socketio
        socketio.emit("segment_updated", {
            "incident_id": incident_id,
            "id":          segment_db_id,
            **fields,
        })
    except Exception:
        pass

    log.info("Updated segment %s", segment_db_id)
    return jsonify({"message": "Segment updated", "id": segment_db_id})


# GET /api/map/<incident_id>/segments/summary
# Segment status counts — for BASECAMP overview panel

@map_bp.route("/<incident_id>/segments/summary", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def segments_summary(incident_id):
    with local_db() as db:
        rows = db.execute(
            """
            SELECT status, COUNT(*) as count
            FROM search_segments
            WHERE incident_id = ?
            GROUP BY status
            """,
            (incident_id,),
        ).fetchall()

    summary = {s: 0 for s in SEGMENT_STATUSES}
    for row in rows:
        summary[row["status"]] = row["count"]
    summary["total"] = sum(summary.values())

    return jsonify(summary)
