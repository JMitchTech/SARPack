"""
SARPack — logbook/compiler.py
Form compiler. Takes an incident_id and queries every relevant table
to produce a structured data object for each ICS form.

This is the single source of truth for what goes into each form.
The validator checks the output of this module.
The generator renders it to PDF.

All eight forms are compiled in one pass — one incident_id lookup,
one coordinated set of queries, one structured result object.
"""

import json
import logging
from datetime import datetime, timezone
from core.db import local_db, get_record

log = logging.getLogger("logbook.compiler")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compile_incident(incident_id: str) -> dict:
    """
    Compile all form data for an incident.
    Returns a dict with keys for each ICS form plus shared incident data.
    Raises ValueError if the incident does not exist.

    Structure:
    {
        "incident":   {...},          # core incident record
        "personnel":  [...],          # all deployed personnel with certs
        "ics_201":    {...},          # ICS-201 field data
        "ics_204":    [...],          # list of ICS-204 (one per division)
        "ics_205":    {...},
        "ics_206":    {...},
        "ics_209":    {...},
        "ics_211":    [...],          # full check-in list
        "ics_214":    [...],          # list of ICS-214 (one per operator)
        "ics_215":    {...},
        "compiled_at": "ISO8601",
    }
    """
    incident = get_record("incidents", incident_id)
    if not incident:
        raise ValueError(f"Incident {incident_id} not found")

    log.info("Compiling forms for incident %s — %s",
             incident["incident_number"], incident["incident_name"])

    with local_db() as db:
        # All deployments with personnel detail and certifications
        deployments = db.execute(
            """
            SELECT d.id as deployment_id, d.role, d.division, d.team,
                   d.checked_in_at, d.checked_out_at, d.status,
                   p.id as personnel_id, p.first_name, p.last_name,
                   p.call_sign, p.phone, p.blood_type,
                   p.emergency_contact_name, p.emergency_contact_phone,
                   p.allergies, p.medical_notes,
                   GROUP_CONCAT(c.cert_type, '|') as cert_types,
                   GROUP_CONCAT(c.cert_number, '|') as cert_numbers,
                   GROUP_CONCAT(c.expiry_date, '|') as cert_expiries
            FROM deployments d
            JOIN personnel p ON p.id = d.personnel_id
            LEFT JOIN certifications c ON c.personnel_id = p.id
            WHERE d.incident_id = ?
            GROUP BY d.id
            ORDER BY d.checked_in_at ASC
            """,
            (incident_id,),
        ).fetchall()
        deployments = [dict(r) for r in deployments]

        # Parse concatenated cert fields into lists
        for dep in deployments:
            cert_types   = dep.pop("cert_types",   "") or ""
            cert_numbers = dep.pop("cert_numbers", "") or ""
            cert_expiries = dep.pop("cert_expiries", "") or ""
            dep["certifications"] = [
                {"cert_type": t, "cert_number": n, "expiry_date": e}
                for t, n, e in zip(
                    cert_types.split("|"),
                    cert_numbers.split("|"),
                    cert_expiries.split("|"),
                )
                if t
            ]

        # Search segments
        segments = db.execute(
            "SELECT * FROM search_segments WHERE incident_id = ? ORDER BY segment_id",
            (incident_id,),
        ).fetchall()
        segments = [dict(r) for r in segments]

        # Radio log
        radio_log = db.execute(
            """
            SELECT r.*, p.call_sign, p.first_name, p.last_name
            FROM radio_log r
            LEFT JOIN personnel p ON p.id = r.personnel_id
            WHERE r.incident_id = ?
            ORDER BY r.logged_at ASC
            """,
            (incident_id,),
        ).fetchall()
        radio_log = [dict(r) for r in radio_log]

        # Incident commander detail
        commander = None
        if incident.get("incident_commander_id"):
            commander = get_record("personnel", incident["incident_commander_id"])

        # Existing ICS form records (for version tracking and signed status)
        existing_forms = {}
        for form in ("ics_201","ics_204","ics_205","ics_206",
                     "ics_209","ics_214","ics_215"):
            rows = db.execute(
                f"SELECT * FROM {form} WHERE incident_id = ? ORDER BY version DESC LIMIT 1",
                (incident_id,),
            ).fetchone()
            existing_forms[form] = dict(rows) if rows else None

        # ICS-211 is append-only — no version column
        ics_211_row = db.execute(
            "SELECT * FROM ics_211 WHERE incident_id = ? LIMIT 1",
            (incident_id,),
        ).fetchone()
        existing_forms["ics_211"] = dict(ics_211_row) if ics_211_row else None

    compiled_at = datetime.now(timezone.utc).isoformat()

    return {
        "incident":    incident,
        "commander":   commander,
        "deployments": deployments,
        "segments":    segments,
        "radio_log":   radio_log,
        "existing_forms": existing_forms,
        "compiled_at": compiled_at,

        "ics_201": _compile_201(incident, deployments, segments, commander, existing_forms.get("ics_201")),
        "ics_204": _compile_204(incident, deployments, existing_forms.get("ics_204")),
        "ics_205": _compile_205(incident, radio_log, existing_forms.get("ics_205")),
        "ics_206": _compile_206(incident, deployments, commander, existing_forms.get("ics_206")),
        "ics_209": _compile_209(incident, deployments, segments, existing_forms.get("ics_209")),
        "ics_211": _compile_211(incident, deployments),
        "ics_214": _compile_214(incident, deployments, radio_log),
        "ics_215": _compile_215(incident, segments, deployments, existing_forms.get("ics_215")),
    }


# ---------------------------------------------------------------------------
# ICS-201: Incident Briefing
# Auto-populated: incident name, number, type, commander, location,
# start time, resource summary, current situation outline
# ---------------------------------------------------------------------------

def _compile_201(incident, deployments, segments, commander, existing):
    active = [d for d in deployments if d["status"] == "active"]
    total  = len(deployments)

    resource_summary = {
        "total_personnel":     total,
        "active_personnel":    len(active),
        "search_segments":     len(segments),
        "segments_cleared":    sum(1 for s in segments if s["status"] == "cleared"),
        "segments_assigned":   sum(1 for s in segments if s["status"] == "assigned"),
    }

    return {
        "incident_name":     incident["incident_name"],
        "incident_number":   incident["incident_number"],
        "incident_type":     incident["incident_type"],
        "date_initiated":    incident["started_at"][:10] if incident.get("started_at") else "",
        "time_initiated":    incident["started_at"][11:16] if incident.get("started_at") else "",
        "location":          f"{incident.get('county','')}, {incident.get('state','')}".strip(", "),
        "lat":               incident.get("lat"),
        "lng":               incident.get("lng"),
        "incident_commander": _person_name(commander),
        "ic_phone":          commander.get("phone") if commander else "",
        "resource_summary":  resource_summary,
        # Narrative fields — must be filled by IC
        "situation_summary": existing.get("situation_summary", "") if existing else "",
        "initial_objectives": existing.get("initial_objectives", "") if existing else "",
        "current_actions":   existing.get("current_actions", "") if existing else "",
        "signed_by":         existing.get("signed_by") if existing else None,
        "signed_at":         existing.get("signed_at") if existing else None,
        "version":           existing.get("version", 1) if existing else 1,
        "existing_id":       existing.get("id") if existing else None,
    }


# ---------------------------------------------------------------------------
# ICS-204: Assignment List
# One record per division/group — auto-populated from deployments
# ---------------------------------------------------------------------------

def _compile_204(incident, deployments, existing):
    # Group deployments by division
    divisions = {}
    for dep in deployments:
        div = dep.get("division") or "Unassigned"
        if div not in divisions:
            divisions[div] = []
        divisions[div].append(dep)

    result = []
    for div_name, members in sorted(divisions.items()):
        # Find supervisor (first person with a leadership role)
        supervisor = next(
            (m for m in members if any(
                kw in m.get("role","").lower()
                for kw in ("leader","chief","supervisor","commander","officer")
            )),
            members[0] if members else None,
        )

        result.append({
            "incident_name":      incident["incident_name"],
            "incident_number":    incident["incident_number"],
            "operational_period": _op_period(incident),
            "division":           div_name,
            "supervisor":         _person_name(supervisor) if supervisor else "",
            "supervisor_phone":   supervisor.get("phone","") if supervisor else "",
            "resources": [
                {
                    "name":      f"{m['first_name']} {m['last_name']}",
                    "call_sign": m.get("call_sign",""),
                    "role":      m.get("role",""),
                    "team":      m.get("team",""),
                }
                for m in members
            ],
            "special_instructions": existing.get("special_instructions","") if existing else "",
            "signed_by":  existing.get("signed_by") if existing else None,
            "signed_at":  existing.get("signed_at") if existing else None,
            "existing_id": existing.get("id") if existing else None,
        })

    return result


# ---------------------------------------------------------------------------
# ICS-205: Radio Communications Plan
# Auto-populated from radio_log channel usage
# ---------------------------------------------------------------------------

def _compile_205(incident, radio_log, existing):
    # Extract unique channels from radio log
    channels = {}
    for entry in radio_log:
        ch = entry.get("channel")
        if ch and ch not in channels:
            channels[ch] = {
                "channel_name": ch,
                "function":     "Tactical",
                "frequency":    "",   # must be filled by IC — LOGBOOK can't know this
                "remarks":      "",
            }

    return {
        "incident_name":      incident["incident_name"],
        "incident_number":    incident["incident_number"],
        "operational_period": _op_period(incident),
        "channel_assignments": list(channels.values()),
        "special_instructions": existing.get("special_instructions","") if existing else "",
        "prepared_at":        existing.get("prepared_at","") if existing else "",
        "signed_by":          existing.get("signed_by") if existing else None,
        "signed_at":          existing.get("signed_at") if existing else None,
        "existing_id":        existing.get("id") if existing else None,
    }


# ---------------------------------------------------------------------------
# ICS-206: Medical Plan
# Auto-populated from certifications — WFR, EMT, Paramedic, etc.
# ---------------------------------------------------------------------------

MEDICAL_CERT_TYPES = ("WFR", "WEMT", "EMT", "Paramedic", "RN", "MD")

def _compile_206(incident, deployments, commander, existing):
    medical_personnel = []
    for dep in deployments:
        med_certs = [
            c for c in dep.get("certifications", [])
            if c["cert_type"] in MEDICAL_CERT_TYPES
        ]
        if med_certs:
            medical_personnel.append({
                "name":      f"{dep['first_name']} {dep['last_name']}",
                "call_sign": dep.get("call_sign",""),
                "phone":     dep.get("phone",""),
                "role":      dep.get("role",""),
                "division":  dep.get("division",""),
                "certs":     med_certs,
            })

    return {
        "incident_name":      incident["incident_name"],
        "incident_number":    incident["incident_number"],
        "operational_period": _op_period(incident),
        "medical_personnel":  medical_personnel,
        # Hospitals and aid stations require local knowledge — IC fills these
        "medical_aid_stations": _parse_json(existing.get("medical_aid_stations")) if existing else [],
        "hospitals":          _parse_json(existing.get("hospitals")) if existing else [],
        "medical_officer":    _person_name(commander),
        "signed_by":          existing.get("signed_by") if existing else None,
        "signed_at":          existing.get("signed_at") if existing else None,
        "existing_id":        existing.get("id") if existing else None,
    }


# ---------------------------------------------------------------------------
# ICS-209: Incident Status Summary
# Auto-populated from incident + deployment + segment totals
# ---------------------------------------------------------------------------

def _compile_209(incident, deployments, segments, existing):
    active_deps = [d for d in deployments if d["status"] == "active"]

    return {
        "incident_name":      incident["incident_name"],
        "incident_number":    incident["incident_number"],
        "incident_type":      incident["incident_type"],
        "state":              incident.get("state","PA"),
        "county":             incident.get("county",""),
        "operational_period": _op_period(incident),
        "incident_phase":     existing.get("incident_phase","Initial Attack") if existing else "Initial Attack",
        "total_personnel":    len(deployments),
        "active_personnel":   len(active_deps),
        "total_segments":     len(segments),
        "cleared_segments":   sum(1 for s in segments if s["status"] == "cleared"),
        "assigned_segments":  sum(1 for s in segments if s["status"] == "assigned"),
        # Narrative — IC fills these
        "current_situation":  existing.get("current_situation","") if existing else "",
        "primary_mission":    existing.get("primary_mission","") if existing else "",
        "planned_actions":    existing.get("planned_actions","") if existing else "",
        "signed_by":          existing.get("signed_by") if existing else None,
        "signed_at":          existing.get("signed_at") if existing else None,
        "existing_id":        existing.get("id") if existing else None,
    }


# ---------------------------------------------------------------------------
# ICS-211: Check-In/Check-Out List
# Fully auto-populated from deployments — 100% automatic
# ---------------------------------------------------------------------------

def _compile_211(incident, deployments):
    return {
        "incident_name":   incident["incident_name"],
        "incident_number": incident["incident_number"],
        "entries": [
            {
                "name":           f"{d['first_name']} {d['last_name']}",
                "call_sign":      d.get("call_sign",""),
                "role":           d.get("role",""),
                "division":       d.get("division",""),
                "team":           d.get("team",""),
                "check_in_time":  d.get("checked_in_at",""),
                "check_out_time": d.get("checked_out_at",""),
                "status":         d.get("status",""),
                "home_agency":    "Keystone Rescue Service",
                "resource_type":  "Personnel",
            }
            for d in deployments
        ],
    }


# ---------------------------------------------------------------------------
# ICS-214: Activity Log
# One record per deployed operator — auto-populated from radio_log
# ---------------------------------------------------------------------------

def _compile_214(incident, deployments, radio_log):
    result = []
    for dep in deployments:
        # Filter radio log entries for this operator
        entries = [
            {
                "time":    r.get("logged_at","")[:16],
                "notable": r.get("message",""),
            }
            for r in radio_log
            if r.get("personnel_id") == dep["personnel_id"]
        ]

        result.append({
            "incident_name":      incident["incident_name"],
            "incident_number":    incident["incident_number"],
            "operational_period": _op_period(incident),
            "unit_name":          dep.get("team") or dep.get("division") or "Unassigned",
            "operator_name":      f"{dep['first_name']} {dep['last_name']}",
            "operator_call_sign": dep.get("call_sign",""),
            "role":               dep.get("role",""),
            "activity_entries":   entries,
        })

    return result


# ---------------------------------------------------------------------------
# ICS-215: Operational Planning Worksheet
# Auto-populated from segments and divisions
# ---------------------------------------------------------------------------

def _compile_215(incident, segments, deployments, existing):
    # Build division summary
    divisions = {}
    for dep in deployments:
        div = dep.get("division") or "Unassigned"
        if div not in divisions:
            divisions[div] = {"personnel": [], "teams": set()}
        divisions[div]["personnel"].append(f"{dep['first_name']} {dep['last_name']}")
        if dep.get("team"):
            divisions[div]["teams"].add(dep["team"])

    div_list = [
        {
            "division":  name,
            "personnel_count": len(data["personnel"]),
            "teams":     list(data["teams"]),
        }
        for name, data in sorted(divisions.items())
    ]

    tactical_objectives = [
        {
            "segment_id": s["segment_id"],
            "objective":  f"Search segment {s['segment_id']}",
            "team":       s.get("assigned_team","Unassigned"),
            "status":     s["status"],
            "pod":        s.get("probability_of_detection", 0),
        }
        for s in segments
    ]

    return {
        "incident_name":       incident["incident_name"],
        "incident_number":     incident["incident_number"],
        "operational_period":  _op_period(incident),
        "divisions":           div_list,
        "tactical_objectives": tactical_objectives,
        "support_requirements": existing.get("support_requirements","") if existing else "",
        "signed_by":           existing.get("signed_by") if existing else None,
        "signed_at":           existing.get("signed_at") if existing else None,
        "existing_id":         existing.get("id") if existing else None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _person_name(person: dict | None) -> str:
    if not person:
        return ""
    return f"{person.get('first_name','')} {person.get('last_name','')}".strip()


def _op_period(incident: dict) -> str:
    """Best-effort operational period string."""
    started = incident.get("started_at","")
    if started:
        return f"From: {started[:16]}"
    return ""


def _parse_json(value) -> list:
    """Safely parse a JSON string or return empty list."""
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except Exception:
        return []
