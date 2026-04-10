"""
SARPack — basecamp/routes/deployments.py
Personnel deployment management for active incidents.
Handles check-in, check-out, role/division/team assignment.
Every deployment is scoped to a single incident.
Feeds ICS-211 (check-in list) and ICS-204 (assignment list) in LOGBOOK.
"""

import logging
from flask import Blueprint, jsonify, request
from core.auth import require_role, require_ic, get_current_user
from core.db import (
    versioned_insert,
    versioned_update,
    get_record,
    get_deployments,
    local_db,
    now_utc,
    VersionConflictError,
)

log = logging.getLogger("basecamp.deployments")
deployments_bp = Blueprint("deployments", __name__)


@deployments_bp.route("/<incident_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def list_deployments(incident_id):
    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    status = request.args.get("status")

    with local_db() as db:
        query = """
            SELECT d.*, p.first_name, p.last_name, p.call_sign,
                   p.blood_type, p.phone, p.emergency_contact_name,
                   p.emergency_contact_phone,
                   GROUP_CONCAT(c.cert_type, ', ') as certifications
            FROM deployments d
            JOIN personnel p ON p.id = d.personnel_id
            LEFT JOIN certifications c ON c.personnel_id = p.id
            WHERE d.incident_id = ?
        """
        params = [incident_id]
        if status:
            query += " AND d.status = ?"
            params.append(status)
        query += " GROUP BY d.id ORDER BY d.checked_in_at ASC"
        rows = db.execute(query, params).fetchall()

    return jsonify([dict(r) for r in rows])


@deployments_bp.route("/<incident_id>/checkin", methods=["POST"])
@require_role("IC", "ops_chief", "logistics")
def checkin(incident_id):
    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    if incident["status"] != "active":
        return jsonify({"error": "Cannot deploy to a non-active incident"}), 400

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    required = ("personnel_id", "role")
    missing = [f for f in required if not data.get(f, "").strip()]
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    person = get_record("personnel", data["personnel_id"])
    if not person:
        return jsonify({"error": "Personnel record not found"}), 404
    if not person["is_active"]:
        return jsonify({"error": "Cannot deploy an inactive personnel record"}), 400

    ts = now_utc()

    with local_db() as db:
        existing = db.execute(
            "SELECT id, status, version FROM deployments "
            "WHERE incident_id = ? AND personnel_id = ?",
            (incident_id, data["personnel_id"]),
        ).fetchone()

    if existing:
        existing = dict(existing)
        if existing["status"] == "active":
            return jsonify({
                "error": "This personnel record is already checked in to this incident",
                "deployment_id": existing["id"],
            }), 409
        try:
            versioned_update(
                "deployments", existing["id"],
                {
                    "status":         "active",
                    "role":           data["role"].strip(),
                    "division":       data.get("division", "").strip() or None,
                    "team":           data.get("team", "").strip() or None,
                    "checked_in_at":  ts,
                    "checked_out_at": None,
                },
                expected_version=existing["version"],
            )
        except VersionConflictError:
            return jsonify({"error": "Version conflict — re-fetch and try again"}), 409
        deployment_id = existing["id"]
        log.info("Re-checked-in: %s %s → incident %s",
                 person["first_name"], person["last_name"], incident_id)
    else:
        record = {
            "incident_id":   incident_id,
            "personnel_id":  data["personnel_id"],
            "role":          data["role"].strip(),
            "division":      data.get("division", "").strip() or None,
            "team":          data.get("team", "").strip() or None,
            "checked_in_at": ts,
            "status":        "active",
        }
        deployment_id = versioned_insert("deployments", record)
        log.info("Check-in: %s %s → incident %s (role: %s)",
                 person["first_name"], person["last_name"],
                 incident_id, record["role"])

    try:
        from basecamp.app import socketio
        socketio.emit("operator_checkin", {
            "incident_id":   incident_id,
            "deployment_id": deployment_id,
            "personnel_id":  data["personnel_id"],
            "name":          f"{person['first_name']} {person['last_name']}",
            "call_sign":     person["call_sign"],
            "role":          data["role"].strip(),
            "checked_in_at": ts,
        })
    except Exception:
        pass

    return jsonify({
        "message":       "Personnel checked in",
        "deployment_id": deployment_id,
        "checked_in_at": ts,
    }), 201


@deployments_bp.route("/<incident_id>/checkout/<deployment_id>", methods=["POST"])
@require_role("IC", "ops_chief", "logistics")
def checkout(incident_id, deployment_id):
    deployment = get_record("deployments", deployment_id)
    if not deployment:
        return jsonify({"error": "Deployment not found"}), 404

    if deployment["incident_id"] != incident_id:
        return jsonify({"error": "Deployment does not belong to this incident"}), 400

    if deployment["status"] == "checked_out":
        return jsonify({"error": "Personnel is already checked out"}), 400

    person = get_record("personnel", deployment["personnel_id"])
    ts = now_utc()

    try:
        versioned_update(
            "deployments", deployment_id,
            {"status": "checked_out", "checked_out_at": ts},
            expected_version=deployment["version"],
        )
    except VersionConflictError:
        return jsonify({"error": "Version conflict — re-fetch and try again"}), 409

    log.info("Check-out: %s %s ← incident %s",
             person["first_name"], person["last_name"], incident_id)

    try:
        from basecamp.app import socketio
        socketio.emit("operator_checkout", {
            "incident_id":    incident_id,
            "deployment_id":  deployment_id,
            "personnel_id":   deployment["personnel_id"],
            "name":           f"{person['first_name']} {person['last_name']}",
            "call_sign":      person["call_sign"],
            "checked_out_at": ts,
        })
    except Exception:
        pass

    return jsonify({
        "message":        "Personnel checked out",
        "deployment_id":  deployment_id,
        "checked_out_at": ts,
    })


@deployments_bp.route("/<incident_id>/assignment/<deployment_id>", methods=["PATCH"])
@require_role("IC", "ops_chief")
def update_assignment(incident_id, deployment_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    version = data.get("version")
    if version is None:
        return jsonify({"error": "version is required for updates"}), 400

    deployment = get_record("deployments", deployment_id)
    if not deployment:
        return jsonify({"error": "Deployment not found"}), 404

    if deployment["incident_id"] != incident_id:
        return jsonify({"error": "Deployment does not belong to this incident"}), 400

    allowed = ("role", "division", "team")
    fields = {k: v for k, v in data.items() if k in allowed}

    if not fields:
        return jsonify({"error": "No updatable assignment fields provided"}), 400

    try:
        versioned_update("deployments", deployment_id, fields, expected_version=int(version))
    except VersionConflictError as e:
        return jsonify({
            "error":            "Version conflict",
            "expected_version": e.expected,
            "current_version":  e.actual,
        }), 409

    log.info("Updated assignment for deployment %s", deployment_id)

    try:
        from basecamp.app import socketio
        socketio.emit("assignment_updated", {
            "incident_id":   incident_id,
            "deployment_id": deployment_id,
            **fields,
        })
    except Exception:
        pass

    return jsonify({"message": "Assignment updated", "deployment_id": deployment_id})


@deployments_bp.route("/<incident_id>/summary", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def deployment_summary(incident_id):
    with local_db() as db:
        total = db.execute(
            "SELECT COUNT(*) as n FROM deployments "
            "WHERE incident_id = ? AND status = 'active'",
            (incident_id,),
        ).fetchone()["n"]

        by_division = db.execute(
            """
            SELECT division, COUNT(*) as count FROM deployments
            WHERE incident_id = ? AND status = 'active'
            GROUP BY division ORDER BY division
            """,
            (incident_id,),
        ).fetchall()

        by_role = db.execute(
            """
            SELECT role, COUNT(*) as count FROM deployments
            WHERE incident_id = ? AND status = 'active'
            GROUP BY role ORDER BY count DESC
            """,
            (incident_id,),
        ).fetchall()

    return jsonify({
        "total_active": total,
        "by_division":  [dict(r) for r in by_division],
        "by_role":      [dict(r) for r in by_role],
    })
