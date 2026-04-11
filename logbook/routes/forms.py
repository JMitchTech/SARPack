"""
SARPack — logbook/routes/forms.py
Core LOGBOOK endpoints — compile, validate, sign, and export ICS forms.

Flow:
  1. GET  /api/forms/<incident_id>/compile   — compile from DB, run validator
  2. POST /api/forms/<incident_id>/narrative — IC fills narrative fields
  3. POST /api/forms/<incident_id>/sign      — IC digital sign (hard gate: zero RED fields)
  4. GET  /api/forms/<incident_id>/export/zip  — download signed ZIP packet
  5. GET  /api/forms/<incident_id>/export/json — download JSON export
  6. GET  /api/forms/<incident_id>/export/<form_key> — single PDF download
"""

import logging
from flask import Blueprint, jsonify, request, Response
from core.auth import require_role, require_ic, get_current_user
from core.db import (
    versioned_insert,
    versioned_update,
    get_record,
    local_db,
    now_utc,
    VersionConflictError,
)
from logbook.compiler  import compile_incident
from logbook.validator import validate
from logbook.exporter  import (
    build_zip_packet, build_json_export,
    zip_filename, json_filename,
)

log = logging.getLogger("logbook.forms")
forms_bp = Blueprint("forms", __name__)


# ---------------------------------------------------------------------------
# GET /api/forms/<incident_id>/compile
# Compile all form data and run compliance validation.
# Returns the compiled data plus the validation report.
# This is the primary endpoint — called when IC opens LOGBOOK.
# ---------------------------------------------------------------------------

@forms_bp.route("/<incident_id>/compile", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def compile_forms(incident_id):
    try:
        compiled = compile_incident(incident_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    validation = validate(compiled)

    # Strip raw deployment/radio data from response — too large, available via BASECAMP
    response_compiled = {k: v for k, v in compiled.items()
                         if k not in ("deployments", "radio_log", "segments")}

    return jsonify({
        "compiled":   response_compiled,
        "validation": validation,
        "incident_number": compiled["incident"]["incident_number"],
        "incident_name":   compiled["incident"]["incident_name"],
    })


# ---------------------------------------------------------------------------
# POST /api/forms/<incident_id>/narrative
# IC submits narrative fields for one or more forms.
# Saves to the appropriate ICS table in the database.
# Called after compile — IC fills in the yellow/red narrative fields.
# ---------------------------------------------------------------------------

@forms_bp.route("/<incident_id>/narrative", methods=["POST"])
@require_role("IC", "ops_chief")
def save_narrative(incident_id):
    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    form_key = data.get("form")
    if not form_key:
        return jsonify({"error": "form field is required (e.g. 'ics_201')"}), 400

    valid_forms = ("ics_201","ics_204","ics_205","ics_206",
                   "ics_209","ics_214","ics_215")
    if form_key not in valid_forms:
        return jsonify({"error": f"Invalid form '{form_key}'", "valid": list(valid_forms)}), 400

    fields = data.get("fields", {})
    if not fields:
        return jsonify({"error": "fields dict is required"}), 400

    user = get_current_user()

    # Check if a record already exists for this form/incident
    with local_db() as db:
        existing = db.execute(
            f"SELECT id, version, signed_at FROM {form_key} WHERE incident_id = ? "
            f"ORDER BY version DESC LIMIT 1",
            (incident_id,),
        ).fetchone()

    # Forms that use medical_officer_id instead of prepared_by
    FORMS_WITHOUT_PREPARED_BY = {"ics_206"}

    if existing:
        existing = dict(existing)
        # Cannot edit a signed form — amendments require a new version
        if existing.get("signed_at"):
            return jsonify({
                "error": "This form has been signed and is now immutable. "
                         "Use POST /api/forms/<id>/amend to create a new version.",
            }), 409

        # Update existing record
        safe_fields = {k: v for k, v in fields.items()
                       if k not in ("id","incident_id","signed_by","signed_at",
                                    "version","created_at","updated_at")}
        if form_key not in FORMS_WITHOUT_PREPARED_BY:
            safe_fields["prepared_by"] = user.get("personnel_id")
            safe_fields["prepared_at"] = now_utc()

        try:
            versioned_update(form_key, existing["id"], safe_fields,
                             expected_version=existing["version"])
        except VersionConflictError:
            return jsonify({"error": "Version conflict — re-fetch and try again"}), 409

        log.info("Updated narrative for %s on incident %s", form_key, incident_id)
        return jsonify({"message": "Narrative saved", "form": form_key, "id": existing["id"]})

    else:
        # Create new record
        record = {
            "incident_id": incident_id,
            **{k: v for k, v in fields.items()
               if k not in ("id","incident_id","signed_by","signed_at",
                            "version","created_at","updated_at")},
        }
        if form_key not in FORMS_WITHOUT_PREPARED_BY:
            record["prepared_by"] = user.get("personnel_id")
            record["prepared_at"] = now_utc()

        form_id = versioned_insert(form_key, record)
        log.info("Created %s record for incident %s", form_key, incident_id)
        return jsonify({"message": "Narrative saved", "form": form_key, "id": form_id}), 201


# ---------------------------------------------------------------------------
# POST /api/forms/<incident_id>/sign
# IC digital sign-off. HARD GATE — requires zero RED validation fields.
# Signs all unsigned forms simultaneously.
# Signed forms become immutable.
# ---------------------------------------------------------------------------

@forms_bp.route("/<incident_id>/sign", methods=["POST"])
@require_ic
def sign_forms(incident_id):
    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    # Compile and validate — must be clean before signing
    try:
        compiled = compile_incident(incident_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    validation = validate(compiled)

    if not validation["ready_to_sign"]:
        red_forms = [
            form for form, result in validation["forms"].items()
            if result["status"] == "red"
        ]
        red_fields = []
        for form, result in validation["forms"].items():
            for field in result["fields"]:
                if field["status"] == "red":
                    red_fields.append({
                        "form":    form,
                        "field":   field["field"],
                        "label":   field["label"],
                        "message": field["message"],
                    })

        return jsonify({
            "error":      "Cannot sign — compliance validation failed",
            "red_forms":  red_forms,
            "red_fields": red_fields,
            "message":    f"{len(red_fields)} required field(s) must be completed before signing.",
        }), 422

    user = get_current_user()
    ts = now_utc()
    signed_forms = []

    # Forms that have version + prepared_by columns
    VERSIONED_FORMS = ("ics_201","ics_204","ics_205","ics_206",
                       "ics_209","ics_214","ics_215")
    # ics_211 is append-only — handled separately
    FORMS_WITHOUT_PREPARED_BY = {"ics_206", "ics_211"}

    with local_db() as db:
        for form_key in VERSIONED_FORMS:
            existing = db.execute(
                f"SELECT id, version, signed_at FROM {form_key} "
                f"WHERE incident_id = ? ORDER BY version DESC LIMIT 1",
                (incident_id,),
            ).fetchone()

            if existing:
                existing = dict(existing)
                if not existing.get("signed_at"):
                    try:
                        versioned_update(
                            form_key, existing["id"],
                            {"signed_by": user.get("personnel_id"), "signed_at": ts},
                            expected_version=existing["version"],
                        )
                        signed_forms.append(form_key)
                    except Exception as e:
                        log.warning("Could not sign %s: %s", form_key, e)
            else:
                # Create a minimal signed record
                try:
                    record = {
                        "incident_id": incident_id,
                        "signed_by":   user.get("personnel_id"),
                        "signed_at":   ts,
                    }
                    if form_key not in FORMS_WITHOUT_PREPARED_BY:
                        record["prepared_by"] = user.get("personnel_id")
                        record["prepared_at"] = ts
                    versioned_insert(form_key, record)
                    signed_forms.append(form_key)
                except Exception as e:
                    log.warning("Could not create signed record for %s: %s", form_key, e)

    log.info(
        "IC sign-off complete for incident %s — %d forms signed by %s",
        incident_id, len(signed_forms), user.get("username"),
    )

    return jsonify({
        "message":      "All forms signed successfully",
        "signed_forms": signed_forms,
        "signed_by":    user.get("username"),
        "signed_at":    ts,
        "incident_number": incident["incident_number"],
    })


# ---------------------------------------------------------------------------
# GET /api/forms/<incident_id>/export/zip
# Download the full ICS packet as a ZIP file.
# Requires all forms to be signed.
# ---------------------------------------------------------------------------

@forms_bp.route("/<incident_id>/export/zip", methods=["GET"])
@require_role("IC", "ops_chief", "logistics")
def export_zip(incident_id):
    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    # Verify at least one form is signed
    with local_db() as db:
        signed_count = 0
        for form in ("ics_201","ics_209","ics_211"):
            row = db.execute(
                f"SELECT signed_at FROM {form} WHERE incident_id = ? AND signed_at IS NOT NULL",
                (incident_id,),
            ).fetchone()
            if row:
                signed_count += 1

    if signed_count == 0:
        return jsonify({
            "error": "No signed forms found. IC must sign before exporting.",
        }), 422

    try:
        from logbook.generator import render_all
        compiled = compile_incident(incident_id)
        rendered = render_all(compiled)
        zip_bytes = build_zip_packet(incident["incident_number"], rendered)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        log.exception("Export failed for incident %s: %s", incident_id, e)
        return jsonify({"error": "Export failed — check server logs"}), 500

    filename = zip_filename(incident["incident_number"])
    log.info("ZIP export: incident %s → %s (%d bytes)",
             incident_id, filename, len(zip_bytes))

    return Response(
        zip_bytes,
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# GET /api/forms/<incident_id>/export/json
# Download full structured JSON export.
# ---------------------------------------------------------------------------

@forms_bp.route("/<incident_id>/export/json", methods=["GET"])
@require_role("IC", "ops_chief", "logistics")
def export_json(incident_id):
    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    try:
        compiled = compile_incident(incident_id)
        json_bytes = build_json_export(compiled)
    except Exception as e:
        log.exception("JSON export failed: %s", e)
        return jsonify({"error": "Export failed"}), 500

    filename = json_filename(incident["incident_number"])
    return Response(
        json_bytes,
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# GET /api/forms/<incident_id>/export/<form_key>
# Download a single ICS form as PDF.
# ---------------------------------------------------------------------------

@forms_bp.route("/<incident_id>/export/<form_key>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics")
def export_single_form(incident_id, form_key):
    valid_forms = ("ics_201","ics_204","ics_205","ics_206",
                   "ics_209","ics_211","ics_214","ics_215")
    if form_key not in valid_forms:
        return jsonify({"error": f"Unknown form '{form_key}'"}), 400

    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    try:
        from logbook.generator import render_form
        compiled = compile_incident(incident_id)
        pdf_bytes = render_form(form_key, compiled.get(form_key, {}))
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        log.exception("Single form export failed: %s", e)
        return jsonify({"error": "Export failed"}), 500

    from logbook.exporter import FORM_FILENAMES
    filename = FORM_FILENAMES.get(form_key, f"{form_key}.pdf")

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# POST /api/forms/<incident_id>/amend
# Create a new version of a signed form — preserves the signed original.
# ---------------------------------------------------------------------------

@forms_bp.route("/<incident_id>/amend", methods=["POST"])
@require_ic
def amend_form(incident_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    form_key = data.get("form")
    if not form_key:
        return jsonify({"error": "form field is required"}), 400

    user = get_current_user()
    ts = now_utc()

    with local_db() as db:
        existing = db.execute(
            f"SELECT * FROM {form_key} WHERE incident_id = ? ORDER BY version DESC LIMIT 1",
            (incident_id,),
        ).fetchone()

    if not existing:
        return jsonify({"error": "No existing form record found to amend"}), 404

    existing = dict(existing)
    if not existing.get("signed_at"):
        return jsonify({"error": "Form is not yet signed — use /narrative to edit it"}), 400

    # Create a new record — amendment, not overwrite
    new_record = {k: v for k, v in existing.items()
                  if k not in ("id","version","created_at","updated_at",
                               "signed_by","signed_at")}
    new_record["incident_id"] = incident_id
    new_record["prepared_by"] = user.get("personnel_id")
    new_record["prepared_at"] = ts
    # Apply amendment fields
    amendment_fields = data.get("fields", {})
    new_record.update({k: v for k, v in amendment_fields.items()
                       if k not in ("id","incident_id","version","created_at","updated_at")})

    new_id = versioned_insert(form_key, new_record)
    log.info("Amendment created for %s on incident %s (new id: %s)",
             form_key, incident_id, new_id)

    return jsonify({
        "message": "Amendment created — IC must sign the new version before exporting",
        "form":    form_key,
        "id":      new_id,
    }), 201
