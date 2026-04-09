"""
SARPack — warden/routes/personnel.py
Personnel roster management.
Create, read, update, and deactivate personnel records.
Deactivation only — records are never hard deleted (audit trail).
"""

import logging
from flask import Blueprint, jsonify, request
from core.auth import require_role, require_ic, get_current_user
from core.db import (
    versioned_insert,
    versioned_update,
    get_record,
    local_db,
    now_utc,
    VersionConflictError,
)

log = logging.getLogger("warden.personnel")
personnel_bp = Blueprint("personnel", __name__)


# ---------------------------------------------------------------------------
# GET /api/personnel/
# List all active personnel, optional search by name or call sign
# ---------------------------------------------------------------------------

@personnel_bp.route("/", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def list_personnel():
    search = request.args.get("q", "").strip().lower()
    active_only = request.args.get("active", "true").lower() == "true"

    with local_db() as db:
        if search:
            rows = db.execute(
                """
                SELECT p.*,
                       COUNT(d.id) as deployment_count,
                       MAX(d.checked_in_at) as last_deployed
                FROM personnel p
                LEFT JOIN deployments d ON d.personnel_id = p.id
                WHERE (
                    LOWER(p.first_name) LIKE ?
                    OR LOWER(p.last_name) LIKE ?
                    OR LOWER(p.call_sign) LIKE ?
                    OR LOWER(p.email) LIKE ?
                )
                AND (? OR p.is_active = 1)
                GROUP BY p.id
                ORDER BY p.last_name, p.first_name
                """,
                (f"%{search}%", f"%{search}%", f"%{search}%",
                 f"%{search}%", not active_only),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT p.*,
                       COUNT(d.id) as deployment_count,
                       MAX(d.checked_in_at) as last_deployed
                FROM personnel p
                LEFT JOIN deployments d ON d.personnel_id = p.id
                WHERE (? OR p.is_active = 1)
                GROUP BY p.id
                ORDER BY p.last_name, p.first_name
                """,
                (not active_only,),
            ).fetchall()

    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# GET /api/personnel/<id>
# Single personnel record with certifications and deployment history
# ---------------------------------------------------------------------------

@personnel_bp.route("/<person_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def get_person(person_id):
    person = get_record("personnel", person_id)
    if not person:
        return jsonify({"error": "Personnel record not found"}), 404

    # Attach certifications
    with local_db() as db:
        certs = db.execute(
            "SELECT * FROM certifications WHERE personnel_id = ? ORDER BY cert_type",
            (person_id,),
        ).fetchall()

        # Attach deployment history
        deployments = db.execute(
            """
            SELECT d.*, i.incident_name, i.incident_type, i.incident_number
            FROM deployments d
            JOIN incidents i ON i.id = d.incident_id
            WHERE d.personnel_id = ?
            ORDER BY d.checked_in_at DESC
            """,
            (person_id,),
        ).fetchall()

        # Attach equipment
        equipment = db.execute(
            "SELECT * FROM equipment WHERE personnel_id = ? ORDER BY item_name",
            (person_id,),
        ).fetchall()

        # Attach user account if exists
        user = db.execute(
            "SELECT id, username, role, is_active, last_login_at "
            "FROM users WHERE personnel_id = ?",
            (person_id,),
        ).fetchone()

    person["certifications"] = [dict(c) for c in certs]
    person["deployments"] = [dict(d) for d in deployments]
    person["equipment"] = [dict(e) for e in equipment]
    person["user_account"] = dict(user) if user else None

    return jsonify(person)


# ---------------------------------------------------------------------------
# POST /api/personnel/
# Create a new personnel record
# ---------------------------------------------------------------------------

@personnel_bp.route("/", methods=["POST"])
@require_role("IC", "logistics")
def create_person():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    # Required fields
    required = ("first_name", "last_name")
    missing = [f for f in required if not data.get(f, "").strip()]
    if missing:
        return jsonify({
            "error": "Missing required fields",
            "missing": missing,
        }), 400

    # Check for duplicate email
    if data.get("email"):
        with local_db() as db:
            existing = db.execute(
                "SELECT id FROM personnel WHERE email = ?",
                (data["email"].strip().lower(),),
            ).fetchone()
        if existing:
            return jsonify({
                "error": "A personnel record with this email already exists",
                "existing_id": existing["id"],
            }), 409

    # Check for duplicate call sign
    if data.get("call_sign"):
        with local_db() as db:
            existing = db.execute(
                "SELECT id FROM personnel WHERE call_sign = ?",
                (data["call_sign"].strip().upper(),),
            ).fetchone()
        if existing:
            return jsonify({
                "error": "A personnel record with this call sign already exists",
                "existing_id": existing["id"],
            }), 409

    record = {
        "first_name":               data["first_name"].strip(),
        "last_name":                data["last_name"].strip(),
        "call_sign":                data.get("call_sign", "").strip().upper() or None,
        "phone":                    data.get("phone", "").strip() or None,
        "email":                    data.get("email", "").strip().lower() or None,
        "blood_type":               data.get("blood_type", "").strip().upper() or None,
        "allergies":                data.get("allergies", "").strip() or None,
        "medical_notes":            data.get("medical_notes", "").strip() or None,
        "emergency_contact_name":   data.get("emergency_contact_name", "").strip() or None,
        "emergency_contact_phone":  data.get("emergency_contact_phone", "").strip() or None,
        "is_active":                1,
    }

    person_id = versioned_insert("personnel", record)
    log.info("Created personnel record: %s %s (%s)",
             record["first_name"], record["last_name"], person_id)

    return jsonify({
        "message": "Personnel record created",
        "id": person_id,
    }), 201


# ---------------------------------------------------------------------------
# PATCH /api/personnel/<id>
# Update a personnel record (partial update — only send changed fields)
# Requires current version number to prevent conflicts
# ---------------------------------------------------------------------------

@personnel_bp.route("/<person_id>", methods=["PATCH"])
@require_role("IC", "logistics")
def update_person(person_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    version = data.get("version")
    if version is None:
        return jsonify({
            "error": "version is required for updates. "
                     "Fetch the current record first and include its version number."
        }), 400

    # Strip protected fields — these cannot be updated via this endpoint
    protected = ("id", "version", "created_at", "updated_at", "is_active")
    fields = {k: v for k, v in data.items() if k not in protected}

    if not fields:
        return jsonify({"error": "No updatable fields provided"}), 400

    # Normalize
    if "call_sign" in fields and fields["call_sign"]:
        fields["call_sign"] = fields["call_sign"].strip().upper()
    if "email" in fields and fields["email"]:
        fields["email"] = fields["email"].strip().lower()

    try:
        versioned_update("personnel", person_id, fields, expected_version=int(version))
    except VersionConflictError as e:
        return jsonify({
            "error": "Version conflict — this record was modified by another user.",
            "expected_version": e.expected,
            "current_version": e.actual,
            "action": "Re-fetch the record and reapply your changes.",
        }), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    log.info("Updated personnel record: %s", person_id)
    return jsonify({"message": "Personnel record updated", "id": person_id})


# ---------------------------------------------------------------------------
# POST /api/personnel/<id>/deactivate
# Deactivate a personnel record (soft delete — never hard delete)
# IC only — removing someone from active roster is a significant action
# ---------------------------------------------------------------------------

@personnel_bp.route("/<person_id>/deactivate", methods=["POST"])
@require_ic
def deactivate_person(person_id):
    person = get_record("personnel", person_id)
    if not person:
        return jsonify({"error": "Personnel record not found"}), 404

    if not person["is_active"]:
        return jsonify({"error": "Personnel record is already inactive"}), 400

    # Check for active deployments before deactivating
    with local_db() as db:
        active_deployments = db.execute(
            "SELECT COUNT(*) as count FROM deployments "
            "WHERE personnel_id = ? AND status = 'active'",
            (person_id,),
        ).fetchone()["count"]

    if active_deployments > 0:
        return jsonify({
            "error": "Cannot deactivate — personnel has active deployments.",
            "active_deployments": active_deployments,
            "action": "Check them out of all active incidents first.",
        }), 409

    try:
        versioned_update(
            "personnel", person_id,
            {"is_active": 0},
            expected_version=person["version"],
        )
    except VersionConflictError:
        return jsonify({
            "error": "Version conflict — record was modified. Re-fetch and try again."
        }), 409

    log.info("Deactivated personnel record: %s %s (%s)",
             person["first_name"], person["last_name"], person_id)

    return jsonify({
        "message": f"{person['first_name']} {person['last_name']} deactivated.",
        "id": person_id,
    })


# ---------------------------------------------------------------------------
# POST /api/personnel/<id>/reactivate
# Reactivate a previously deactivated record
# ---------------------------------------------------------------------------

@personnel_bp.route("/<person_id>/reactivate", methods=["POST"])
@require_ic
def reactivate_person(person_id):
    person = get_record("personnel", person_id)
    if not person:
        return jsonify({"error": "Personnel record not found"}), 404

    if person["is_active"]:
        return jsonify({"error": "Personnel record is already active"}), 400

    try:
        versioned_update(
            "personnel", person_id,
            {"is_active": 1},
            expected_version=person["version"],
        )
    except VersionConflictError:
        return jsonify({
            "error": "Version conflict — record was modified. Re-fetch and try again."
        }), 409

    log.info("Reactivated personnel record: %s %s (%s)",
             person["first_name"], person["last_name"], person_id)

    return jsonify({
        "message": f"{person['first_name']} {person['last_name']} reactivated.",
        "id": person_id,
    })


# ---------------------------------------------------------------------------
# GET /api/personnel/<id>/summary
# Lightweight summary card — used by BASECAMP deployment panels
# ---------------------------------------------------------------------------

@personnel_bp.route("/<person_id>/summary", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def person_summary(person_id):
    with local_db() as db:
        row = db.execute(
            """
            SELECT p.id, p.first_name, p.last_name, p.call_sign,
                   p.blood_type, p.phone, p.is_active,
                   GROUP_CONCAT(c.cert_type, ', ') as certifications
            FROM personnel p
            LEFT JOIN certifications c ON c.personnel_id = p.id
            WHERE p.id = ?
            GROUP BY p.id
            """,
            (person_id,),
        ).fetchone()

    if not row:
        return jsonify({"error": "Personnel record not found"}), 404

    return jsonify(dict(row))
