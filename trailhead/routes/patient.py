"""
SARPack — trailhead/routes/patient.py
Patient assessment form for field operators.
Captures wilderness patient data: demographics, chief complaint,
mechanism of injury, vitals, physical exam, and treatment given.

Data is stored in a patient_assessments table and syncs to BASECAMP.
The IC and medical officer can view assessments in real time.
Assessment data feeds the ICS-206 medical plan in LOGBOOK.
"""

import json
import logging
from flask import Blueprint, jsonify, request
from core.auth import require_role, get_current_user
from core.db import (
    versioned_insert,
    versioned_update,
    get_record,
    local_db,
    now_utc,
    VersionConflictError,
)

log = logging.getLogger("trailhead.patient")
patient_bp = Blueprint("patient", __name__)

# Level of consciousness scale
LOC_OPTIONS = ("Alert", "Verbal", "Pain", "Unresponsive")

# Chief complaint categories
COMPLAINT_CATEGORIES = (
    "Trauma", "Medical", "Environmental", "Behavioral", "Unknown",
)


# ---------------------------------------------------------------------------
# POST /api/patient/
# Create a new patient assessment.
# Called when an operator locates a subject in the field.
# ---------------------------------------------------------------------------

@patient_bp.route("/", methods=["POST"])
@require_role("IC", "ops_chief", "logistics", "field_op")
def create_assessment():
    user = get_current_user()
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    required = ("incident_id",)
    missing = [f for f in required if not data.get(f, "").strip()]
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    incident = get_record("incidents", data["incident_id"])
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    # Vitals — validated but not required (operator may not have equipment)
    vitals = data.get("vitals", {})
    if vitals:
        try:
            if "heart_rate" in vitals:
                hr = int(vitals["heart_rate"])
                if not (0 <= hr <= 300):
                    return jsonify({"error": "heart_rate out of range (0-300)"}), 400
            if "spo2" in vitals:
                spo2 = float(vitals["spo2"])
                if not (0 <= spo2 <= 100):
                    return jsonify({"error": "spo2 out of range (0-100)"}), 400
            if "respiratory_rate" in vitals:
                rr = int(vitals["respiratory_rate"])
                if not (0 <= rr <= 60):
                    return jsonify({"error": "respiratory_rate out of range (0-60)"}), 400
            if "gcs" in vitals:
                gcs = int(vitals["gcs"])
                if not (3 <= gcs <= 15):
                    return jsonify({"error": "GCS score must be 3-15"}), 400
        except (ValueError, TypeError) as e:
            return jsonify({"error": f"Invalid vitals value: {e}"}), 400

    record = {
        "incident_id":          data["incident_id"],
        "assessed_by":          user.get("personnel_id"),
        "assessed_at":          data.get("assessed_at", now_utc()),
        # Patient demographics
        "patient_name":         data.get("patient_name", "").strip() or None,
        "patient_age":          data.get("patient_age"),
        "patient_sex":          data.get("patient_sex", "").strip() or None,
        # Chief complaint
        "chief_complaint":      data.get("chief_complaint", "").strip() or None,
        "complaint_category":   data.get("complaint_category", "Unknown"),
        "mechanism_of_injury":  data.get("mechanism_of_injury", "").strip() or None,
        # Scene
        "scene_location":       data.get("scene_location", "").strip() or None,
        "scene_lat":            data.get("scene_lat"),
        "scene_lng":            data.get("scene_lng"),
        # Level of consciousness
        "loc":                  data.get("loc", "Alert"),
        # Vitals (stored as JSON)
        "vitals":               json.dumps(vitals) if vitals else None,
        # Physical exam findings
        "physical_exam":        json.dumps(data.get("physical_exam", {})),
        # Treatment given
        "treatment_given":      data.get("treatment_given", "").strip() or None,
        # Additional notes
        "notes":                data.get("notes", "").strip() or None,
        # Outcome/disposition
        "disposition":          data.get("disposition", "").strip() or None,
    }

    assessment_id = versioned_insert("patient_assessments", record)

    log.info(
        "Patient assessment created for incident %s by %s",
        data["incident_id"], user.get("username"),
    )

    # Notify BASECAMP
    try:
        from basecamp.app import socketio
        socketio.emit("patient_assessment", {
            "incident_id":    data["incident_id"],
            "assessment_id":  assessment_id,
            "chief_complaint": record["chief_complaint"],
            "loc":            record["loc"],
            "assessed_at":    record["assessed_at"],
        }, room=data["incident_id"])
    except Exception:
        pass

    return jsonify({
        "message":       "Patient assessment created",
        "id":            assessment_id,
        "assessed_at":   record["assessed_at"],
    }), 201


# ---------------------------------------------------------------------------
# PATCH /api/patient/<id>
# Update an assessment — add vitals, update disposition, add treatment notes.
# Assessments can be updated until the incident is closed.
# ---------------------------------------------------------------------------

@patient_bp.route("/<assessment_id>", methods=["PATCH"])
@require_role("IC", "ops_chief", "logistics", "field_op")
def update_assessment(assessment_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    version = data.get("version")
    if version is None:
        return jsonify({"error": "version is required for updates"}), 400

    assessment = get_record("patient_assessments", assessment_id)
    if not assessment:
        return jsonify({"error": "Assessment not found"}), 404

    protected = ("id", "incident_id", "assessed_by", "assessed_at",
                 "version", "created_at", "updated_at")
    fields = {k: v for k, v in data.items() if k not in protected}

    # Re-serialize vitals and physical_exam if included
    if "vitals" in fields and isinstance(fields["vitals"], dict):
        fields["vitals"] = json.dumps(fields["vitals"])
    if "physical_exam" in fields and isinstance(fields["physical_exam"], dict):
        fields["physical_exam"] = json.dumps(fields["physical_exam"])

    if not fields:
        return jsonify({"error": "No updatable fields provided"}), 400

    try:
        versioned_update("patient_assessments", assessment_id, fields,
                         expected_version=int(version))
    except VersionConflictError as e:
        return jsonify({
            "error":            "Version conflict",
            "expected_version": e.expected,
            "current_version":  e.actual,
        }), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    log.info("Updated patient assessment: %s", assessment_id)
    return jsonify({"message": "Assessment updated", "id": assessment_id})


# ---------------------------------------------------------------------------
# GET /api/patient/<assessment_id>
# Retrieve a single patient assessment.
# ---------------------------------------------------------------------------

@patient_bp.route("/<assessment_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "field_op", "observer")
def get_assessment(assessment_id):
    assessment = get_record("patient_assessments", assessment_id)
    if not assessment:
        return jsonify({"error": "Assessment not found"}), 404

    assessment = dict(assessment)

    # Parse JSON fields for response
    for field in ("vitals", "physical_exam"):
        if assessment.get(field):
            try:
                assessment[field] = json.loads(assessment[field])
            except Exception:
                pass

    return jsonify(assessment)


# ---------------------------------------------------------------------------
# GET /api/patient/incident/<incident_id>
# All patient assessments for an incident.
# Used by BASECAMP medical panel and LOGBOOK ICS-206 compiler.
# ---------------------------------------------------------------------------

@patient_bp.route("/incident/<incident_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "field_op", "observer")
def list_assessments(incident_id):
    with local_db() as db:
        rows = db.execute(
            """
            SELECT pa.*,
                   p.first_name || ' ' || p.last_name as assessed_by_name,
                   p.call_sign as assessed_by_call_sign
            FROM patient_assessments pa
            LEFT JOIN personnel p ON p.id = pa.assessed_by
            WHERE pa.incident_id = ?
            ORDER BY pa.assessed_at DESC
            """,
            (incident_id,),
        ).fetchall()

    results = []
    for row in rows:
        assessment = dict(row)
        for field in ("vitals", "physical_exam"):
            if assessment.get(field):
                try:
                    assessment[field] = json.loads(assessment[field])
                except Exception:
                    pass
        results.append(assessment)

    return jsonify(results)


# ---------------------------------------------------------------------------
# GET /api/patient/options
# Return valid options for dropdowns — used by TRAILHEAD form UI.
# ---------------------------------------------------------------------------

@patient_bp.route("/options", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "field_op", "observer")
def get_options():
    return jsonify({
        "loc_options":          list(LOC_OPTIONS),
        "complaint_categories": list(COMPLAINT_CATEGORIES),
        "disposition_options": [
            "Transported by ground ambulance",
            "Transported by air ambulance",
            "Walked out — refused transport",
            "Treated and released on scene",
            "Transferred to other agency",
            "Deceased — awaiting ME",
            "Still on scene",
        ],
        "vitals_fields": {
            "heart_rate":         "Heart rate (bpm)",
            "blood_pressure":     "Blood pressure (mmHg)",
            "respiratory_rate":   "Respiratory rate (breaths/min)",
            "spo2":               "SpO2 (%)",
            "temperature":        "Temperature (°F)",
            "gcs":                "Glasgow Coma Scale (3-15)",
            "pupils":             "Pupils (PERRL / unequal / fixed)",
            "skin":               "Skin (color, temp, moisture)",
        },
    })
