"""
SARPack — warden/routes/schedules.py
Shift-based on-call scheduling for volunteer personnel.
Tracks who is on-call and when, surfaced by BASECAMP when
a new incident is created to show available responders.
"""

import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from core.auth import require_role, require_ic
from core.db import (
    versioned_insert,
    versioned_update,
    get_record,
    local_db,
    now_utc,
    VersionConflictError,
)

log = logging.getLogger("warden.schedules")
schedules_bp = Blueprint("schedules", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dt(value: str, field_name: str) -> str:
    """
    Validate and normalize a datetime string to ISO 8601 UTC.
    Accepts: 'YYYY-MM-DDTHH:MM', 'YYYY-MM-DDTHH:MM:SS', full ISO 8601.
    Returns the normalized string or raises ValueError with a clear message.
    """
    formats = (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    )
    for fmt in formats:
        try:
            dt = datetime.strptime(value.strip(), fmt)
            return dt.isoformat()
        except ValueError:
            continue
    raise ValueError(
        f"{field_name} must be a valid datetime. "
        f"Expected format: YYYY-MM-DDTHH:MM (e.g. 2025-06-15T18:00). Got: '{value}'"
    )


def _now_iso() -> str:
    """Current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# GET /api/schedules/oncall
# Who is on-call RIGHT NOW — primary query used by BASECAMP
# ---------------------------------------------------------------------------

@schedules_bp.route("/oncall", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def oncall_now():
    now = _now_iso()

    with local_db() as db:
        rows = db.execute(
            """
            SELECT s.*, p.first_name, p.last_name, p.call_sign,
                   p.phone, p.blood_type,
                   GROUP_CONCAT(c.cert_type, ', ') as certifications
            FROM schedules s
            JOIN personnel p ON p.id = s.personnel_id
            LEFT JOIN certifications c ON c.personnel_id = p.id
            WHERE s.is_oncall = 1
            AND s.starts_at <= ?
            AND s.ends_at >= ?
            AND p.is_active = 1
            GROUP BY s.id
            ORDER BY p.last_name, p.first_name
            """,
            (now, now),
        ).fetchall()

    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# GET /api/schedules/upcoming
# Shifts starting within the next N days — default 14
# ---------------------------------------------------------------------------

@schedules_bp.route("/upcoming", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def upcoming_shifts():
    from datetime import timedelta
    days = int(request.args.get("days", 14))
    now = _now_iso()
    cutoff = (
        datetime.now(timezone.utc) + timedelta(days=days)
    ).isoformat()

    with local_db() as db:
        rows = db.execute(
            """
            SELECT s.*, p.first_name, p.last_name, p.call_sign, p.phone
            FROM schedules s
            JOIN personnel p ON p.id = s.personnel_id
            WHERE s.starts_at >= ?
            AND s.starts_at <= ?
            AND p.is_active = 1
            ORDER BY s.starts_at ASC
            """,
            (now, cutoff),
        ).fetchall()

    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# GET /api/schedules/personnel/<id>
# All shifts for a specific personnel record
# ---------------------------------------------------------------------------

@schedules_bp.route("/personnel/<person_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def person_schedule(person_id):
    person = get_record("personnel", person_id)
    if not person:
        return jsonify({"error": "Personnel record not found"}), 404

    # Optional date range filter
    from_dt  = request.args.get("from")
    until_dt = request.args.get("until")

    query = "SELECT * FROM schedules WHERE personnel_id = ?"
    params = [person_id]

    if from_dt:
        query += " AND ends_at >= ?"
        params.append(from_dt)
    if until_dt:
        query += " AND starts_at <= ?"
        params.append(until_dt)

    query += " ORDER BY starts_at ASC"

    with local_db() as db:
        rows = db.execute(query, params).fetchall()

    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# GET /api/schedules/
# All schedules — optionally filtered by shift name or date range
# ---------------------------------------------------------------------------

@schedules_bp.route("/", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def list_schedules():
    shift_name = request.args.get("shift")
    from_dt    = request.args.get("from")
    until_dt   = request.args.get("until")

    query = """
        SELECT s.*, p.first_name, p.last_name, p.call_sign
        FROM schedules s
        JOIN personnel p ON p.id = s.personnel_id
        WHERE p.is_active = 1
    """
    params = []

    if shift_name:
        query += " AND LOWER(s.shift_name) LIKE ?"
        params.append(f"%{shift_name.lower()}%")
    if from_dt:
        query += " AND s.ends_at >= ?"
        params.append(from_dt)
    if until_dt:
        query += " AND s.starts_at <= ?"
        params.append(until_dt)

    query += " ORDER BY s.starts_at ASC"

    with local_db() as db:
        rows = db.execute(query, params).fetchall()

    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# POST /api/schedules/
# Create a new shift assignment
# ---------------------------------------------------------------------------

@schedules_bp.route("/", methods=["POST"])
@require_role("IC", "logistics")
def create_shift():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    required = ("personnel_id", "shift_name", "starts_at", "ends_at")
    missing = [f for f in required if not data.get(f, "")]
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    # Confirm personnel exists
    person = get_record("personnel", data["personnel_id"])
    if not person:
        return jsonify({"error": "Personnel record not found"}), 404

    if not person["is_active"]:
        return jsonify({"error": "Cannot schedule an inactive personnel record"}), 400

    # Parse and validate datetimes
    try:
        starts_at = _parse_dt(data["starts_at"], "starts_at")
        ends_at   = _parse_dt(data["ends_at"],   "ends_at")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Ensure end is after start
    if ends_at <= starts_at:
        return jsonify({
            "error": "ends_at must be after starts_at"
        }), 400

    # Warn on overlapping shifts for the same person (non-blocking)
    with local_db() as db:
        overlap = db.execute(
            """
            SELECT COUNT(*) as count FROM schedules
            WHERE personnel_id = ?
            AND starts_at < ?
            AND ends_at > ?
            """,
            (data["personnel_id"], ends_at, starts_at),
        ).fetchone()["count"]

    record = {
        "personnel_id": data["personnel_id"],
        "shift_name":   data["shift_name"].strip(),
        "starts_at":    starts_at,
        "ends_at":      ends_at,
        "is_oncall":    int(bool(data.get("is_oncall", True))),
        "notes":        data.get("notes", "").strip() or None,
    }

    shift_id = versioned_insert("schedules", record)
    log.info(
        "Created shift '%s' for %s %s (%s → %s)",
        record["shift_name"], person["first_name"], person["last_name"],
        starts_at, ends_at,
    )

    response = {
        "message": "Shift created",
        "id": shift_id,
    }
    if overlap > 0:
        response["warning"] = (
            f"This personnel record has {overlap} overlapping shift(s). "
            f"Review their schedule to confirm this is intentional."
        )

    return jsonify(response), 201


# ---------------------------------------------------------------------------
# POST /api/schedules/bulk
# Create multiple shifts at once — for setting up a full rotation
# Accepts a list of shift objects, same format as POST /api/schedules/
# Returns a summary of created and failed entries
# ---------------------------------------------------------------------------

@schedules_bp.route("/bulk", methods=["POST"])
@require_role("IC", "logistics")
def bulk_create_shifts():
    data = request.get_json(silent=True)
    if not data or not isinstance(data, list):
        return jsonify({"error": "Request body must be a JSON array of shift objects"}), 400

    if len(data) > 100:
        return jsonify({"error": "Maximum 100 shifts per bulk request"}), 400

    created = []
    failed  = []

    for i, shift in enumerate(data):
        try:
            required = ("personnel_id", "shift_name", "starts_at", "ends_at")
            missing = [f for f in required if not shift.get(f, "")]
            if missing:
                raise ValueError(f"Missing fields: {missing}")

            person = get_record("personnel", shift["personnel_id"])
            if not person:
                raise ValueError("Personnel record not found")
            if not person["is_active"]:
                raise ValueError("Personnel record is inactive")

            starts_at = _parse_dt(shift["starts_at"], "starts_at")
            ends_at   = _parse_dt(shift["ends_at"],   "ends_at")

            if ends_at <= starts_at:
                raise ValueError("ends_at must be after starts_at")

            record = {
                "personnel_id": shift["personnel_id"],
                "shift_name":   shift["shift_name"].strip(),
                "starts_at":    starts_at,
                "ends_at":      ends_at,
                "is_oncall":    int(bool(shift.get("is_oncall", True))),
                "notes":        shift.get("notes", "").strip() or None,
            }
            shift_id = versioned_insert("schedules", record)
            created.append({"index": i, "id": shift_id, "shift_name": record["shift_name"]})

        except Exception as e:
            failed.append({"index": i, "error": str(e), "data": shift})

    log.info("Bulk shift create: %d created, %d failed", len(created), len(failed))
    return jsonify({
        "created": len(created),
        "failed":  len(failed),
        "results": created,
        "errors":  failed,
    }), 207 if failed else 201


# ---------------------------------------------------------------------------
# PATCH /api/schedules/<id>
# Update a shift — times, name, on-call status, notes
# ---------------------------------------------------------------------------

@schedules_bp.route("/<shift_id>", methods=["PATCH"])
@require_role("IC", "logistics")
def update_shift(shift_id):
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

    # Re-validate datetimes if being changed
    for dt_field in ("starts_at", "ends_at"):
        if dt_field in fields:
            try:
                fields[dt_field] = _parse_dt(fields[dt_field], dt_field)
            except ValueError as e:
                return jsonify({"error": str(e)}), 400

    # If both are being updated, check order
    if "starts_at" in fields and "ends_at" in fields:
        if fields["ends_at"] <= fields["starts_at"]:
            return jsonify({"error": "ends_at must be after starts_at"}), 400

    try:
        versioned_update("schedules", shift_id, fields, expected_version=int(version))
    except VersionConflictError as e:
        return jsonify({
            "error": "Version conflict",
            "expected_version": e.expected,
            "current_version":  e.actual,
        }), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    log.info("Updated shift: %s", shift_id)
    return jsonify({"message": "Shift updated", "id": shift_id})


# ---------------------------------------------------------------------------
# DELETE /api/schedules/<id>
# Remove a shift — IC only
# Hard delete is acceptable — schedules are not auditable records
# ---------------------------------------------------------------------------

@schedules_bp.route("/<shift_id>", methods=["DELETE"])
@require_ic
def delete_shift(shift_id):
    shift = get_record("schedules", shift_id)
    if not shift:
        return jsonify({"error": "Shift not found"}), 404

    with local_db() as db:
        db.execute("DELETE FROM schedules WHERE id = ?", (shift_id,))

    log.info("Deleted shift '%s' (%s)", shift["shift_name"], shift_id)
    return jsonify({"message": "Shift deleted", "id": shift_id})


# ---------------------------------------------------------------------------
# POST /api/schedules/<id>/toggle-oncall
# Quickly flip the on-call status of a shift without a full PATCH
# Useful for last-minute availability changes
# ---------------------------------------------------------------------------

@schedules_bp.route("/<shift_id>/toggle-oncall", methods=["POST"])
@require_role("IC", "logistics")
def toggle_oncall(shift_id):
    shift = get_record("schedules", shift_id)
    if not shift:
        return jsonify({"error": "Shift not found"}), 404

    new_status = 0 if shift["is_oncall"] else 1

    try:
        versioned_update(
            "schedules", shift_id,
            {"is_oncall": new_status},
            expected_version=shift["version"],
        )
    except VersionConflictError:
        return jsonify({"error": "Version conflict — re-fetch and try again"}), 409

    status_label = "on-call" if new_status else "off-call"
    log.info("Shift %s toggled to %s", shift_id, status_label)

    return jsonify({
        "message": f"Shift marked {status_label}",
        "id":       shift_id,
        "is_oncall": new_status,
    })
