"""
SARPack 2.0 — api/deployments.py
Personnel check-in/check-out, assignments, and search segments for BASECAMP.
"""

import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, g

from core.database import get_db, row_to_dict, rows_to_list
from core.auth import require_auth, require_ic, require_logistics, audit

bp = Blueprint("deployments", __name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _deployment_or_404(deployment_id: str) -> dict:
    db  = get_db()
    row = db.execute(
        "SELECT * FROM deployments WHERE id = ?", (deployment_id,)
    ).fetchone()
    return row_to_dict(row)


def _enrich_deployment(dep: dict) -> dict:
    """Add personnel and incident details to a deployment."""
    db = get_db()

    dep["personnel"] = row_to_dict(db.execute(
        """SELECT id, first_name, last_name, call_sign, blood_type, phone
           FROM personnel WHERE id = ?""",
        (dep["personnel_id"],)
    ).fetchone())

    dep["incident"] = row_to_dict(db.execute(
        "SELECT id, incident_name, incident_number, status FROM incidents WHERE id = ?",
        (dep["incident_id"],)
    ).fetchone())

    # Current segment assignment
    dep["segment"] = row_to_dict(db.execute(
        """SELECT * FROM search_segments
           WHERE incident_id = ? AND assigned_to = ?
           AND status = 'assigned'
           ORDER BY updated_at DESC LIMIT 1""",
        (dep["incident_id"], dep.get("division") or dep.get("team"))
    ).fetchone())

    return dep


# ── Check-in ──────────────────────────────────────────────────────────────────

@bp.route("/<incident_id>/checkin", methods=["POST"])
@require_logistics
def checkin(incident_id):
    """
    Check a personnel member into an incident.
    Creates a deployment record and broadcasts to all windows.
    """
    db = get_db()

    # Verify incident exists and is active
    incident = db.execute(
        "SELECT id, status FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if not incident:
        return jsonify({"error": "Incident not found"}), 404
    if incident["status"] not in ("active", "standby"):
        return jsonify({"error": "Incident is not active"}), 400

    data         = request.get_json(silent=True) or {}
    personnel_id = data.get("personnel_id")

    if not personnel_id:
        return jsonify({"error": "personnel_id is required"}), 400

    # Verify personnel exists
    person = db.execute(
        "SELECT id, first_name, last_name, call_sign FROM personnel WHERE id = ? AND is_active = 1",
        (personnel_id,)
    ).fetchone()
    if not person:
        return jsonify({"error": "Personnel not found or inactive"}), 404

    # Check if already checked in to this incident
    existing = db.execute(
        """SELECT id FROM deployments
           WHERE incident_id = ? AND personnel_id = ? AND status = 'active'""",
        (incident_id, personnel_id)
    ).fetchone()
    if existing:
        return jsonify({"error": "Personnel already checked in to this incident"}), 409

    deployment_id = str(uuid.uuid4())
    now           = datetime.now(timezone.utc).isoformat()

    db.execute(
        """INSERT INTO deployments
           (id, incident_id, personnel_id, role, division, team,
            checked_in_at, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'active')""",
        (
            deployment_id, incident_id, personnel_id,
            data.get("role", "field_op"),
            data.get("division"),
            data.get("team"),
            now,
        )
    )
    db.commit()

    dep = _enrich_deployment(_deployment_or_404(deployment_id))

    # Broadcast check-in to all windows
    try:
        from app import socketio
        socketio.emit("personnel_checkin", {
            "incident_id": incident_id,
            "deployment":  dep,
        }, room=f"incident_{incident_id}")
    except Exception:
        pass

    audit("checkin", target_type="deployment", target_id=deployment_id,
          detail=f"{person['call_sign'] or person['last_name']} → {incident_id}")
    return jsonify(dep), 201


# ── Check-out ─────────────────────────────────────────────────────────────────

@bp.route("/<incident_id>/checkout/<deployment_id>", methods=["POST"])
@require_logistics
def checkout(incident_id, deployment_id):
    """Check a personnel member out of an incident."""
    dep = _deployment_or_404(deployment_id)
    if not dep or dep["incident_id"] != incident_id:
        return jsonify({"error": "Deployment not found"}), 404
    if dep["status"] != "active":
        return jsonify({"error": "Deployment is not active"}), 400

    db  = get_db()
    now = datetime.now(timezone.utc).isoformat()

    db.execute(
        """UPDATE deployments
           SET status = 'checked_out', checked_out_at = ?
           WHERE id = ?""",
        (now, deployment_id)
    )
    db.commit()

    try:
        from app import socketio
        socketio.emit("personnel_checkout", {
            "incident_id":   incident_id,
            "deployment_id": deployment_id,
            "personnel_id":  dep["personnel_id"],
        }, room=f"incident_{incident_id}")
    except Exception:
        pass

    audit("checkout", target_type="deployment", target_id=deployment_id)
    return jsonify({"message": "Checked out", "checked_out_at": now}), 200


# ── List deployments ──────────────────────────────────────────────────────────

@bp.route("/<incident_id>", methods=["GET"])
@require_auth
def list_deployments(incident_id):
    """
    List all deployments for an incident.
    Query params: status (active | checked_out | standby)
    """
    db     = get_db()
    status = request.args.get("status", "active")

    query  = """SELECT d.*, p.first_name, p.last_name, p.call_sign,
                       p.blood_type, p.phone
                FROM deployments d
                JOIN personnel p ON d.personnel_id = p.id
                WHERE d.incident_id = ?"""
    params = [incident_id]

    if status != "all":
        query += " AND d.status = ?"
        params.append(status)

    query += " ORDER BY d.checked_in_at"
    rows   = db.execute(query, params).fetchall()
    deps   = rows_to_list(rows)

    # Add cert summary for each deployed person
    for dep in deps:
        certs = db.execute(
            "SELECT cert_type FROM certifications WHERE personnel_id = ?",
            (dep["personnel_id"],)
        ).fetchall()
        dep["cert_types"] = [c["cert_type"] for c in certs]

    return jsonify({
        "deployments": deps,
        "count":       len(deps),
    }), 200


# ── Update deployment ─────────────────────────────────────────────────────────

@bp.route("/<incident_id>/<deployment_id>", methods=["PATCH"])
@require_logistics
def update_deployment(incident_id, deployment_id):
    """Update role, division, or team assignment for a deployment."""
    dep = _deployment_or_404(deployment_id)
    if not dep or dep["incident_id"] != incident_id:
        return jsonify({"error": "Deployment not found"}), 404

    data    = request.get_json(silent=True) or {}
    db      = get_db()
    updates = []
    params  = []

    for field in ["role", "division", "team", "status"]:
        if field in data:
            updates.append(f"{field} = ?")
            params.append(data[field])

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    params.append(deployment_id)
    db.execute(
        f"UPDATE deployments SET {', '.join(updates)} WHERE id = ?", params
    )
    db.commit()

    dep = _enrich_deployment(_deployment_or_404(deployment_id))

    try:
        from app import socketio
        socketio.emit("deployment_updated", {
            "incident_id": incident_id,
            "deployment":  dep,
        }, room=f"incident_{incident_id}")
    except Exception:
        pass

    audit("update_deployment", target_type="deployment", target_id=deployment_id)
    return jsonify(dep), 200


# ── Operator self-status (TRAILHEAD) ──────────────────────────────────────────

@bp.route("/me", methods=["GET"])
@require_auth
def operator_me():
    """
    TRAILHEAD calls this on boot to get the current operator's
    deployment status, assigned segment, and incident details.
    """
    db = get_db()

    # Find the user's personnel record
    user = db.execute(
        "SELECT personnel_id FROM users WHERE id = ?", (g.user_id,)
    ).fetchone()

    if not user or not user["personnel_id"]:
        return jsonify({"deployed": False}), 200

    personnel_id = user["personnel_id"]

    # Find active deployment
    dep = db.execute(
        """SELECT d.*, i.incident_name, i.incident_number,
                  i.lkp_lat, i.lkp_lng, i.lkp_notes
           FROM deployments d
           JOIN incidents i ON d.incident_id = i.id
           WHERE d.personnel_id = ? AND d.status = 'active'
           ORDER BY d.checked_in_at DESC LIMIT 1""",
        (personnel_id,)
    ).fetchone()

    if not dep:
        return jsonify({"deployed": False}), 200

    dep = row_to_dict(dep)

    # Find assigned segment
    segment = db.execute(
        """SELECT * FROM search_segments
           WHERE incident_id = ?
             AND (assigned_to = ? OR assigned_to = ?)
             AND status = 'assigned'
           LIMIT 1""",
        (dep["incident_id"], dep.get("division"), dep.get("team"))
    ).fetchone()

    # Get pending DZ targets for this operator
    dz_targets = rows_to_list(db.execute(
        """SELECT * FROM map_markers
           WHERE incident_id = ? AND marker_type = 'dz'
             AND target_device = ?
           ORDER BY created_at DESC LIMIT 5""",
        (dep["incident_id"], personnel_id)
    ).fetchall())

    return jsonify({
        "deployed":   True,
        "deployment": dep,
        "segment":    row_to_dict(segment),
        "dz_targets": dz_targets,
    }), 200


# ── Search segments ───────────────────────────────────────────────────────────

@bp.route("/<incident_id>/segments", methods=["GET"])
@require_auth
def list_segments(incident_id):
    """List all search segments for an incident."""
    db   = get_db()
    rows = db.execute(
        "SELECT * FROM search_segments WHERE incident_id = ? ORDER BY segment_id",
        (incident_id,)
    ).fetchall()
    return jsonify(rows_to_list(rows)), 200


@bp.route("/<incident_id>/segments", methods=["POST"])
@require_ic
def create_segment(incident_id):
    """Create a new search segment."""
    data = request.get_json(silent=True) or {}

    if not data.get("segment_id"):
        return jsonify({"error": "segment_id is required"}), 400

    db = get_db()

    # Check uniqueness within incident
    existing = db.execute(
        "SELECT id FROM search_segments WHERE incident_id = ? AND segment_id = ?",
        (incident_id, data["segment_id"].upper().strip())
    ).fetchone()
    if existing:
        return jsonify({"error": "Segment ID already exists for this incident"}), 409

    import json
    seg_id = str(uuid.uuid4())
    coords = data.get("boundary_coords")
    if isinstance(coords, list):
        coords = json.dumps(coords)

    db.execute(
        """INSERT INTO search_segments
           (id, incident_id, segment_id, area_name, description,
            status, assigned_to, pod, boundary_coords,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'unassigned', ?, ?, ?, datetime('now'), datetime('now'))""",
        (
            seg_id, incident_id,
            data["segment_id"].upper().strip(),
            data.get("area_name"),
            data.get("description"),
            data.get("assigned_to"),
            data.get("pod"),
            coords,
        )
    )
    db.commit()

    seg = row_to_dict(db.execute(
        "SELECT * FROM search_segments WHERE id = ?", (seg_id,)
    ).fetchone())

    try:
        from app import socketio
        socketio.emit("segment_updated", {
            "incident_id": incident_id,
            "segment":     seg,
            "action":      "created",
        }, room=f"incident_{incident_id}")
    except Exception:
        pass

    audit("create_segment", target_type="segment", target_id=seg_id,
          detail=data["segment_id"])
    return jsonify(seg), 201


@bp.route("/<incident_id>/segments/<segment_id>", methods=["PATCH"])
@require_ic
def update_segment(incident_id, segment_id):
    """Update a search segment — status, POD, assignment, boundary."""
    db  = get_db()
    seg = db.execute(
        "SELECT * FROM search_segments WHERE id = ? AND incident_id = ?",
        (segment_id, incident_id)
    ).fetchone()

    if not seg:
        return jsonify({"error": "Segment not found"}), 404

    data    = request.get_json(silent=True) or {}
    updates = []
    params  = []

    updatable = ["status", "assigned_to", "pod", "area_name", "description"]
    for field in updatable:
        if field in data:
            updates.append(f"{field} = ?")
            params.append(data[field])

    if "boundary_coords" in data:
        import json
        coords = data["boundary_coords"]
        if isinstance(coords, list):
            coords = json.dumps(coords)
        updates.append("boundary_coords = ?")
        params.append(coords)

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    updates.append("updated_at = datetime('now')")
    params.append(segment_id)

    db.execute(
        f"UPDATE search_segments SET {', '.join(updates)} WHERE id = ?", params
    )
    db.commit()

    updated = row_to_dict(db.execute(
        "SELECT * FROM search_segments WHERE id = ?", (segment_id,)
    ).fetchone())

    try:
        from app import socketio
        socketio.emit("segment_updated", {
            "incident_id": incident_id,
            "segment":     updated,
            "action":      "updated",
        }, room=f"incident_{incident_id}")
    except Exception:
        pass

    audit("update_segment", target_type="segment", target_id=segment_id)
    return jsonify(updated), 200


@bp.route("/<incident_id>/segments/<segment_id>", methods=["DELETE"])
@require_ic
def delete_segment(incident_id, segment_id):
    """Delete a search segment."""
    db = get_db()
    db.execute(
        "DELETE FROM search_segments WHERE id = ? AND incident_id = ?",
        (segment_id, incident_id)
    )
    db.commit()

    try:
        from app import socketio
        socketio.emit("segment_updated", {
            "incident_id": incident_id,
            "segment_id":  segment_id,
            "action":      "deleted",
        }, room=f"incident_{incident_id}")
    except Exception:
        pass

    return jsonify({"message": "Segment deleted"}), 200


# ── ICS-211 Check-in list ─────────────────────────────────────────────────────

@bp.route("/<incident_id>/ics211", methods=["GET"])
@require_auth
def ics211(incident_id):
    """
    Generate ICS-211 check-in/check-out list data.
    Returns all personnel who checked in, in chronological order.
    """
    db   = get_db()
    rows = db.execute(
        """SELECT d.*, p.first_name, p.last_name, p.call_sign,
                  p.home_agency, p.phone
           FROM deployments d
           JOIN personnel p ON d.personnel_id = p.id
           WHERE d.incident_id = ?
           ORDER BY d.checked_in_at""",
        (incident_id,)
    ).fetchall()

    incident = row_to_dict(db.execute(
        "SELECT * FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone())

    return jsonify({
        "incident":    incident,
        "entries":     rows_to_list(rows),
        "total_count": len(rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }), 200