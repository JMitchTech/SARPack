"""
SARPack 2.0 — api/patients.py
Patient assessment records from TRAILHEAD field submissions.
Supports tap-to-select fields for rapid field entry.
"""

import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, g

from core.database import get_db, row_to_dict, rows_to_list
from core.auth import require_auth, require_ic, require_logistics, audit

bp = Blueprint("patients", __name__)


# ── Tap-to-select option sets ─────────────────────────────────────────────────
# These are returned to TRAILHEAD so the UI can render tap buttons.
# Updating here updates the field app automatically.

MECHANISMS = [
    "Fall", "MVC", "Struck by object", "Crush injury",
    "Submersion / drowning", "Hypothermia / cold exposure",
    "Heat illness", "Cardiac event", "Allergic reaction",
    "Bite / sting", "Toxic exposure", "Burns",
    "Medical emergency", "Psychiatric", "Unknown",
]

BODY_REGIONS = [
    "Head", "Neck", "Chest", "Abdomen", "Back / Spine",
    "Pelvis", "Left shoulder / arm", "Right shoulder / arm",
    "Left hand / wrist", "Right hand / wrist",
    "Left hip / thigh", "Right hip / thigh",
    "Left knee / lower leg", "Right knee / lower leg",
    "Left foot / ankle", "Right foot / ankle",
    "Multiple regions", "Full body",
]

INJURY_TYPES = [
    "Laceration", "Abrasion", "Contusion / bruising",
    "Fracture (suspected)", "Dislocation", "Sprain / strain",
    "Burn", "Puncture wound", "Amputation",
    "Internal (suspected)", "Head injury / concussion",
    "Spinal (suspected)", "No visible injury",
]

SEVERITY_LEVELS = [
    {"value": "minor",    "label": "Minor",    "color": "green"},
    {"value": "moderate", "label": "Moderate", "color": "amber"},
    {"value": "serious",  "label": "Serious",  "color": "orange"},
    {"value": "critical", "label": "Critical", "color": "red"},
]

COMPLAINT_CATEGORIES = [
    "Trauma", "Medical", "Environmental",
    "Behavioral / psychiatric", "Unknown",
]

TRANSPORT_METHODS = [
    "Walking out", "Litter carry", "ATV / vehicle",
    "Helicopter (hoist)", "Helicopter (landing zone)",
    "Boat", "Other",
]


# ── Field options endpoint (TRAILHEAD reads this on boot) ─────────────────────

@bp.route("/options", methods=["GET"])
@require_auth
def field_options():
    """
    Return all tap-to-select option sets for TRAILHEAD patient form.
    TRAILHEAD calls this on boot and caches locally for offline use.
    """
    return jsonify({
        "mechanisms":           MECHANISMS,
        "body_regions":         BODY_REGIONS,
        "injury_types":         INJURY_TYPES,
        "severity_levels":      SEVERITY_LEVELS,
        "complaint_categories": COMPLAINT_CATEGORIES,
        "transport_methods":    TRANSPORT_METHODS,
        "loc_options": [
            {"value": "Alert",        "label": "Alert",        "abbr": "A"},
            {"value": "Verbal",       "label": "Verbal",       "abbr": "V"},
            {"value": "Pain",         "label": "Pain",         "abbr": "P"},
            {"value": "Unresponsive", "label": "Unresponsive", "abbr": "U"},
        ],
        "sex_options": ["Unknown", "Male", "Female", "Other"],
    }), 200


# ── Patient CRUD ──────────────────────────────────────────────────────────────

@bp.route("/", methods=["GET"])
@require_auth
def list_patients():
    """
    List all patient records.
    Query params: incident_id, severity, limit, offset
    """
    db          = get_db()
    incident_id = request.args.get("incident_id")
    severity    = request.args.get("severity")
    limit       = min(int(request.args.get("limit",  100)), 500)
    offset      = int(request.args.get("offset", 0))

    query  = """SELECT pt.*, p.first_name, p.last_name, p.call_sign
                FROM patients pt
                LEFT JOIN personnel p ON pt.reported_by = p.id
                WHERE 1=1"""
    params = []

    if incident_id:
        query += " AND pt.incident_id = ?"
        params.append(incident_id)
    if severity:
        query += " AND pt.severity = ?"
        params.append(severity)

    query += " ORDER BY pt.assessed_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    rows = db.execute(query, params).fetchall()
    return jsonify({
        "patients": rows_to_list(rows),
        "total":    len(rows),
    }), 200


@bp.route("/<patient_id>", methods=["GET"])
@require_auth
def get_patient(patient_id):
    """Get a single patient record."""
    db  = get_db()
    row = db.execute(
        """SELECT pt.*, p.first_name, p.last_name, p.call_sign
           FROM patients pt
           LEFT JOIN personnel p ON pt.reported_by = p.id
           WHERE pt.id = ?""",
        (patient_id,)
    ).fetchone()

    if not row:
        return jsonify({"error": "Patient not found"}), 404

    patient = row_to_dict(row)

    # Parse vitals JSON
    import json
    if patient.get("vitals"):
        try:
            patient["vitals"] = json.loads(patient["vitals"])
        except Exception:
            pass

    return jsonify(patient), 200


@bp.route("/", methods=["POST"])
@require_auth
def create_patient():
    """
    Submit a patient assessment.
    Called by TRAILHEAD field devices and BASECAMP.
    Supports both tap-to-select fields and free text.
    Broadcasts to all connected windows.
    """
    data        = request.get_json(silent=True) or {}
    incident_id = data.get("incident_id")

    if not incident_id:
        return jsonify({"error": "incident_id is required"}), 400

    db = get_db()

    incident = db.execute(
        "SELECT id FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    # Resolve reporter
    user = db.execute(
        "SELECT personnel_id FROM users WHERE id = ?", (g.user_id,)
    ).fetchone()
    reported_by = user["personnel_id"] if user else None

    import json
    vitals = data.get("vitals")
    if isinstance(vitals, dict):
        vitals = json.dumps(vitals)

    patient_id = str(uuid.uuid4())
    now        = datetime.now(timezone.utc).isoformat()

    db.execute(
        """INSERT INTO patients
           (id, incident_id, reported_by,
            patient_name, patient_age, patient_sex,
            chief_complaint, complaint_category,
            mechanism, body_region, injury_type, severity,
            loc, vitals, treatment_given,
            scene_lat, scene_lng,
            transport_method, receiving_facility,
            assessed_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            patient_id, incident_id, reported_by,
            data.get("patient_name"),
            data.get("patient_age"),
            data.get("patient_sex"),
            data.get("chief_complaint"),
            data.get("complaint_category", "Trauma"),
            data.get("mechanism"),
            # body_region and injury_type may be lists from tap-select
            json.dumps(data["body_region"]) if isinstance(data.get("body_region"), list)
                else data.get("body_region"),
            json.dumps(data["injury_type"]) if isinstance(data.get("injury_type"), list)
                else data.get("injury_type"),
            data.get("severity"),
            data.get("loc", "Alert"),
            vitals,
            data.get("treatment_given"),
            data.get("scene_lat"),
            data.get("scene_lng"),
            data.get("transport_method"),
            data.get("receiving_facility"),
            data.get("assessed_at", now),
            now,
        )
    )
    db.commit()

    patient = row_to_dict(db.execute(
        "SELECT * FROM patients WHERE id = ?", (patient_id,)
    ).fetchone())

    # Broadcast to all windows — BASECAMP shows new patient alert
    try:
        from app import socketio
        socketio.emit("patient_reported", {
            "incident_id": incident_id,
            "patient_id":  patient_id,
            "severity":    data.get("severity"),
            "location": {
                "lat": data.get("scene_lat"),
                "lng": data.get("scene_lng"),
            },
        }, room=f"incident_{incident_id}")
    except Exception:
        pass

    audit("patient_reported", target_type="patient", target_id=patient_id,
          detail=f"severity={data.get('severity')}")
    return jsonify(patient), 201


@bp.route("/<patient_id>", methods=["PATCH"])
@require_logistics
def update_patient(patient_id):
    """Update a patient record — transport, facility, additional treatment."""
    db      = get_db()
    patient = db.execute(
        "SELECT id FROM patients WHERE id = ?", (patient_id,)
    ).fetchone()
    if not patient:
        return jsonify({"error": "Patient not found"}), 404

    data    = request.get_json(silent=True) or {}
    updates = []
    params  = []

    updatable = [
        "patient_name", "patient_age", "patient_sex",
        "chief_complaint", "complaint_category",
        "mechanism", "body_region", "injury_type", "severity",
        "loc", "treatment_given", "transport_method",
        "receiving_facility", "scene_lat", "scene_lng",
    ]

    import json
    for field in updatable:
        if field in data:
            val = data[field]
            if isinstance(val, (list, dict)):
                val = json.dumps(val)
            updates.append(f"{field} = ?")
            params.append(val)

    if "vitals" in data:
        val = data["vitals"]
        if isinstance(val, dict):
            val = json.dumps(val)
        updates.append("vitals = ?")
        params.append(val)

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    params.append(patient_id)
    db.execute(
        f"UPDATE patients SET {', '.join(updates)} WHERE id = ?", params
    )
    db.commit()

    audit("update_patient", target_type="patient", target_id=patient_id)
    return jsonify(row_to_dict(db.execute(
        "SELECT * FROM patients WHERE id = ?", (patient_id,)
    ).fetchone())), 200


# ── Incident patient summary ──────────────────────────────────────────────────

@bp.route("/incident/<incident_id>/summary", methods=["GET"])
@require_auth
def incident_patient_summary(incident_id):
    """
    Patient summary for an incident.
    Used by BASECAMP dashboard and ICS-206 Medical Plan.
    """
    db = get_db()

    patients = rows_to_list(db.execute(
        """SELECT pt.*, p.first_name as reporter_first,
                  p.last_name as reporter_last, p.call_sign as reporter_callsign
           FROM patients pt
           LEFT JOIN personnel p ON pt.reported_by = p.id
           WHERE pt.incident_id = ?
           ORDER BY pt.assessed_at DESC""",
        (incident_id,)
    ).fetchall())

    import json
    for pt in patients:
        if pt.get("vitals"):
            try:
                pt["vitals"] = json.loads(pt["vitals"])
            except Exception:
                pass

    by_severity = {}
    for pt in patients:
        sev = pt.get("severity") or "unknown"
        by_severity[sev] = by_severity.get(sev, 0) + 1

    return jsonify({
        "incident_id":  incident_id,
        "total":        len(patients),
        "by_severity":  by_severity,
        "patients":     patients,
    }), 200