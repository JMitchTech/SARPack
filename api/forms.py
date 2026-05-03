"""
SARPack 2.0 — api/forms.py
ICS form management, compilation, validation, and export for LOGBOOK.
"""

import uuid
import json
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, g

from core.database import get_db, row_to_dict, rows_to_list
from core.auth import require_auth, require_ic, require_logistics, audit

bp = Blueprint("forms", __name__)


# ── ICS form definitions ───────────────────────────────────────────────────────

ICS_DEFINITIONS = {
    "ics_201": {
        "number":     "ICS-201",
        "title":      "Incident Briefing",
        "short":      "Incident Briefing",
        "desc":       "Initial situation, objectives, resources, and organization.",
        "required":   ["incident_name", "incident_number", "started_at", "ic_name"],
        "sources":    ["incidents", "personnel", "deployments"],
        "narrative_fields": ["situation_summary", "initial_objectives", "current_actions"],
    },
    "ics_204": {
        "number":     "ICS-204",
        "title":      "Assignment List",
        "short":      "Assignment List",
        "desc":       "Work assignments for Divisions and Groups.",
        "required":   ["operational_period", "divisions"],
        "sources":    ["deployments", "personnel", "search_segments"],
        "narrative_fields": ["special_instructions"],
    },
    "ics_205": {
        "number":     "ICS-205",
        "title":      "Incident Radio Communications Plan",
        "short":      "Radio Comms Plan",
        "desc":       "Radio channels, frequencies, and assignments.",
        "required":   ["operational_period", "channel_assignments"],
        "sources":    ["radio_entries", "incidents"],
        "narrative_fields": ["channel_notes"],
    },
    "ics_206": {
        "number":     "ICS-206",
        "title":      "Medical Plan",
        "short":      "Medical Plan",
        "desc":       "Medical aid stations, personnel credentials, hospital routing.",
        "required":   ["medical_officer", "hospital_routing"],
        "sources":    ["certifications", "personnel", "deployments"],
        "narrative_fields": ["hospital_routing", "medical_procedures"],
    },
    "ics_209": {
        "number":     "ICS-209",
        "title":      "Incident Status Summary",
        "short":      "Status Summary",
        "desc":       "Situation and resource status for agency executives.",
        "required":   ["incident_phase", "situation_narrative", "total_personnel"],
        "sources":    ["incidents", "deployments", "search_segments"],
        "narrative_fields": ["situation_narrative", "planned_actions"],
    },
    "ics_211": {
        "number":     "ICS-211",
        "title":      "Incident Check-In/Check-Out List",
        "short":      "Check-In List",
        "desc":       "Complete roster of all personnel. Primary accountability document.",
        "required":   ["entries"],
        "sources":    ["deployments", "personnel"],
        "narrative_fields": [],
    },
    "ics_214": {
        "number":     "ICS-214",
        "title":      "Activity Log",
        "short":      "Activity Log",
        "desc":       "Timestamped activity record per operator.",
        "required":   ["unit_name", "operational_period", "activity_entries"],
        "sources":    ["radio_entries", "deployments", "personnel"],
        "narrative_fields": [],
    },
    "ics_215": {
        "number":     "ICS-215",
        "title":      "Operational Planning Worksheet",
        "short":      "Planning Worksheet",
        "desc":       "Branch/Division assignments and tactical objectives.",
        "required":   ["operational_period", "branches"],
        "sources":    ["search_segments", "deployments", "incidents"],
        "narrative_fields": ["tactical_objectives"],
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_create_form(incident_id: str, form_key: str) -> dict:
    """Get existing form or create a draft."""
    db  = get_db()
    row = db.execute(
        """SELECT * FROM ics_forms
           WHERE incident_id = ? AND form_key = ?
           ORDER BY version DESC LIMIT 1""",
        (incident_id, form_key)
    ).fetchone()

    if row:
        return row_to_dict(row)

    form_id = str(uuid.uuid4())
    now     = datetime.now(timezone.utc).isoformat()
    db.execute(
        """INSERT INTO ics_forms
           (id, incident_id, form_key, version, status, created_at, updated_at)
           VALUES (?, ?, ?, 1, 'draft', ?, ?)""",
        (form_id, incident_id, form_key, now, now)
    )
    db.commit()
    return row_to_dict(db.execute(
        "SELECT * FROM ics_forms WHERE id = ?", (form_id,)
    ).fetchone())


def _compile_ics201(incident_id: str, db) -> dict:
    incident = row_to_dict(db.execute(
        "SELECT * FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone())

    deployments = rows_to_list(db.execute(
        """SELECT d.*, p.first_name, p.last_name, p.call_sign
           FROM deployments d
           JOIN personnel p ON d.personnel_id = p.id
           WHERE d.incident_id = ?
           ORDER BY d.checked_in_at""",
        (incident_id,)
    ).fetchall())

    return {
        "incident_name":   incident.get("incident_name"),
        "incident_number": incident.get("incident_number"),
        "incident_type":   incident.get("incident_type"),
        "started_at":      incident.get("started_at"),
        "county":          incident.get("county"),
        "state":           incident.get("state"),
        "ic_name":         incident.get("ic_name"),
        "lkp_lat":         incident.get("lkp_lat"),
        "lkp_lng":         incident.get("lkp_lng"),
        "lkp_notes":       incident.get("lkp_notes"),
        "description":     incident.get("description"),
        "resources_summary": [
            {
                "call_sign":  d.get("call_sign"),
                "name":       f"{d.get('first_name')} {d.get('last_name')}",
                "role":       d.get("role"),
                "division":   d.get("division"),
                "checked_in": d.get("checked_in_at"),
            }
            for d in deployments
        ],
        "total_resources": len(deployments),
    }


def _compile_ics204(incident_id: str, db) -> dict:
    deployments = rows_to_list(db.execute(
        """SELECT d.*, p.first_name, p.last_name, p.call_sign,
                  p.phone, p.blood_type
           FROM deployments d
           JOIN personnel p ON d.personnel_id = p.id
           WHERE d.incident_id = ? AND d.status = 'active'
           ORDER BY d.division, d.team, p.call_sign""",
        (incident_id,)
    ).fetchall())

    # Group by division
    divisions = {}
    for dep in deployments:
        div = dep.get("division") or "Unassigned"
        if div not in divisions:
            divisions[div] = []
        divisions[div].append(dep)

    segments = rows_to_list(db.execute(
        "SELECT * FROM search_segments WHERE incident_id = ? ORDER BY segment_id",
        (incident_id,)
    ).fetchall())

    return {
        "divisions":        divisions,
        "division_count":   len(divisions),
        "segments":         segments,
        "total_personnel":  len(deployments),
    }


def _compile_ics205(incident_id: str, db) -> dict:
    # Get unique channels from radio log
    channels = rows_to_list(db.execute(
        """SELECT DISTINCT channel, COUNT(*) as entry_count
           FROM radio_entries
           WHERE incident_id = ? AND channel IS NOT NULL
           GROUP BY channel ORDER BY entry_count DESC""",
        (incident_id,)
    ).fetchall())

    standard = [
        {"channel": "CMD-1", "function": "Command",     "assignment": "IC / Section Chiefs"},
        {"channel": "OPS-1", "function": "Operations",  "assignment": "Field teams"},
        {"channel": "MED-1", "function": "Medical",     "assignment": "Medical personnel"},
        {"channel": "LOG-1", "function": "Logistics",   "assignment": "Logistics section"},
        {"channel": "EMRG",  "function": "Emergency",   "assignment": "MAYDAY / SOS only"},
    ]

    return {
        "channels_in_use":  channels,
        "standard_plan":    standard,
        "channel_count":    len(channels),
    }


def _compile_ics206(incident_id: str, db) -> dict:
    # Find all medical personnel
    medical_certs = ["WFR", "WEMT", "EMT", "EMT-B", "AEMT",
                     "Paramedic", "RN", "MD", "DO", "NP", "PA"]
    placeholders  = ",".join("?" * len(medical_certs))

    medical_personnel = rows_to_list(db.execute(
        f"""SELECT DISTINCT p.first_name, p.last_name, p.call_sign,
                   p.phone, d.division, d.team,
                   GROUP_CONCAT(c.cert_type, ', ') as credentials
            FROM deployments d
            JOIN personnel p ON d.personnel_id = p.id
            JOIN certifications c ON c.personnel_id = p.id
            WHERE d.incident_id = ? AND d.status = 'active'
              AND c.cert_type IN ({placeholders})
            GROUP BY p.id
            ORDER BY p.last_name""",
        [incident_id] + medical_certs
    ).fetchall())

    # Patient summary
    patients = rows_to_list(db.execute(
        """SELECT severity, COUNT(*) as count
           FROM patients WHERE incident_id = ?
           GROUP BY severity""",
        (incident_id,)
    ).fetchall())

    return {
        "medical_personnel": medical_personnel,
        "medical_count":     len(medical_personnel),
        "patient_summary":   patients,
        "aid_stations":      [],  # IC narrative field
        "hospital_routing":  None,  # IC narrative field
    }


def _compile_ics209(incident_id: str, db) -> dict:
    incident = row_to_dict(db.execute(
        "SELECT * FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone())

    total_personnel = db.execute(
        "SELECT COUNT(*) as c FROM deployments WHERE incident_id = ? AND status = 'active'",
        (incident_id,)
    ).fetchone()["c"]

    segments = rows_to_list(db.execute(
        "SELECT * FROM search_segments WHERE incident_id = ?",
        (incident_id,)
    ).fetchall())

    cleared  = sum(1 for s in segments if s["status"] == "cleared")
    assigned = sum(1 for s in segments if s["status"] == "assigned")

    return {
        "incident_name":    incident.get("incident_name"),
        "incident_number":  incident.get("incident_number"),
        "incident_type":    incident.get("incident_type"),
        "started_at":       incident.get("started_at"),
        "ic_name":          incident.get("ic_name"),
        "total_personnel":  total_personnel,
        "segments_total":   len(segments),
        "segments_cleared": cleared,
        "segments_assigned": assigned,
        "lkp_lat":          incident.get("lkp_lat"),
        "lkp_lng":          incident.get("lkp_lng"),
    }


def _compile_ics211(incident_id: str, db) -> dict:
    entries = rows_to_list(db.execute(
        """SELECT d.checked_in_at, d.checked_out_at, d.role,
                  d.division, d.team, d.status,
                  p.first_name, p.last_name, p.call_sign,
                  p.home_agency, p.phone, p.blood_type
           FROM deployments d
           JOIN personnel p ON d.personnel_id = p.id
           WHERE d.incident_id = ?
           ORDER BY d.checked_in_at""",
        (incident_id,)
    ).fetchall())

    return {
        "entries":       entries,
        "total_checked_in": len(entries),
        "still_active":  sum(1 for e in entries if e["status"] == "active"),
    }


def _compile_ics214(incident_id: str, db) -> dict:
    # One log per deployed operator
    deployments = rows_to_list(db.execute(
        """SELECT d.*, p.first_name, p.last_name, p.call_sign
           FROM deployments d
           JOIN personnel p ON d.personnel_id = p.id
           WHERE d.incident_id = ? AND d.status = 'active'""",
        (incident_id,)
    ).fetchall())

    logs = []
    for dep in deployments:
        entries = rows_to_list(db.execute(
            """SELECT message, channel, logged_at, is_missed
               FROM radio_entries
               WHERE incident_id = ? AND personnel_id = ?
               ORDER BY logged_at""",
            (incident_id, dep["personnel_id"])
        ).fetchall())

        logs.append({
            "call_sign":  dep.get("call_sign"),
            "name":       f"{dep.get('first_name')} {dep.get('last_name')}",
            "role":       dep.get("role"),
            "division":   dep.get("division"),
            "entries":    entries,
            "entry_count": len(entries),
        })

    return {"operator_logs": logs, "operator_count": len(logs)}


def _compile_ics215(incident_id: str, db) -> dict:
    segments = rows_to_list(db.execute(
        "SELECT * FROM search_segments WHERE incident_id = ? ORDER BY segment_id",
        (incident_id,)
    ).fetchall())

    deployments = rows_to_list(db.execute(
        """SELECT d.division, d.team, COUNT(*) as personnel_count
           FROM deployments d
           WHERE d.incident_id = ? AND d.status = 'active'
           GROUP BY d.division, d.team""",
        (incident_id,)
    ).fetchall())

    return {
        "segments":    segments,
        "assignments": deployments,
    }


COMPILERS = {
    "ics_201": _compile_ics201,
    "ics_204": _compile_ics204,
    "ics_205": _compile_ics205,
    "ics_206": _compile_ics206,
    "ics_209": _compile_ics209,
    "ics_211": _compile_ics211,
    "ics_214": _compile_ics214,
    "ics_215": _compile_ics215,
}


# ── Form definitions ──────────────────────────────────────────────────────────

@bp.route("/definitions", methods=["GET"])
@require_auth
def get_definitions():
    """Return all ICS form definitions for the LOGBOOK library."""
    return jsonify(ICS_DEFINITIONS), 200


# ── Form status ───────────────────────────────────────────────────────────────

@bp.route("/<incident_id>", methods=["GET"])
@require_auth
def form_status(incident_id):
    """Get the current status of all ICS forms for an incident."""
    db   = get_db()
    rows = db.execute(
        """SELECT form_key, version, status, signed_at, updated_at
           FROM ics_forms WHERE incident_id = ?
           ORDER BY form_key, version DESC""",
        (incident_id,)
    ).fetchall()

    forms = {}
    for row in rows:
        key = row["form_key"]
        if key not in forms:
            forms[key] = row_to_dict(row)
            forms[key]["definition"] = ICS_DEFINITIONS.get(key, {})

    # Add any forms not yet started
    for key, defn in ICS_DEFINITIONS.items():
        if key not in forms:
            forms[key] = {
                "form_key":  key,
                "status":    "not_started",
                "version":   0,
                "signed_at": None,
                "definition": defn,
            }

    return jsonify(forms), 200


# ── Compile forms ─────────────────────────────────────────────────────────────

@bp.route("/<incident_id>/compile", methods=["POST"])
@require_ic
def compile_forms(incident_id):
    """
    Compile all ICS forms for an incident from live database data.
    Runs all compilers, validates required fields, returns
    compiled data and validation summary.
    """
    db = get_db()

    incident = db.execute(
        "SELECT id FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    compiled   = {}
    validation = {}
    now        = datetime.now(timezone.utc).isoformat()

    # Merge any saved narrative fields
    saved_narratives = {}
    saved = db.execute(
        "SELECT form_key, narrative FROM ics_forms WHERE incident_id = ?",
        (incident_id,)
    ).fetchall()
    for s in saved:
        if s["narrative"]:
            try:
                saved_narratives[s["form_key"]] = json.loads(s["narrative"])
            except Exception:
                pass

    for form_key, compiler in COMPILERS.items():
        try:
            data = compiler(incident_id, db)

            # Merge saved narratives
            if form_key in saved_narratives:
                data.update(saved_narratives[form_key])

            compiled[form_key] = data

            # Validate required fields
            defn     = ICS_DEFINITIONS[form_key]
            required = defn.get("required", [])
            missing  = []
            partial  = []

            for field in required:
                val = data.get(field)
                if val is None or val == "" or val == [] or val == {}:
                    missing.append(field)
                elif isinstance(val, list) and len(val) == 0:
                    missing.append(field)

            # Check narrative fields
            narrative_defn = defn.get("narrative_fields", [])
            for field in narrative_defn:
                if not data.get(field):
                    partial.append(field)

            status = "green" if not missing else ("yellow" if not partial else "red")
            if missing:
                status = "red"
            elif partial:
                status = "yellow"
            else:
                status = "green"

            validation[form_key] = {
                "status":          status,
                "missing_required": missing,
                "missing_narrative": partial,
                "ready":           status == "green",
            }

            # Update form record
            form = _get_or_create_form(incident_id, form_key)
            db.execute(
                """UPDATE ics_forms
                   SET data = ?, status = 'compiled', updated_at = ?
                   WHERE id = ?""",
                (json.dumps(data), now, form["id"])
            )

        except Exception as e:
            compiled[form_key]   = {}
            validation[form_key] = {
                "status":  "red",
                "error":   str(e),
                "ready":   False,
            }

    db.commit()

    # Summary
    statuses      = [v["status"] for v in validation.values()]
    red_count     = statuses.count("red")
    yellow_count  = statuses.count("yellow")
    green_count   = statuses.count("green")
    ready_to_sign = red_count == 0

    audit("compile_forms", target_type="incident", target_id=incident_id)
    return jsonify({
        "compiled":   compiled,
        "validation": {
            "forms":        validation,
            "ready_to_sign": ready_to_sign,
            "summary": {
                "red_count":    red_count,
                "yellow_count": yellow_count,
                "green_count":  green_count,
            },
        },
    }), 200


# ── Narrative fields ──────────────────────────────────────────────────────────

@bp.route("/<incident_id>/narrative", methods=["POST"])
@require_ic
def save_narrative(incident_id):
    """
    Save IC narrative fields for a form.
    These are fields that must be manually authored —
    they persist across compilations.
    """
    data     = request.get_json(silent=True) or {}
    form_key = data.get("form")
    fields   = data.get("fields", {})

    if not form_key or form_key not in ICS_DEFINITIONS:
        return jsonify({"error": "Invalid form key"}), 400

    db   = get_db()
    form = _get_or_create_form(incident_id, form_key)

    # Merge with existing narrative
    existing = {}
    if form.get("narrative"):
        try:
            existing = json.loads(form["narrative"])
        except Exception:
            pass

    existing.update(fields)

    db.execute(
        "UPDATE ics_forms SET narrative = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(existing), form["id"])
    )
    db.commit()

    return jsonify({"message": "Narrative saved", "form": form_key}), 200


# ── Sign forms ────────────────────────────────────────────────────────────────

@bp.route("/<incident_id>/sign", methods=["POST"])
@require_ic
def sign_forms(incident_id):
    """
    IC signs all compiled forms for an incident.
    Signed forms are immutable — creates a new version for any
    subsequent changes.
    """
    db = get_db()

    # Only sign forms that are compiled
    forms = db.execute(
        """SELECT id, form_key FROM ics_forms
           WHERE incident_id = ? AND status = 'compiled'""",
        (incident_id,)
    ).fetchall()

    if not forms:
        return jsonify({"error": "No compiled forms to sign"}), 400

    now = datetime.now(timezone.utc).isoformat()

    signed_keys = []
    for form in forms:
        db.execute(
            """UPDATE ics_forms
               SET status = 'signed', signed_at = ?, signed_by = ?
               WHERE id = ?""",
            (now, g.user_id, form["id"])
        )
        signed_keys.append(form["form_key"])

    # Mark incident as signed
    db.execute(
        "UPDATE incidents SET signed_at = ?, signed_by = ? WHERE id = ?",
        (now, g.user_id, incident_id)
    )
    db.commit()

    audit("sign_forms", target_type="incident", target_id=incident_id,
          detail=f"Signed {len(signed_keys)} forms")
    return jsonify({
        "message":    f"{len(signed_keys)} forms signed",
        "signed_by":  g.user_id,
        "signed_at":  now,
        "signed_forms": signed_keys,
    }), 200


# ── Form history ──────────────────────────────────────────────────────────────

@bp.route("/<incident_id>/<form_key>/history", methods=["GET"])
@require_auth
def form_history(incident_id, form_key):
    """Get all versions of a specific form."""
    if form_key not in ICS_DEFINITIONS:
        return jsonify({"error": "Invalid form key"}), 400

    db   = get_db()
    rows = db.execute(
        """SELECT f.*, u.username as signed_by_username
           FROM ics_forms f
           LEFT JOIN users u ON f.signed_by = u.id
           WHERE f.incident_id = ? AND f.form_key = ?
           ORDER BY f.version DESC""",
        (incident_id, form_key)
    ).fetchall()

    versions = rows_to_list(rows)
    for v in versions:
        if v.get("data"):
            try:
                v["data"] = json.loads(v["data"])
            except Exception:
                pass
        if v.get("narrative"):
            try:
                v["narrative"] = json.loads(v["narrative"])
            except Exception:
                pass

    return jsonify({
        "form_key":   form_key,
        "definition": ICS_DEFINITIONS[form_key],
        "versions":   versions,
    }), 200


# ── Export ────────────────────────────────────────────────────────────────────

@bp.route("/<incident_id>/export/json", methods=["GET"])
@require_auth
def export_json(incident_id):
    """Export all signed forms as a JSON packet."""
    db = get_db()

    incident = row_to_dict(db.execute(
        "SELECT * FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone())
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    forms = rows_to_list(db.execute(
        """SELECT form_key, version, data, narrative, status, signed_at
           FROM ics_forms WHERE incident_id = ?
           ORDER BY form_key""",
        (incident_id,)
    ).fetchall())

    packet = {
        "export_type":    "sarpack_ics_packet",
        "version":        "2.0",
        "exported_at":    datetime.now(timezone.utc).isoformat(),
        "incident":       incident,
        "forms":          {},
    }

    for form in forms:
        key  = form["form_key"]
        data = {}
        try:
            data = json.loads(form["data"]) if form.get("data") else {}
        except Exception:
            pass
        narrative = {}
        try:
            narrative = json.loads(form["narrative"]) if form.get("narrative") else {}
        except Exception:
            pass

        packet["forms"][key] = {
            "definition": ICS_DEFINITIONS.get(key, {}),
            "version":    form["version"],
            "status":     form["status"],
            "signed_at":  form["signed_at"],
            "data":       {**data, **narrative},
        }

    return jsonify(packet), 200


# ── LOGBOOK reference data ────────────────────────────────────────────────────

@bp.route("/reference/sar-symbols", methods=["GET"])
@require_auth
def sar_symbols():
    """
    Wilderness SAR and USAR field marking symbols reference.
    Used by the LOGBOOK reference section.
    """
    return jsonify({
        "wilderness_sar": [
            {
                "symbol":      "X",
                "meaning":     "Area searched — negative",
                "usage":       "Mark on map/ground after clearing a segment",
                "color":       "Orange",
            },
            {
                "symbol":      "X (circled)",
                "meaning":     "Area searched — subject found",
                "usage":       "Mark at location of subject discovery",
                "color":       "Orange",
            },
            {
                "symbol":      "→ (arrow)",
                "meaning":     "Direction of travel / route taken",
                "usage":       "Mark direction team is moving",
                "color":       "Orange",
            },
            {
                "symbol":      "? (question mark)",
                "meaning":     "Clue found — investigate",
                "usage":       "Mark location of potential clue",
                "color":       "Orange",
            },
            {
                "symbol":      "L",
                "meaning":     "Last Known Position",
                "usage":       "Mark LKP on the ground",
                "color":       "Red",
            },
            {
                "symbol":      "SOS (ground-to-air)",
                "meaning":     "Need assistance",
                "usage":       "Stomp / lay out in open area for aerial search",
                "color":       "Any high-contrast",
                "size":        "Minimum 10ft letters",
            },
            {
                "symbol":      "I (ground-to-air)",
                "meaning":     "Need medical assistance",
                "usage":       "Ground-to-air signal for medical emergency",
                "color":       "Any high-contrast",
                "size":        "Minimum 10ft",
            },
            {
                "symbol":      "V (ground-to-air)",
                "meaning":     "Need help",
                "usage":       "General distress signal",
                "color":       "Any high-contrast",
            },
            {
                "symbol":      "→ (ground-to-air)",
                "meaning":     "Traveling this direction",
                "usage":       "Indicate direction of travel for aerial",
                "color":       "Any high-contrast",
            },
            {
                "symbol":      "N (ground-to-air)",
                "meaning":     "No / negative",
                "usage":       "Signal negative or no to aerial",
                "color":       "Any high-contrast",
            },
            {
                "symbol":      "Y (ground-to-air)",
                "meaning":     "Yes / affirmative",
                "usage":       "Signal yes or affirmative to aerial",
                "color":       "Any high-contrast",
            },
        ],
        "usar": [
            {
                "symbol":      "Search sector marking (box)",
                "meaning":     "FEMA USAR task force search sector",
                "usage":       "Spray painted on structure face — 18in box",
                "color":       "International Orange",
            },
            {
                "symbol":      "Single slash /",
                "meaning":     "Search in progress",
                "usage":       "Top of box — team entering structure",
                "color":       "International Orange",
            },
            {
                "symbol":      "X (in box)",
                "meaning":     "Search complete",
                "usage":       "After clearing — add team ID and date/time",
                "color":       "International Orange",
                "quadrants": {
                    "top":    "Date and time entered",
                    "left":   "Task force / team designation",
                    "right":  "Hazards found (structural, chemical, etc)",
                    "bottom": "Victims — live (number) / dead (number)",
                },
            },
            {
                "symbol":      "DOG (in box bottom)",
                "meaning":     "Canine search performed",
                "usage":       "Indicates K9 was used in the search",
                "color":       "International Orange",
            },
            {
                "symbol":      "VOID",
                "meaning":     "Survivable void space identified",
                "usage":       "Mark exterior near identified void",
                "color":       "International Orange",
            },
            {
                "symbol":      "Skull and crossbones",
                "meaning":     "Hazardous material present",
                "usage":       "Right quadrant of X box",
                "color":       "International Orange",
            },
        ],
        "hazmat_placards": [
            {"class": "1", "label": "Explosives",      "color": "Orange"},
            {"class": "2", "label": "Gases",           "color": "Green (nonflammable) / Red (flammable)"},
            {"class": "3", "label": "Flammable liquid","color": "Red"},
            {"class": "4", "label": "Flammable solid", "color": "Red/White stripes"},
            {"class": "5", "label": "Oxidizer",        "color": "Yellow"},
            {"class": "6", "label": "Toxic",           "color": "White"},
            {"class": "7", "label": "Radioactive",     "color": "Yellow/White"},
            {"class": "8", "label": "Corrosive",       "color": "Black/White"},
            {"class": "9", "label": "Misc dangerous",  "color": "Black/White stripes"},
        ],
        "source": "FEMA NIMS ICS Field Operations Guide, NASAR FUNSAR, ERG 2024",
    }), 200


@bp.route("/reference/training-links", methods=["GET"])
@require_auth
def training_links():
    """
    FEMA and NASAR training certification links.
    LOGBOOK Click-Train-Certify section.
    """
    return jsonify({
        "fema_ics": [
            {
                "course":  "IS-100.C",
                "title":   "Introduction to Incident Command System",
                "level":   "Basic",
                "url":     "https://training.fema.gov/is/courseoverview.aspx?code=IS-100.c",
                "cert":    "Certificate of Completion",
                "prereqs": "None",
            },
            {
                "course":  "IS-200.C",
                "title":   "Basic Incident Command System for Initial Response",
                "level":   "Basic",
                "url":     "https://training.fema.gov/is/courseoverview.aspx?code=IS-200.c",
                "cert":    "Certificate of Completion",
                "prereqs": "IS-100",
            },
            {
                "course":  "IS-700.B",
                "title":   "An Introduction to NIMS",
                "level":   "Basic",
                "url":     "https://training.fema.gov/is/courseoverview.aspx?code=IS-700.b",
                "cert":    "Certificate of Completion",
                "prereqs": "None",
            },
            {
                "course":  "IS-800.D",
                "title":   "National Response Framework",
                "level":   "Basic",
                "url":     "https://training.fema.gov/is/courseoverview.aspx?code=IS-800.d",
                "cert":    "Certificate of Completion",
                "prereqs": "IS-700",
            },
            {
                "course":  "ICS-300",
                "title":   "Intermediate ICS for Expanding Incidents",
                "level":   "Intermediate",
                "url":     "https://training.fema.gov/icsresource/",
                "cert":    "Certificate of Completion",
                "prereqs": "IS-100, IS-200, IS-700, IS-800",
                "delivery": "Classroom only",
            },
            {
                "course":  "ICS-400",
                "title":   "Advanced ICS — Command and General Staff",
                "level":   "Advanced",
                "url":     "https://training.fema.gov/icsresource/",
                "cert":    "Certificate of Completion",
                "prereqs": "ICS-300",
                "delivery": "Classroom only",
            },
        ],
        "nasar": [
            {
                "course":  "FUNSAR",
                "title":   "Fundamentals of Search and Rescue",
                "level":   "Entry",
                "url":     "https://www.nasar.org/education/funsar/",
                "cert":    "FUNSAR Certificate",
                "prereqs": "None",
            },
            {
                "course":  "SARTECH III",
                "title":   "SAR Technician III",
                "level":   "Entry",
                "url":     "https://www.nasar.org/education/sartech/",
                "cert":    "SARTECH III",
                "prereqs": "FUNSAR",
            },
            {
                "course":  "SARTECH II",
                "title":   "SAR Technician II",
                "level":   "Intermediate",
                "url":     "https://www.nasar.org/education/sartech/",
                "cert":    "SARTECH II",
                "prereqs": "SARTECH III",
            },
            {
                "course":  "SARTECH I",
                "title":   "SAR Technician I",
                "level":   "Advanced",
                "url":     "https://www.nasar.org/education/sartech/",
                "cert":    "SARTECH I",
                "prereqs": "SARTECH II",
            },
        ],
        "medical": [
            {
                "course":  "WFA",
                "title":   "Wilderness First Aid",
                "level":   "Entry",
                "url":     "https://www.nols.edu/en/coursefinder/courses/wilderness-first-aid-WFA/",
                "cert":    "WFA Certificate (2 year)",
                "prereqs": "None",
            },
            {
                "course":  "WFR",
                "title":   "Wilderness First Responder",
                "level":   "Intermediate",
                "url":     "https://www.nols.edu/en/coursefinder/courses/wilderness-first-responder-WFR/",
                "cert":    "WFR Certificate (2 year)",
                "prereqs": "None",
            },
            {
                "course":  "WEMT",
                "title":   "Wilderness Emergency Medical Technician",
                "level":   "Advanced",
                "url":     "https://www.nols.edu/en/coursefinder/courses/wilderness-emt-WEMT/",
                "cert":    "WEMT Certificate (2 year)",
                "prereqs": "EMT-B",
            },
        ],
        "fema_portal": "https://training.fema.gov/is/",
        "nasar_portal": "https://www.nasar.org/education/",
    }), 200