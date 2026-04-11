"""
SARPack — logbook/generator.py
ReportLab PDF generator. Renders compiled form data into PDFs
that match FEMA's official ICS form layouts as closely as possible.

Each ICS form has its own render function. All forms share:
- Standard letter paper (8.5" x 11")
- ICS header block (form number, incident name/number, op period)
- Footer with page number and prepared-by line
- Signature block at the bottom of the last page

Fonts: Helvetica (standard, no install required)
Colors: FEMA-style — black text, light gray headers, white fields
"""

import io
import logging
from datetime import datetime, timezone

log = logging.getLogger("logbook.generator")

# ReportLab imports — guarded so the module loads even without ReportLab
# installed (validator and compiler still work)
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, HRFlowable,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

GRAY_DARK   = colors.HexColor("#333333")
GRAY_MID    = colors.HexColor("#666666")
GRAY_LIGHT  = colors.HexColor("#f0f0f0")
GRAY_BORDER = colors.HexColor("#cccccc")
BLACK       = colors.black
WHITE       = colors.white
HEADER_BG   = colors.HexColor("#1a3a5c")   # dark navy — ICS standard
HEADER_FG   = colors.white


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _get_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        "FormTitle",
        fontName="Helvetica-Bold",
        fontSize=14,
        textColor=HEADER_FG,
        alignment=TA_CENTER,
        spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        "FormSubtitle",
        fontName="Helvetica",
        fontSize=9,
        textColor=HEADER_FG,
        alignment=TA_CENTER,
        spaceAfter=0,
    ))
    styles.add(ParagraphStyle(
        "SectionHeader",
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=WHITE,
        backColor=GRAY_DARK,
        alignment=TA_LEFT,
        leftIndent=4,
        spaceAfter=0,
        spaceBefore=6,
    ))
    styles.add(ParagraphStyle(
        "FieldLabel",
        fontName="Helvetica-Bold",
        fontSize=8,
        textColor=GRAY_MID,
        spaceAfter=1,
    ))
    styles.add(ParagraphStyle(
        "FieldValue",
        fontName="Helvetica",
        fontSize=9,
        textColor=GRAY_DARK,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        "Body",
        fontName="Helvetica",
        fontSize=9,
        textColor=GRAY_DARK,
        leading=13,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        "TableHeader",
        fontName="Helvetica-Bold",
        fontSize=8,
        textColor=WHITE,
        alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        "TableCell",
        fontName="Helvetica",
        fontSize=8,
        textColor=GRAY_DARK,
    ))
    styles.add(ParagraphStyle(
        "SignatureLine",
        fontName="Helvetica",
        fontSize=8,
        textColor=GRAY_MID,
        alignment=TA_LEFT,
    ))

    return styles


# ---------------------------------------------------------------------------
# Shared page elements
# ---------------------------------------------------------------------------

def _ics_header(elements, styles, form_number: str, form_title: str,
                incident_name: str, incident_number: str, op_period: str):
    """Standard ICS form header block."""
    header_data = [[
        Paragraph(f"ICS {form_number}", styles["FormTitle"]),
        Paragraph(form_title, styles["FormSubtitle"]),
    ]]
    header_table = Table(header_data, colWidths=[1.5*inch, 5.5*inch])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), HEADER_BG),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING",   (0,0), (0,-1), 8),
    ]))
    elements.append(header_table)

    info_data = [[
        _label_value("Incident name", incident_name),
        _label_value("Incident number", incident_number),
        _label_value("Operational period", op_period),
    ]]
    info_table = Table(info_data, colWidths=[2.5*inch, 2*inch, 2.5*inch])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), GRAY_LIGHT),
        ("BOX",        (0,0), (-1,-1), 0.5, GRAY_BORDER),
        ("INNERGRID",  (0,0), (-1,-1), 0.5, GRAY_BORDER),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 0.1*inch))


def _signature_block(elements, styles, signed_by: str, signed_at: str,
                     role: str = "Incident Commander"):
    """Standard IC signature block."""
    elements.append(Spacer(1, 0.2*inch))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=GRAY_BORDER))

    sig_label = f"Approved and signed by: {role}"
    sig_value = f"{signed_by}  —  {_fmt_ts(signed_at)}" if signed_by else "_" * 50

    sig_data = [[
        Paragraph(sig_label, styles["FieldLabel"]),
        Paragraph(sig_value, styles["FieldValue"]),
        Paragraph(f"Date/time: {_fmt_ts(signed_at) if signed_at else '___________'}", styles["SignatureLine"]),
    ]]
    sig_table = Table(sig_data, colWidths=[2*inch, 3*inch, 2*inch])
    sig_table.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "BOTTOM"),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    elements.append(sig_table)


def _label_value(label: str, value: str) -> Paragraph:
    return Paragraph(f"<b>{label}:</b> {value or '—'}", ParagraphStyle(
        "lv", fontName="Helvetica", fontSize=8, textColor=GRAY_DARK,
    ))


def _section_header(elements, styles, text: str):
    data = [[Paragraph(text, styles["SectionHeader"])]]
    t = Table(data, colWidths=[7*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), GRAY_DARK),
        ("TOPPADDING",    (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
    ]))
    elements.append(t)


def _data_table(elements, headers: list, rows: list, col_widths: list):
    """Render a standard ICS data table."""
    style = ParagraphStyle("tc", fontName="Helvetica", fontSize=8, textColor=GRAY_DARK)
    hdr_style = ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8, textColor=WHITE)

    table_data = [[Paragraph(h, hdr_style) for h in headers]]
    for row in rows:
        table_data.append([Paragraph(str(c or ""), style) for c in row])

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  HEADER_BG),
        ("BACKGROUND",    (0,1), (-1,-1), WHITE),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, GRAY_LIGHT]),
        ("BOX",           (0,0), (-1,-1), 0.5, GRAY_BORDER),
        ("INNERGRID",     (0,0), (-1,-1), 0.5, GRAY_BORDER),
        ("TOPPADDING",    (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING",   (0,0), (-1,-1), 4),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 0.1*inch))


# ---------------------------------------------------------------------------
# Per-form renderers
# ---------------------------------------------------------------------------

def render_201(data: dict) -> bytes:
    styles = _get_styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.5*inch, bottomMargin=0.75*inch)
    elements = []

    _ics_header(elements, styles, "201", "Incident Briefing",
                data.get("incident_name",""), data.get("incident_number",""),
                data.get("date_initiated","") + " " + data.get("time_initiated",""))

    # Map / location section
    _section_header(elements, styles, "1. Incident location")
    loc_data = [[
        _label_value("Location", data.get("location","")),
        _label_value("Latitude", str(data.get("lat",""))),
        _label_value("Longitude", str(data.get("lng",""))),
    ]]
    loc_t = Table(loc_data, colWidths=[3*inch, 2*inch, 2*inch])
    loc_t.setStyle(TableStyle([
        ("BOX",        (0,0), (-1,-1), 0.5, GRAY_BORDER),
        ("INNERGRID",  (0,0), (-1,-1), 0.5, GRAY_BORDER),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
    ]))
    elements.append(loc_t)
    elements.append(Spacer(1, 0.1*inch))

    # Incident commander
    _section_header(elements, styles, "2. Incident commander")
    ic_data = [[
        _label_value("Name", data.get("incident_commander","")),
        _label_value("Phone", data.get("ic_phone","")),
    ]]
    ic_t = Table(ic_data, colWidths=[4*inch, 3*inch])
    ic_t.setStyle(TableStyle([
        ("BOX",        (0,0), (-1,-1), 0.5, GRAY_BORDER),
        ("INNERGRID",  (0,0), (-1,-1), 0.5, GRAY_BORDER),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
    ]))
    elements.append(ic_t)
    elements.append(Spacer(1, 0.1*inch))

    # Resource summary
    _section_header(elements, styles, "3. Resource summary")
    rs = data.get("resource_summary", {})
    rs_data = [
        [_label_value("Total personnel", str(rs.get("total_personnel",0))),
         _label_value("Active personnel", str(rs.get("active_personnel",0))),
         _label_value("Search segments", str(rs.get("search_segments",0))),
         _label_value("Segments cleared", str(rs.get("segments_cleared",0)))],
    ]
    rs_t = Table(rs_data, colWidths=[1.75*inch]*4)
    rs_t.setStyle(TableStyle([
        ("BOX",        (0,0), (-1,-1), 0.5, GRAY_BORDER),
        ("INNERGRID",  (0,0), (-1,-1), 0.5, GRAY_BORDER),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
    ]))
    elements.append(rs_t)
    elements.append(Spacer(1, 0.1*inch))

    # Narrative fields
    for section_num, key, label in [
        ("4", "situation_summary",  "Current situation"),
        ("5", "initial_objectives", "Initial response objectives"),
        ("6", "current_actions",    "Current and planned actions"),
    ]:
        _section_header(elements, styles, f"{section_num}. {label}")
        text = data.get(key, "") or "(Not completed)"
        elements.append(Paragraph(text, styles["Body"]))
        elements.append(Spacer(1, 0.05*inch))

    _signature_block(elements, styles,
                     data.get("signed_by",""), data.get("signed_at",""))
    doc.build(elements)
    return buf.getvalue()


def render_211(data: dict) -> bytes:
    """ICS-211 Check-In/Check-Out List — fully automatic."""
    styles = _get_styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.5*inch, bottomMargin=0.75*inch)
    elements = []

    _ics_header(elements, styles, "211", "Incident Check-In/Check-Out List",
                data.get("incident_name",""), data.get("incident_number",""), "")

    entries = data.get("entries", [])
    headers = ["Name", "Call sign", "Role", "Division", "Team",
               "Check-in", "Check-out", "Status"]
    rows = [
        [
            e.get("name",""),
            e.get("call_sign",""),
            e.get("role",""),
            e.get("division",""),
            e.get("team",""),
            _fmt_ts(e.get("check_in_time","")),
            _fmt_ts(e.get("check_out_time","")),
            e.get("status","").replace("_"," ").title(),
        ]
        for e in entries
    ]
    _data_table(elements, headers, rows,
                [1.4*inch, 0.8*inch, 1.1*inch, 0.8*inch, 0.6*inch,
                 0.9*inch, 0.9*inch, 0.5*inch])

    doc.build(elements)
    return buf.getvalue()


def render_206(data: dict) -> bytes:
    """ICS-206 Medical Plan."""
    styles = _get_styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.5*inch, bottomMargin=0.75*inch)
    elements = []

    _ics_header(elements, styles, "206", "Medical Plan",
                data.get("incident_name",""), data.get("incident_number",""),
                data.get("operational_period",""))

    _section_header(elements, styles, "1. Medical personnel")
    med = data.get("medical_personnel", [])
    if med:
        headers = ["Name", "Call sign", "Phone", "Role", "Certifications"]
        rows = [
            [
                p.get("name",""),
                p.get("call_sign",""),
                p.get("phone",""),
                p.get("role",""),
                ", ".join(c["cert_type"] for c in p.get("certs",[])),
            ]
            for p in med
        ]
        _data_table(elements, headers, rows,
                    [1.8*inch, 0.9*inch, 1.1*inch, 1.2*inch, 2*inch])
    else:
        elements.append(Paragraph("No personnel with medical certifications deployed.", styles["Body"]))

    _section_header(elements, styles, "2. Hospitals")
    hospitals = data.get("hospitals", [])
    if hospitals:
        for h in hospitals:
            elements.append(Paragraph(str(h), styles["Body"]))
    else:
        elements.append(Paragraph("(IC must complete — list nearest hospitals with address and phone)", styles["Body"]))

    _section_header(elements, styles, "3. Medical aid stations")
    stations = data.get("medical_aid_stations", [])
    if stations:
        for s in stations:
            elements.append(Paragraph(str(s), styles["Body"]))
    else:
        elements.append(Paragraph("(IC must complete if aid stations are established)", styles["Body"]))

    _signature_block(elements, styles,
                     data.get("signed_by",""), data.get("signed_at",""),
                     role="Medical Officer / Incident Commander")
    doc.build(elements)
    return buf.getvalue()


def render_209(data: dict) -> bytes:
    """ICS-209 Incident Status Summary."""
    styles = _get_styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.5*inch, bottomMargin=0.75*inch)
    elements = []

    _ics_header(elements, styles, "209", "Incident Status Summary",
                data.get("incident_name",""), data.get("incident_number",""),
                data.get("operational_period",""))

    # Stats block
    _section_header(elements, styles, "1. Incident status")
    stats_data = [[
        _label_value("State", data.get("state","")),
        _label_value("County", data.get("county","")),
        _label_value("Type", data.get("incident_type","").replace("_"," ").title()),
        _label_value("Phase", data.get("incident_phase","")),
    ],[
        _label_value("Total personnel", str(data.get("total_personnel",0))),
        _label_value("Active personnel", str(data.get("active_personnel",0))),
        _label_value("Total segments", str(data.get("total_segments",0))),
        _label_value("Segments cleared", str(data.get("cleared_segments",0))),
    ]]
    st = Table(stats_data, colWidths=[1.75*inch]*4)
    st.setStyle(TableStyle([
        ("BOX",        (0,0), (-1,-1), 0.5, GRAY_BORDER),
        ("INNERGRID",  (0,0), (-1,-1), 0.5, GRAY_BORDER),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [WHITE, GRAY_LIGHT]),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
    ]))
    elements.append(st)
    elements.append(Spacer(1, 0.1*inch))

    for num, key, label in [
        ("2", "current_situation", "Current situation"),
        ("3", "primary_mission",   "Primary mission"),
        ("4", "planned_actions",   "Planned actions"),
    ]:
        _section_header(elements, styles, f"{num}. {label}")
        text = data.get(key,"") or "(Not completed)"
        elements.append(Paragraph(text, styles["Body"]))

    _signature_block(elements, styles,
                     data.get("signed_by",""), data.get("signed_at",""))
    doc.build(elements)
    return buf.getvalue()


def render_placeholder(form_number: str, form_title: str, data: dict) -> bytes:
    """
    Generic placeholder renderer for forms not yet fully implemented.
    Renders the header, all key-value pairs, and signature block.
    Used for ICS-204, 205, 214, 215.
    """
    styles = _get_styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.5*inch, bottomMargin=0.75*inch)
    elements = []

    _ics_header(elements, styles, form_number, form_title,
                data.get("incident_name",""), data.get("incident_number",""),
                data.get("operational_period",""))

    for key, value in data.items():
        if key in ("signed_by","signed_at","existing_id","version"):
            continue
        if isinstance(value, (dict, list)):
            import json as _json
            value = _json.dumps(value, indent=2, default=str)
        elements.append(Paragraph(f"<b>{key.replace('_',' ').title()}:</b>", styles["FieldLabel"]))
        elements.append(Paragraph(str(value or "—"), styles["Body"]))

    _signature_block(elements, styles,
                     data.get("signed_by",""), data.get("signed_at",""))
    doc.build(elements)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

RENDERERS = {
    "ics_201": lambda d: render_201(d),
    "ics_204": lambda d: render_placeholder("204", "Assignment List", d[0] if d else {}),
    "ics_205": lambda d: render_placeholder("205", "Radio Communications Plan", d),
    "ics_206": lambda d: render_206(d),
    "ics_209": lambda d: render_209(d),
    "ics_211": lambda d: render_211(d),
    "ics_214": lambda d: render_placeholder("214", "Activity Log", d[0] if d else {}),
    "ics_215": lambda d: render_placeholder("215", "Operational Planning Worksheet", d),
}


def render_form(form_key: str, data) -> bytes:
    """
    Render a single ICS form to PDF bytes.
    Raises RuntimeError if ReportLab is not installed.
    Raises KeyError if form_key is not recognized.
    """
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError(
            "ReportLab is not installed. Run: pip install reportlab"
        )
    if form_key not in RENDERERS:
        raise KeyError(f"Unknown form: {form_key}")
    return RENDERERS[form_key](data)


def render_all(compiled: dict) -> dict[str, bytes]:
    """
    Render all 8 ICS forms. Returns {form_key: pdf_bytes}.
    """
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError("ReportLab is not installed. Run: pip install reportlab")

    results = {}
    for key, renderer in RENDERERS.items():
        try:
            results[key] = renderer(compiled.get(key, {}))
            log.debug("Rendered %s OK", key)
        except Exception as e:
            log.error("Failed to render %s: %s", key, e)
            raise
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_ts(ts: str) -> str:
    """Format an ISO timestamp to a human-readable string."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%m/%d/%Y %H:%M")
    except Exception:
        return str(ts)[:16]
