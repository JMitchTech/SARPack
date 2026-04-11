"""
SARPack — logbook/routes/history.py
Signed form history and audit trail.
Every signed form version is preserved — this endpoint surfaces them.
Used by LOGBOOK's history view and for after-action review.
"""

import logging
from flask import Blueprint, jsonify
from core.auth import require_role
from core.db import local_db, get_record

log = logging.getLogger("logbook.history")
history_bp = Blueprint("history", __name__)

ICS_FORMS = ("ics_201","ics_204","ics_205","ics_206",
             "ics_209","ics_211","ics_214","ics_215")

FORM_LABELS = {
    "ics_201": "ICS-201 Incident Briefing",
    "ics_204": "ICS-204 Assignment List",
    "ics_205": "ICS-205 Radio Plan",
    "ics_206": "ICS-206 Medical Plan",
    "ics_209": "ICS-209 Status Summary",
    "ics_211": "ICS-211 Check-In List",
    "ics_214": "ICS-214 Activity Log",
    "ics_215": "ICS-215 Operational Planning",
}


# ---------------------------------------------------------------------------
# GET /api/history/<incident_id>
# Summary of all form versions for an incident
# ---------------------------------------------------------------------------

@history_bp.route("/<incident_id>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def incident_history(incident_id):
    incident = get_record("incidents", incident_id)
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    history = {}

    with local_db() as db:
        for form in ICS_FORMS:
            try:
                if form == "ics_211":
                    # ics_211 is append-only — no version or signed_at column
                    rows = db.execute(
                        f"SELECT id, created_at FROM {form} WHERE incident_id = ?",
                        (incident_id,),
                    ).fetchall()
                    history[form] = {
                        "label":    FORM_LABELS[form],
                        "versions": [dict(r) for r in rows],
                        "signed":   False,
                        "count":    len(rows),
                    }
                else:
                    rows = db.execute(
                        f"""
                        SELECT f.id, f.version, f.created_at, f.updated_at,
                               f.signed_at,
                               p.first_name || ' ' || p.last_name as signed_by_name
                        FROM {form} f
                        LEFT JOIN personnel p ON p.id = f.signed_by
                        WHERE f.incident_id = ?
                        ORDER BY f.version ASC
                        """,
                        (incident_id,),
                    ).fetchall()
                    history[form] = {
                        "label":    FORM_LABELS[form],
                        "versions": [dict(r) for r in rows],
                        "signed":   any(dict(r).get("signed_at") for r in rows),
                        "count":    len(rows),
                    }
            except Exception as e:
                history[form] = {"label": FORM_LABELS[form], "versions": [], "signed": False, "count": 0, "error": str(e)}

    return jsonify({
        "incident_number": incident["incident_number"],
        "incident_name":   incident["incident_name"],
        "forms":           history,
    })


# ---------------------------------------------------------------------------
# GET /api/history/<incident_id>/<form_key>
# Full version history for a single form
# ---------------------------------------------------------------------------

@history_bp.route("/<incident_id>/<form_key>", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def form_history(incident_id, form_key):
    if form_key not in ICS_FORMS:
        return jsonify({"error": f"Unknown form '{form_key}'"}), 400

    with local_db() as db:
        rows = db.execute(
            f"""
            SELECT f.*,
                   pb.first_name || ' ' || pb.last_name as prepared_by_name,
                   sb.first_name || ' ' || sb.last_name as signed_by_name
            FROM {form_key} f
            LEFT JOIN personnel pb ON pb.id = f.prepared_by
            LEFT JOIN personnel sb ON sb.id = f.signed_by
            WHERE f.incident_id = ?
            ORDER BY f.version ASC
            """,
            (incident_id,),
        ).fetchall()

    return jsonify({
        "form":     form_key,
        "label":    FORM_LABELS[form_key],
        "versions": [dict(r) for r in rows],
    })


# ---------------------------------------------------------------------------
# GET /api/history/incidents
# All incidents that have at least one signed form
# Used by LOGBOOK landing page
# ---------------------------------------------------------------------------

@history_bp.route("/incidents", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def incidents_with_forms():
    with local_db() as db:
        # Incidents with at least one ICS-201 (most complete indicator)
        rows = db.execute(
            """
            SELECT i.id, i.incident_number, i.incident_name,
                   i.incident_type, i.status, i.started_at, i.closed_at,
                   i.county, i.state,
                   f.signed_at, f.version
            FROM incidents i
            LEFT JOIN ics_201 f ON f.incident_id = i.id
            ORDER BY i.started_at DESC
            """
        ).fetchall()

    return jsonify([dict(r) for r in rows])
