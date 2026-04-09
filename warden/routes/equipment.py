"""
SARPack — warden/routes/equipment.py
Equipment inventory tracking per personnel.
Tracks gear assigned to each operator — condition, serial numbers,
and expiry dates for time-sensitive equipment (flares, medical supplies).

Note: The equipment table is added via migration 0002.
Schema: id, personnel_id, item_name, serial_number, condition,
        assigned_date, expiry_date, notes, version, created_at, updated_at
"""

import logging
from datetime import date, datetime, timedelta
from flask import Blueprint, jsonify, request
from core.auth import require_role, require_ic
from core.db import (
    versioned_insert,
    versioned_update,
    get_record,
    local_db,
    VersionConflictError,
)

log = logging.getLogger("warden.equipment")
equipment_bp = Blueprint("equipment", __name__)

CONDITION_STATES = ("serviceable", "needs_inspection", "needs_repair", "unserviceable")


# ---------------------------------------------------------------------------
# GET /api/equipment/personnel/<id>
# All equipment assigned to a personnel record
# ---------------------------------------------------------------------------

@equipment_bp.route("/personnel/<person_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def list_equipment(person_id):
    person = get_record("personnel", person_id)
    if not person:
        return jsonify({"error": "Personnel record not found"}), 404

    today = date.today().isoformat()

    with local_db() as db:
        rows = db.execute(
            "SELECT * FROM equipment WHERE personnel_id = ? ORDER BY item_name",
            (person_id,),
        ).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        if item.get("expiry_date"):
            if item["expiry_date"] < today:
                item["expiry_status"] = "expired"
            elif item["expiry_date"] < (date.today() + timedelta(days=30)).isoformat():
                item["expiry_status"] = "expiring_soon"
            else:
                item["expiry_status"] = "valid"
        else:
            item["expiry_status"] = "no_expiry"
        items.append(item)

    return jsonify(items)


# ---------------------------------------------------------------------------
# GET /api/equipment/unserviceable
# All equipment flagged as needing attention across all personnel
# ---------------------------------------------------------------------------

@equipment_bp.route("/unserviceable", methods=["GET"])
@require_role("IC", "ops_chief", "logistics")
def unserviceable_equipment():
    with local_db() as db:
        rows = db.execute(
            """
            SELECT e.*, p.first_name, p.last_name, p.call_sign
            FROM equipment e
            JOIN personnel p ON p.id = e.personnel_id
            WHERE e.condition IN ('needs_inspection', 'needs_repair', 'unserviceable')
            AND p.is_active = 1
            ORDER BY e.condition, p.last_name
            """,
        ).fetchall()

    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# POST /api/equipment/
# Assign equipment to a personnel record
# ---------------------------------------------------------------------------

@equipment_bp.route("/", methods=["POST"])
@require_role("IC", "logistics")
def add_equipment():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    required = ("personnel_id", "item_name")
    missing = [f for f in required if not data.get(f, "").strip()]
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    person = get_record("personnel", data["personnel_id"])
    if not person:
        return jsonify({"error": "Personnel record not found"}), 404

    condition = data.get("condition", "serviceable")
    if condition not in CONDITION_STATES:
        return jsonify({
            "error": f"Invalid condition '{condition}'",
            "valid_states": CONDITION_STATES,
        }), 400

    for date_field in ("assigned_date", "expiry_date"):
        if data.get(date_field):
            try:
                datetime.strptime(data[date_field], "%Y-%m-%d")
            except ValueError:
                return jsonify({
                    "error": f"{date_field} must be in YYYY-MM-DD format"
                }), 400

    record = {
        "personnel_id":  data["personnel_id"],
        "item_name":     data["item_name"].strip(),
        "serial_number": data.get("serial_number", "").strip() or None,
        "condition":     condition,
        "assigned_date": data.get("assigned_date") or date.today().isoformat(),
        "expiry_date":   data.get("expiry_date") or None,
        "notes":         data.get("notes", "").strip() or None,
    }

    item_id = versioned_insert("equipment", record)
    log.info("Assigned equipment '%s' to personnel %s",
             record["item_name"], data["personnel_id"])

    return jsonify({"message": "Equipment assigned", "id": item_id}), 201


# ---------------------------------------------------------------------------
# PATCH /api/equipment/<id>
# Update equipment record — condition, notes, expiry
# ---------------------------------------------------------------------------

@equipment_bp.route("/<item_id>", methods=["PATCH"])
@require_role("IC", "logistics")
def update_equipment(item_id):
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

    if "condition" in fields and fields["condition"] not in CONDITION_STATES:
        return jsonify({
            "error": f"Invalid condition",
            "valid_states": CONDITION_STATES,
        }), 400

    try:
        versioned_update("equipment", item_id, fields, expected_version=int(version))
    except VersionConflictError as e:
        return jsonify({
            "error": "Version conflict",
            "expected_version": e.expected,
            "current_version": e.actual,
        }), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    log.info("Updated equipment: %s", item_id)
    return jsonify({"message": "Equipment updated", "id": item_id})


# ---------------------------------------------------------------------------
# DELETE /api/equipment/<id>
# Remove equipment assignment — IC only
# ---------------------------------------------------------------------------

@equipment_bp.route("/<item_id>", methods=["DELETE"])
@require_ic
def delete_equipment(item_id):
    item = get_record("equipment", item_id)
    if not item:
        return jsonify({"error": "Equipment record not found"}), 404

    with local_db() as db:
        db.execute("DELETE FROM equipment WHERE id = ?", (item_id,))

    log.info("Removed equipment '%s' (%s)", item["item_name"], item_id)
    return jsonify({"message": "Equipment removed", "id": item_id})


# ---------------------------------------------------------------------------
# GET /api/equipment/conditions
# Return valid condition states — for frontend dropdowns
# ---------------------------------------------------------------------------

@equipment_bp.route("/conditions", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def conditions():
    return jsonify(list(CONDITION_STATES))
