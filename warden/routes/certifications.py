"""
SARPack — warden/routes/certifications.py
Certification tracking for personnel.
Tracks cert type, issuing body, issue/expiry dates, and verification status.
Used by LOGBOOK to auto-populate ICS-206 medical personnel section.
"""

import logging
from datetime import date, datetime, timezone
from flask import Blueprint, jsonify, request
from core.auth import require_role, require_ic
from core.db import (
    versioned_insert,
    versioned_update,
    get_record,
    local_db,
    VersionConflictError,
)

log = logging.getLogger("warden.certifications")
certifications_bp = Blueprint("certifications", __name__)

# Valid certification types — extend as needed
CERT_TYPES = (
    "WFR",          # Wilderness First Responder
    "WEMT",         # Wilderness Emergency Medical Technician
    "EMT",          # Emergency Medical Technician
    "Paramedic",
    "RN",           # Registered Nurse
    "MD",           # Medical Doctor
    "CPR",
    "First Aid",
    "FEMA_ICS_100",
    "FEMA_ICS_200",
    "FEMA_ICS_300",
    "FEMA_ICS_400",
    "FEMA_IS_700",
    "FEMA_IS_800",
    "Swift_Water",
    "Rope_Rescue",
    "K9_Handler",
    "Ham_Radio",    # FCC Amateur Radio License
    "Other",
)


# ---------------------------------------------------------------------------
# GET /api/certifications/personnel/<id>
# All certifications for a personnel record, with expiry status
# ---------------------------------------------------------------------------

@certifications_bp.route("/personnel/<person_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def list_certs(person_id):
    person = get_record("personnel", person_id)
    if not person:
        return jsonify({"error": "Personnel record not found"}), 404

    with local_db() as db:
        rows = db.execute(
            "SELECT * FROM certifications WHERE personnel_id = ? ORDER BY cert_type",
            (person_id,),
        ).fetchall()

    today = date.today().isoformat()
    certs = []
    for row in rows:
        cert = dict(row)
        # Compute expiry status
        if cert["expiry_date"]:
            if cert["expiry_date"] < today:
                cert["expiry_status"] = "expired"
            elif cert["expiry_date"] < _days_from_today(30):
                cert["expiry_status"] = "expiring_soon"
            else:
                cert["expiry_status"] = "valid"
        else:
            cert["expiry_status"] = "no_expiry"
        certs.append(cert)

    return jsonify(certs)


# ---------------------------------------------------------------------------
# GET /api/certifications/expiring
# All certifications expiring within N days across all personnel
# Used by WARDEN dashboard to surface upcoming renewals
# ---------------------------------------------------------------------------

@certifications_bp.route("/expiring", methods=["GET"])
@require_role("IC", "ops_chief", "logistics")
def expiring_certs():
    days = int(request.args.get("days", 60))
    cutoff = _days_from_today(days)
    today = date.today().isoformat()

    with local_db() as db:
        rows = db.execute(
            """
            SELECT c.*, p.first_name, p.last_name, p.call_sign, p.email, p.phone
            FROM certifications c
            JOIN personnel p ON p.id = c.personnel_id
            WHERE c.expiry_date IS NOT NULL
            AND c.expiry_date <= ?
            AND p.is_active = 1
            ORDER BY c.expiry_date ASC
            """,
            (cutoff,),
        ).fetchall()

    results = []
    for row in rows:
        cert = dict(row)
        cert["expiry_status"] = "expired" if cert["expiry_date"] < today else "expiring_soon"
        results.append(cert)

    return jsonify(results)


# ---------------------------------------------------------------------------
# GET /api/certifications/medical
# All active personnel with medical certifications
# Used directly by LOGBOOK ICS-206 compiler
# ---------------------------------------------------------------------------

@certifications_bp.route("/medical", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def medical_personnel():
    medical_types = ("WFR", "WEMT", "EMT", "Paramedic", "RN", "MD")
    placeholders = ",".join("?" * len(medical_types))

    with local_db() as db:
        rows = db.execute(
            f"""
            SELECT p.id, p.first_name, p.last_name, p.call_sign,
                   p.phone, p.blood_type,
                   c.cert_type, c.cert_number, c.issuing_body,
                   c.expiry_date, c.is_verified
            FROM personnel p
            JOIN certifications c ON c.personnel_id = p.id
            WHERE c.cert_type IN ({placeholders})
            AND p.is_active = 1
            ORDER BY c.cert_type, p.last_name
            """,
            medical_types,
        ).fetchall()

    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# POST /api/certifications/
# Add a certification to a personnel record
# ---------------------------------------------------------------------------

@certifications_bp.route("/", methods=["POST"])
@require_role("IC", "logistics")
def add_cert():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    required = ("personnel_id", "cert_type")
    missing = [f for f in required if not data.get(f, "").strip()]
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    # Validate cert type
    cert_type = data["cert_type"].strip()
    if cert_type not in CERT_TYPES:
        return jsonify({
            "error": f"Invalid cert_type '{cert_type}'",
            "valid_types": CERT_TYPES,
        }), 400

    # Confirm personnel exists
    person = get_record("personnel", data["personnel_id"])
    if not person:
        return jsonify({"error": "Personnel record not found"}), 404

    # Validate date formats if provided
    for date_field in ("issued_date", "expiry_date"):
        if data.get(date_field):
            try:
                datetime.strptime(data[date_field], "%Y-%m-%d")
            except ValueError:
                return jsonify({
                    "error": f"{date_field} must be in YYYY-MM-DD format"
                }), 400

    record = {
        "personnel_id": data["personnel_id"],
        "cert_type":    cert_type,
        "cert_number":  data.get("cert_number", "").strip() or None,
        "issuing_body": data.get("issuing_body", "").strip() or None,
        "issued_date":  data.get("issued_date") or None,
        "expiry_date":  data.get("expiry_date") or None,
        "is_verified":  int(bool(data.get("is_verified", False))),
    }

    cert_id = versioned_insert("certifications", record)
    log.info("Added %s certification for personnel %s", cert_type, data["personnel_id"])

    return jsonify({
        "message": "Certification added",
        "id": cert_id,
    }), 201


# ---------------------------------------------------------------------------
# PATCH /api/certifications/<id>
# Update a certification record
# ---------------------------------------------------------------------------

@certifications_bp.route("/<cert_id>", methods=["PATCH"])
@require_role("IC", "logistics")
def update_cert(cert_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    version = data.get("version")
    if version is None:
        return jsonify({"error": "version is required for updates"}), 400

    protected = ("id", "personnel_id", "version", "created_at", "updated_at")
    fields = {k: v for k, v in data.items() if k not in protected}

    if not fields:
        return jsonify({"error": "No updatable fields provided"}), 400

    if "cert_type" in fields and fields["cert_type"] not in CERT_TYPES:
        return jsonify({
            "error": f"Invalid cert_type",
            "valid_types": CERT_TYPES,
        }), 400

    for date_field in ("issued_date", "expiry_date"):
        if fields.get(date_field):
            try:
                datetime.strptime(fields[date_field], "%Y-%m-%d")
            except ValueError:
                return jsonify({
                    "error": f"{date_field} must be in YYYY-MM-DD format"
                }), 400

    try:
        versioned_update("certifications", cert_id, fields, expected_version=int(version))
    except VersionConflictError as e:
        return jsonify({
            "error": "Version conflict",
            "expected_version": e.expected,
            "current_version": e.actual,
        }), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    log.info("Updated certification: %s", cert_id)
    return jsonify({"message": "Certification updated", "id": cert_id})


# ---------------------------------------------------------------------------
# POST /api/certifications/<id>/verify
# Mark a certification as verified by an IC or logistics officer
# ---------------------------------------------------------------------------

@certifications_bp.route("/<cert_id>/verify", methods=["POST"])
@require_role("IC", "logistics")
def verify_cert(cert_id):
    cert = get_record("certifications", cert_id)
    if not cert:
        return jsonify({"error": "Certification not found"}), 404

    if cert["is_verified"]:
        return jsonify({"message": "Certification is already verified"}), 200

    try:
        versioned_update(
            "certifications", cert_id,
            {"is_verified": 1},
            expected_version=cert["version"],
        )
    except VersionConflictError:
        return jsonify({"error": "Version conflict — re-fetch and try again"}), 409

    log.info("Verified certification %s (%s)", cert["cert_type"], cert_id)
    return jsonify({"message": "Certification verified", "id": cert_id})


# ---------------------------------------------------------------------------
# DELETE /api/certifications/<id>
# Remove a certification record — IC only
# Hard delete is acceptable here since certifications are child records
# with no downstream foreign key dependencies
# ---------------------------------------------------------------------------

@certifications_bp.route("/<cert_id>", methods=["DELETE"])
@require_ic
def delete_cert(cert_id):
    cert = get_record("certifications", cert_id)
    if not cert:
        return jsonify({"error": "Certification not found"}), 404

    with local_db() as db:
        db.execute("DELETE FROM certifications WHERE id = ?", (cert_id,))

    log.info("Deleted certification %s (%s)", cert["cert_type"], cert_id)
    return jsonify({"message": "Certification removed", "id": cert_id})


# ---------------------------------------------------------------------------
# GET /api/certifications/types
# Return the list of valid certification types
# Used by frontend dropdowns
# ---------------------------------------------------------------------------

@certifications_bp.route("/types", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def cert_types():
    return jsonify(list(CERT_TYPES))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _days_from_today(days: int) -> str:
    """Return an ISO date string N days from today."""
    from datetime import timedelta
    return (date.today() + timedelta(days=days)).isoformat()
