"""
SARPack — logbook/validator.py
Compliance validator. Checks compiled form data against ICS field
requirements and returns a structured report of missing or incomplete fields.

Three severity levels:
  RED   — required field, missing or empty. Blocks export until resolved.
  YELLOW — recommended field, missing. IC should review but can still export.
  GREEN  — field is present and populated.

The IC sign-off gate in forms.py checks that zero RED fields exist
before allowing the signature endpoint to proceed.
"""

import logging
from datetime import date

log = logging.getLogger("logbook.validator")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def validate(compiled: dict) -> dict:
    """
    Validate all compiled form data.
    Returns a validation report dict:
    {
        "ready_to_sign": bool,   # True if zero RED fields across all forms
        "forms": {
            "ics_201": {
                "status": "red"|"yellow"|"green",
                "fields": [{"field": str, "label": str, "status": str, "message": str}]
            },
            ...
        },
        "summary": {
            "red_count": int,
            "yellow_count": int,
            "green_count": int,
        }
    }
    """
    report = {}

    report["ics_201"] = _validate_201(compiled.get("ics_201", {}))
    report["ics_204"] = _validate_204(compiled.get("ics_204", []))
    report["ics_205"] = _validate_205(compiled.get("ics_205", {}))
    report["ics_206"] = _validate_206(compiled.get("ics_206", {}))
    report["ics_209"] = _validate_209(compiled.get("ics_209", {}))
    report["ics_211"] = _validate_211(compiled.get("ics_211", {}))
    report["ics_214"] = _validate_214(compiled.get("ics_214", []))
    report["ics_215"] = _validate_215(compiled.get("ics_215", {}))

    red_count    = sum(1 for f in report.values() if f["status"] == "red")
    yellow_count = sum(1 for f in report.values() if f["status"] == "yellow")
    green_count  = sum(1 for f in report.values() if f["status"] == "green")

    return {
        "ready_to_sign": red_count == 0,
        "forms":   report,
        "summary": {
            "red_count":    red_count,
            "yellow_count": yellow_count,
            "green_count":  green_count,
        },
    }


# ---------------------------------------------------------------------------
# Per-form validators
# ---------------------------------------------------------------------------

def _validate_201(data: dict) -> dict:
    fields = []
    _req(fields, data, "incident_name",     "Incident name")
    _req(fields, data, "incident_number",   "Incident number")
    _req(fields, data, "date_initiated",    "Date initiated")
    _req(fields, data, "time_initiated",    "Time initiated")
    _req(fields, data, "location",          "Incident location")
    _req(fields, data, "incident_commander","Incident commander name")
    _req(fields, data, "situation_summary", "Situation summary")
    _req(fields, data, "initial_objectives","Initial objectives")
    _rec(fields, data, "current_actions",   "Current actions taken")
    _rec(fields, data, "ic_phone",          "IC phone number")
    return _form_result(fields)


def _validate_204(data: list) -> dict:
    if not data:
        return _form_result([_field_result(
            "divisions", "Division assignments",
            "red", "No divisions found — check in personnel first"
        )])
    fields = []
    for i, div in enumerate(data):
        prefix = f"Division {div.get('division','?')}"
        _req_in(fields, div, "division",           f"{prefix}: Division name")
        _req_in(fields, div, "operational_period", f"{prefix}: Operational period")
        if not div.get("resources"):
            fields.append(_field_result(
                f"resources_{i}", f"{prefix}: Assigned resources",
                "red", "No personnel assigned to this division"
            ))
    return _form_result(fields)


def _validate_205(data: dict) -> dict:
    fields = []
    _req(fields, data, "incident_name",      "Incident name")
    _req(fields, data, "operational_period", "Operational period")
    channels = data.get("channel_assignments", [])
    if not channels:
        fields.append(_field_result(
            "channel_assignments", "Channel assignments",
            "yellow", "No radio channels logged — IC should add manually"
        ))
    else:
        for ch in channels:
            if not ch.get("frequency"):
                fields.append(_field_result(
                    f"freq_{ch['channel_name']}", f"Frequency for {ch['channel_name']}",
                    "yellow", "Frequency not set — IC must fill in"
                ))
    return _form_result(fields)


def _validate_206(data: dict) -> dict:
    fields = []
    _req(fields, data, "incident_name",      "Incident name")
    _req(fields, data, "operational_period", "Operational period")
    if not data.get("medical_personnel"):
        fields.append(_field_result(
            "medical_personnel", "Medical personnel",
            "yellow", "No personnel with medical certifications deployed"
        ))
    if not data.get("hospitals"):
        fields.append(_field_result(
            "hospitals", "Nearest hospitals",
            "red", "Hospital information required for medical plan"
        ))
    if not data.get("medical_aid_stations"):
        fields.append(_field_result(
            "medical_aid_stations", "Medical aid stations",
            "yellow", "No aid stations defined — IC should add if applicable"
        ))
    return _form_result(fields)


def _validate_209(data: dict) -> dict:
    fields = []
    _req(fields, data, "incident_name",      "Incident name")
    _req(fields, data, "incident_number",    "Incident number")
    _req(fields, data, "incident_type",      "Incident type")
    _req(fields, data, "county",             "County")
    _req(fields, data, "state",              "State")
    _req(fields, data, "operational_period", "Operational period")
    _req(fields, data, "current_situation",  "Current situation narrative")
    _req(fields, data, "primary_mission",    "Primary mission statement")
    _rec(fields, data, "planned_actions",    "Planned actions")
    _rec(fields, data, "incident_phase",     "Incident phase")
    return _form_result(fields)


def _validate_211(data: dict) -> dict:
    fields = []
    _req(fields, data, "incident_name",   "Incident name")
    _req(fields, data, "incident_number", "Incident number")
    entries = data.get("entries", [])
    if not entries:
        fields.append(_field_result(
            "entries", "Check-in entries",
            "red", "No personnel have been checked in to this incident"
        ))
    else:
        for i, entry in enumerate(entries):
            if not entry.get("check_in_time"):
                fields.append(_field_result(
                    f"checkin_{i}", f"{entry.get('name','?')} check-in time",
                    "yellow", "Check-in time not recorded"
                ))
    return _form_result(fields)


def _validate_214(data: list) -> dict:
    if not data:
        return _form_result([_field_result(
            "operators", "Operator activity logs",
            "yellow", "No deployed operators — check in personnel first"
        )])
    fields = []
    for op in data:
        name = op.get("operator_name","?")
        _req_in(fields, op, "incident_name",      f"{name}: Incident name")
        _req_in(fields, op, "operational_period",  f"{name}: Operational period")
        if not op.get("activity_entries"):
            fields.append(_field_result(
                f"entries_{name}", f"{name}: Activity entries",
                "yellow", "No radio log entries for this operator"
            ))
    return _form_result(fields)


def _validate_215(data: dict) -> dict:
    fields = []
    _req(fields, data, "incident_name",      "Incident name")
    _req(fields, data, "operational_period", "Operational period")
    if not data.get("divisions"):
        fields.append(_field_result(
            "divisions", "Branch/division assignments",
            "red", "No divisions defined — assign personnel to divisions in BASECAMP"
        ))
    if not data.get("tactical_objectives"):
        fields.append(_field_result(
            "tactical_objectives", "Tactical objectives",
            "yellow", "No search segments defined"
        ))
    return _form_result(fields)


# ---------------------------------------------------------------------------
# Field check helpers
# ---------------------------------------------------------------------------

def _req(fields: list, data: dict, key: str, label: str):
    """Required field check."""
    value = data.get(key)
    if not value and value != 0:
        fields.append(_field_result(key, label, "red", f"{label} is required"))
    else:
        fields.append(_field_result(key, label, "green", ""))


def _rec(fields: list, data: dict, key: str, label: str):
    """Recommended field check."""
    value = data.get(key)
    if not value and value != 0:
        fields.append(_field_result(key, label, "yellow", f"{label} is recommended"))
    else:
        fields.append(_field_result(key, label, "green", ""))


def _req_in(fields: list, data: dict, key: str, label: str):
    """Required field check for nested dicts."""
    _req(fields, data, key, label)


def _field_result(field: str, label: str, status: str, message: str) -> dict:
    return {"field": field, "label": label, "status": status, "message": message}


def _form_result(fields: list) -> dict:
    """Determine overall form status from its field results."""
    statuses = [f["status"] for f in fields]
    if "red" in statuses:
        overall = "red"
    elif "yellow" in statuses:
        overall = "yellow"
    else:
        overall = "green"
    return {"status": overall, "fields": fields}
