"""
SARPack — seed_demo.py
Populates the database with a realistic demo incident for presentations.

Scenario:
    Missing hiker on Hawk Mountain Sanctuary, Schuylkill County, PA.
    Subject: male, 58, cardiac history, overdue 6 hours.
    Active search with 3 teams across 6 segments, 14 personnel deployed.

Run from the SARPack root directory:
    python seed_demo.py

To reset and re-seed:
    python seed_demo.py --reset
"""

import sys
import json
import argparse
import sqlite3
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")
from core import initialize
from core.db import local_db, new_id, now_utc
from core.auth import create_user, hash_password

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ts(hours_ago: float = 0) -> str:
    """Return a UTC ISO timestamp offset from now."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def insert(db, table: str, record: dict):
    """Raw insert — bypasses outbox for seed data."""
    cols = ", ".join(record.keys())
    placeholders = ", ".join("?" * len(record))
    db.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(record.values()))


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

def reset_db():
    print("Resetting demo data...")
    tables = [
        "outbox", "sessions", "users", "ics_215", "ics_214", "ics_211",
        "ics_209", "ics_206", "ics_205", "ics_204", "ics_201",
        "radio_log", "search_segments", "gps_tracks", "deployments",
        "certifications", "incidents", "personnel",
    ]
    with local_db() as db:
        for t in tables:
            db.execute(f"DELETE FROM {t}")
    print("Database cleared.")


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

def seed():
    print("Seeding demo data — Hawk Mountain SAR incident...")

    with local_db() as db:

        # -------------------------------------------------------------------
        # PERSONNEL  (14 members)
        # -------------------------------------------------------------------
        # Format: (id, first, last, call_sign, phone, email, blood, emerg_name, emerg_phone)
        personnel_data = [
            ("p01", "Marcus",   "Holloway",   "BRAVO-1",  "570-555-0101", "m.holloway@krs.org",   "A+",  "Linda Holloway",    "570-555-0201"),
            ("p02", "Diane",    "Vesper",     "ALPHA-1",  "570-555-0102", "d.vesper@krs.org",     "O-",  "Tom Vesper",        "570-555-0202"),
            ("p03", "Rafael",   "Okafor",     "CHARLIE-1","570-555-0103", "r.okafor@krs.org",     "B+",  "Amara Okafor",      "570-555-0203"),
            ("p04", "Sienna",   "Caldwell",   "ALPHA-2",  "570-555-0104", "s.caldwell@krs.org",   "AB+", "James Caldwell",    "570-555-0204"),
            ("p05", "Derek",    "Morrow",     "BRAVO-2",  "570-555-0105", "d.morrow@krs.org",     "O+",  "Pam Morrow",        "570-555-0205"),
            ("p06", "Yuki",     "Tanaka",     "CHARLIE-2","570-555-0106", "y.tanaka@krs.org",     "A-",  "Kenji Tanaka",      "570-555-0206"),
            ("p07", "Andre",    "Beckett",    "ALPHA-3",  "570-555-0107", "a.beckett@krs.org",    "B-",  "Claire Beckett",    "570-555-0207"),
            ("p08", "Priya",    "Nair",       "BRAVO-3",  "570-555-0108", "p.nair@krs.org",       "O+",  "Suresh Nair",       "570-555-0208"),
            ("p09", "Cole",     "Whitmore",   "BASE-OPS", "570-555-0109", "c.whitmore@krs.org",   "A+",  "Janet Whitmore",    "570-555-0209"),
            ("p10", "Tasha",    "Drummond",   "MEDICAL",  "570-555-0110", "t.drummond@krs.org",   "O-",  "Eric Drummond",     "570-555-0210"),
            ("p11", "Owen",     "Garrett",    "LOG-1",    "570-555-0111", "o.garrett@krs.org",    "A+",  "Ruth Garrett",      "570-555-0211"),
            ("p12", "Fatima",   "Idris",      "CHARLIE-3","570-555-0112", "f.idris@krs.org",      "B+",  "Hassan Idris",      "570-555-0212"),
            ("p13", "Leon",     "Parrish",    "ALPHA-4",  "570-555-0113", "l.parrish@krs.org",    "AB-", "Donna Parrish",     "570-555-0213"),
            ("p14", "Maya",     "Solis",      "BRAVO-4",  "570-555-0114", "m.solis@krs.org",      "O+",  "Carlos Solis",      "570-555-0214"),
        ]

        for p in personnel_data:
            insert(db, "personnel", {
                "id": p[0], "first_name": p[1], "last_name": p[2],
                "call_sign": p[3], "phone": p[4], "email": p[5],
                "blood_type": p[6], "emergency_contact_name": p[7],
                "emergency_contact_phone": p[8], "is_active": 1, "version": 1,
                "created_at": ts(48), "updated_at": ts(48),
            })

        print(f"  ✓ {len(personnel_data)} personnel records")

        # -------------------------------------------------------------------
        # CERTIFICATIONS
        # -------------------------------------------------------------------
        certs = [
            # IC — Marcus Holloway
            ("p01", "FEMA_ICS_400", "ICS-400-2019-MH", "FEMA", "2019-03-15", "2027-03-15"),
            ("p01", "WFR",          "WFR-2022-MH",     "NOLS", "2022-06-01", "2025-06-01"),
            ("p01", "CPR",          "CPR-2023-MH",     "AHA",  "2023-01-10", "2025-01-10"),
            # Ops Chief — Diane Vesper
            ("p02", "FEMA_ICS_300", "ICS-300-2020-DV", "FEMA", "2020-09-20", "2026-09-20"),
            ("p02", "WFR",          "WFR-2021-DV",     "NOLS", "2021-04-10", "2024-04-10"),
            # Medical — Tasha Drummond
            ("p10", "EMT",          "EMT-PA-2021-TD",  "PA DOH","2021-07-01", "2026-07-01"),
            ("p10", "WFR",          "WFR-2022-TD",     "NOLS", "2022-03-15", "2025-03-15"),
            ("p10", "CPR",          "CPR-2023-TD",     "AHA",  "2023-05-20", "2025-05-20"),
            # Rafael Okafor — WFR + Swift Water
            ("p03", "WFR",          "WFR-2023-RO",     "NOLS", "2023-01-20", "2026-01-20"),
            ("p03", "Swift_Water_Rescue", "SWR-2022-RO","ARC", "2022-11-01", "2025-11-01"),
            ("p03", "CPR",          "CPR-2022-RO",     "AHA",  "2022-08-14", "2024-08-14"),
            # Sienna Caldwell
            ("p04", "WFR",          "WFR-2022-SC",     "NOLS", "2022-05-01", "2025-05-01"),
            ("p04", "CPR",          "CPR-2023-SC",     "AHA",  "2023-02-28", "2025-02-28"),
            # Derek Morrow
            ("p05", "WEMT",         "WEMT-2021-DM",    "NOLS", "2021-09-15", "2024-09-15"),
            ("p05", "CPR",          "CPR-2022-DM",     "AHA",  "2022-06-01", "2024-06-01"),
            # Others — CPR + basic
            ("p06", "CPR",          "CPR-2023-YT",     "AHA",  "2023-03-10", "2025-03-10"),
            ("p07", "CPR",          "CPR-2022-AB",     "AHA",  "2022-12-01", "2024-12-01"),
            ("p08", "WFR",          "WFR-2023-PN",     "NOLS", "2023-06-01", "2026-06-01"),
            ("p08", "CPR",          "CPR-2023-PN",     "AHA",  "2023-06-01", "2025-06-01"),
            ("p12", "CPR",          "CPR-2023-FI",     "AHA",  "2023-04-15", "2025-04-15"),
            ("p13", "WFR",          "WFR-2022-LP",     "NOLS", "2022-07-20", "2025-07-20"),
            ("p14", "CPR",          "CPR-2023-MS",     "AHA",  "2023-01-30", "2025-01-30"),
        ]

        for c in certs:
            insert(db, "certifications", {
                "id": new_id(), "personnel_id": c[0], "cert_type": c[1],
                "cert_number": c[2], "issuing_body": c[3],
                "issued_date": c[4], "expiry_date": c[5],
                "is_verified": 1, "version": 1,
                "created_at": ts(48), "updated_at": ts(48),
            })

        print(f"  ✓ {len(certs)} certification records")

        # -------------------------------------------------------------------
        # INCIDENT
        # Hawk Mountain Sanctuary, Schuylkill County, PA
        # -------------------------------------------------------------------
        incident_id = "inc-hawk-mountain-001"
        insert(db, "incidents", {
            "id":                   incident_id,
            "incident_number":      "KRS-2026-0047",
            "incident_name":        "Hawk Mountain Missing Hiker",
            "incident_type":        "sar",
            "status":               "active",
            "lat":                  40.6370,
            "lng":                  -75.9940,
            "county":               "Schuylkill",
            "state":                "PA",
            "started_at":           ts(6),
            "incident_commander_id": "p01",
            "notes":                (
                "Subject: Raymond Kowalski, M/58, cardiac history. "
                "Last seen: Lookout Trail near South Lookout, approx 14:00. "
                "Vehicle remains in main lot. Reported overdue by spouse at 18:30. "
                "Weather: 44°F, clearing, winds NW 12mph. Sunset 19:52. "
                "Subject is experienced hiker but unfamiliar with north ridge terrain."
            ),
            "version":              1,
            "created_at":           ts(6),
            "updated_at":           ts(0.25),
        })

        print(f"  ✓ Incident: KRS-2026-0047 — Hawk Mountain Missing Hiker")

        # -------------------------------------------------------------------
        # DEPLOYMENTS
        # -------------------------------------------------------------------
        # (personnel_id, role, division, team, hours_ago_checkin)
        deployments = [
            ("p01", "Incident Commander",       "Command",  None,       5.8),
            ("p02", "Operations Section Chief", "Command",  None,       5.5),
            ("p09", "Base Camp Coordinator",    "Command",  None,       5.5),
            ("p10", "Medical Officer",          "Medical",  None,       5.3),
            ("p11", "Logistics Chief",          "Logistics",None,       5.3),
            ("p03", "Team Leader",              "Alpha",    "Alpha-1",  5.0),
            ("p04", "Field Operator",           "Alpha",    "Alpha-1",  5.0),
            ("p13", "Field Operator",           "Alpha",    "Alpha-1",  4.8),
            ("p05", "Team Leader",              "Bravo",    "Bravo-1",  4.9),
            ("p06", "Field Operator",           "Bravo",    "Bravo-1",  4.9),
            ("p14", "Field Operator",           "Bravo",    "Bravo-1",  4.7),
            ("p07", "Team Leader",              "Charlie",  "Charlie-1",4.6),
            ("p08", "Field Operator",           "Charlie",  "Charlie-1",4.6),
            ("p12", "Field Operator",           "Charlie",  "Charlie-1",4.5),
        ]

        deployment_ids = {}
        for d in deployments:
            did = new_id()
            deployment_ids[d[0]] = did
            insert(db, "deployments", {
                "id":            did,
                "incident_id":   incident_id,
                "personnel_id":  d[0],
                "role":          d[1],
                "division":      d[2],
                "team":          d[3],
                "checked_in_at": ts(d[4]),
                "status":        "active",
                "version":       1,
                "created_at":    ts(d[4]),
                "updated_at":    ts(d[4]),
            })

        print(f"  ✓ {len(deployments)} deployment records")

        # -------------------------------------------------------------------
        # SEARCH SEGMENTS
        # Segments around Hawk Mountain Sanctuary area
        # -------------------------------------------------------------------
        # Coordinates approximate Hawk Mountain ridge and surrounding areas
        segments = [
            ("A1", "Alpha-1",   "cleared",    4.2,  None,  0.87, [
                [40.6420, -75.9980], [40.6440, -75.9920], [40.6400, -75.9900], [40.6380, -75.9960],
            ]),
            ("A2", "Alpha-1",   "cleared",    3.8,  3.1,   0.91, [
                [40.6440, -75.9920], [40.6460, -75.9860], [40.6420, -75.9840], [40.6400, -75.9900],
            ]),
            ("B1", "Bravo-1",   "assigned",   4.5,  None,  0.72, [
                [40.6370, -76.0040], [40.6390, -75.9980], [40.6350, -75.9960], [40.6330, -76.0020],
            ]),
            ("B2", "Bravo-1",   "assigned",   4.1,  None,  0.65, [
                [40.6390, -75.9980], [40.6410, -75.9920], [40.6370, -75.9900], [40.6350, -75.9960],
            ]),
            ("C1", "Charlie-1", "assigned",   4.3,  None,  0.58, [
                [40.6310, -75.9960], [40.6330, -75.9900], [40.6290, -75.9880], [40.6270, -75.9940],
            ]),
            ("C2", None,        "unassigned", None, None,  0.43, [
                [40.6330, -75.9900], [40.6350, -75.9840], [40.6310, -75.9820], [40.6290, -75.9880],
            ]),
        ]

        for s in segments:
            seg_id_str, team, status, assigned_hrs, cleared_hrs, pod, coords = s
            insert(db, "search_segments", {
                "id":                       new_id(),
                "incident_id":              incident_id,
                "segment_id":               seg_id_str,
                "assigned_team":            team,
                "status":                   status,
                "boundary_coords":          json.dumps(coords),
                "probability_of_detection": pod,
                "assigned_at":              ts(assigned_hrs) if assigned_hrs else None,
                "cleared_at":               ts(cleared_hrs) if cleared_hrs else None,
                "version":                  1,
                "created_at":               ts(5),
                "updated_at":               ts(1),
            })

        print(f"  ✓ {len(segments)} search segments (2 cleared, 3 assigned, 1 unassigned)")

        # -------------------------------------------------------------------
        # GPS TRACKS
        # Simulate movement for all field teams
        # -------------------------------------------------------------------
        # Alpha-1 team — worked north ridge, now returning
        alpha_track_base = [
            (40.6390, -75.9970, 5.0), (40.6400, -75.9955, 4.8), (40.6412, -75.9940, 4.5),
            (40.6425, -75.9928, 4.2), (40.6438, -75.9915, 3.9), (40.6448, -75.9900, 3.6),
            (40.6452, -75.9882, 3.3), (40.6445, -75.9868, 3.0), (40.6430, -75.9875, 2.7),
            (40.6418, -75.9890, 2.4), (40.6405, -75.9910, 2.1), (40.6395, -75.9930, 1.8),
        ]
        # Bravo-1 team — working west slope
        bravo_track_base = [
            (40.6380, -76.0010, 4.9), (40.6372, -75.9995, 4.6), (40.6365, -75.9978, 4.3),
            (40.6358, -75.9962, 4.0), (40.6368, -75.9948, 3.7), (40.6375, -75.9935, 3.4),
            (40.6382, -75.9920, 3.1), (40.6390, -75.9908, 2.8), (40.6385, -75.9895, 2.5),
            (40.6374, -75.9910, 2.2),
        ]
        # Charlie-1 team — working south approach
        charlie_track_base = [
            (40.6320, -75.9950, 4.6), (40.6328, -75.9935, 4.3), (40.6315, -75.9920, 4.0),
            (40.6305, -75.9908, 3.7), (40.6295, -75.9895, 3.4), (40.6302, -75.9882, 3.1),
            (40.6312, -75.9870, 2.8), (40.6320, -75.9858, 2.5), (40.6325, -75.9870, 2.2),
        ]

        gps_entries = []
        # Alpha team members: p03, p04, p13
        for i, (lat, lng, hrs) in enumerate(alpha_track_base):
            for pid, offset_lat, offset_lng in [("p03", 0, 0), ("p04", 0.0003, 0.0002), ("p13", -0.0002, 0.0003)]:
                gps_entries.append((pid, lat + offset_lat, lng + offset_lng, hrs))
        # Bravo team: p05, p06, p14
        for lat, lng, hrs in bravo_track_base:
            for pid, offset_lat, offset_lng in [("p05", 0, 0), ("p06", 0.0002, -0.0002), ("p14", -0.0003, 0.0001)]:
                gps_entries.append((pid, lat + offset_lat, lng + offset_lng, hrs))
        # Charlie team: p07, p08, p12
        for lat, lng, hrs in charlie_track_base:
            for pid, offset_lat, offset_lng in [("p07", 0, 0), ("p08", 0.0002, 0.0003), ("p12", -0.0002, -0.0002)]:
                gps_entries.append((pid, lat + offset_lat, lng + offset_lng, hrs))

        for pid, lat, lng, hrs in gps_entries:
            insert(db, "gps_tracks", {
                "id":          new_id(),
                "incident_id": incident_id,
                "personnel_id":pid,
                "lat":         round(lat, 6),
                "lng":         round(lng, 6),
                "elevation":   round(380 + (lat - 40.63) * 800, 1),
                "accuracy":    round(3.5 + (hrs % 2), 1),
                "recorded_at": ts(hrs),
                "source":      "trailhead",
                "created_at":  ts(hrs),
            })

        print(f"  ✓ {len(gps_entries)} GPS track points across 9 field operators")

        # -------------------------------------------------------------------
        # RADIO LOG
        # Realistic comms timeline, oldest to newest
        # -------------------------------------------------------------------
        radio_entries = [
            # Initial activation
            ("p01", "TAC-1", ts(6.0),   False, "All units — BASECAMP. KRS-2026-0047 is active. Subject is Raymond Kowalski, M/58, cardiac history. Last seen South Lookout at 1400. Stand by for team assignments."),
            ("p02", "TAC-1", ts(5.9),   False, "BASECAMP — OPS. Confirming Alpha-1 assigned to segments A1 and A2, north ridge. Bravo-1 to B1 and B2, west slope. Charlie-1 to C1, south approach. Copy?"),
            ("p01", "TAC-1", ts(5.85),  False, "OPS — BASECAMP. Copy all. Teams move on my mark. MEDICAL — confirm you are staged at base."),
            ("p10", "TAC-1", ts(5.8),   False, "BASECAMP — MEDICAL. Affirm. Staged at base with AED, O2, and cardiac kit. Standing by."),
            # Teams deploying
            ("p03", "TAC-1", ts(5.5),   False, "BASECAMP — ALPHA-1. Entering segment A1 from north trailhead. 3 personnel. Radio check every 30."),
            ("p01", "TAC-1", ts(5.48),  False, "ALPHA-1 — BASECAMP. Copy. Check in 30 minutes."),
            ("p05", "TAC-1", ts(5.4),   False, "BASECAMP — BRAVO-1. On trail, approaching B1 entry point. Terrain is steep, watch your footing."),
            ("p07", "TAC-1", ts(5.3),   False, "BASECAMP — CHARLIE-1. En route to C1, ETA 15 minutes from south access."),
            # 30-minute check-ins
            ("p03", "TAC-1", ts(5.0),   False, "BASECAMP — ALPHA-1. 30-minute check. All 3 personnel accounted for. Working A1 grid. No sign of subject. Copy."),
            ("p01", "TAC-1", ts(4.98),  False, "ALPHA-1 — BASECAMP. Copy. Continue."),
            ("p05", "TAC-1", ts(4.9),   False, "BASECAMP — BRAVO-1. Check-in. B1 entry. Moderate brush. No sign. 3 personnel OK."),
            ("p07", "TAC-1", ts(4.8),   False, "BASECAMP — CHARLIE-1. In C1. Terrain manageable. No subject contact."),
            # Alpha clears A1
            ("p03", "TAC-1", ts(4.2),   False, "BASECAMP — ALPHA-1. Segment A1 is cleared. POD high. No sign of subject. Transitioning to A2 now."),
            ("p01", "TAC-1", ts(4.18),  False, "ALPHA-1 — BASECAMP. Copy A1 cleared. Proceed to A2."),
            ("p02", "TAC-1", ts(4.15),  False, "All units — OPS. Segment A1 cleared with high POD. Refining probability matrix. Stand by for updated tasking."),
            # 1-hour check-ins
            ("p03", "TAC-1", ts(4.0),   False, "BASECAMP — ALPHA-1. Check. Working A2. Terrain rough on east face. All OK."),
            ("p05", "TAC-1", ts(3.9),   False, "BASECAMP — BRAVO-1. Check. Deep in B1. Finding a lot of deer trails — could explain confusion. No sign of subject."),
            ("p07", "TAC-1", ts(3.8),   False, "BASECAMP — CHARLIE-1. Check. C1 is dense. Moving slower than planned. All 3 accounted for."),
            # Medical note
            ("p10", "MED-1", ts(3.5),   False, "BASECAMP — MEDICAL. Reminder: subject has cardiac history. If located, do not allow him to walk out unassisted. Request litter extraction if found beyond 0.5 miles from trailhead."),
            ("p01", "TAC-1", ts(3.48),  False, "MEDICAL — BASECAMP. Copy. All team leaders — advise. Subject is cardiac risk. Plan for litter if needed."),
            # Alpha clears A2
            ("p03", "TAC-1", ts(3.1),   False, "BASECAMP — ALPHA-1. A2 cleared. High POD. No subject. Requesting new assignment."),
            ("p02", "TAC-1", ts(3.05),  False, "ALPHA-1 — OPS. Hold position at A2 boundary. Reassigning you to B2 to support Bravo. Confirm copy."),
            ("p03", "TAC-1", ts(3.02),  False, "OPS — ALPHA-1. Copy. Moving to B2."),
            # Missed check-in — Bravo
            ("p05", "TAC-1", ts(2.85),  True,  "MISSED CHECK-IN — BRAVO-1. No radio contact at scheduled interval."),
            ("p01", "TAC-1", ts(2.83),  False, "BRAVO-1 — BASECAMP. Radio check. Respond."),
            ("p05", "TAC-1", ts(2.80),  False, "BASECAMP — BRAVO-1. Copy, sorry — radio went to wrong channel. All 3 accounted for. In B1, dense canopy."),
            ("p01", "TAC-1", ts(2.78),  False, "BRAVO-1 — BASECAMP. Copy. Stay on TAC-1. Check in on the 30."),
            # Afternoon check-ins
            ("p05", "TAC-1", ts(2.5),   False, "BASECAMP — BRAVO-1. B1 is clear. Transitioning to B2."),
            ("p07", "TAC-1", ts(2.4),   False, "BASECAMP — CHARLIE-1. Still in C1. Found a water bottle near the lower switchback — unknown if subject's. Marking coords."),
            ("p01", "TAC-1", ts(2.38),  False, "CHARLIE-1 — BASECAMP. Copy. Note coords. MEDICAL — heads up, possible sign of subject in C1."),
            ("p10", "MED-1", ts(2.35),  False, "BASECAMP — MEDICAL. Copy. Moving kit closer to C1 access point."),
            # Contact
            ("p07", "TAC-1", ts(1.4),   False, "BASECAMP — CHARLIE-1. WE HAVE VISUAL ON SUBJECT. Repeat — subject located. Lower C1, off trail 30 meters east of switchback. Subject is ambulatory, appears disoriented. Requesting medical."),
            ("p01", "TAC-1", ts(1.38),  False, "ALL UNITS — BASECAMP. Subject located by CHARLIE-1 in segment C1. All teams hold position. MEDICAL — respond to C1 access point immediately."),
            ("p10", "MED-1", ts(1.35),  False, "BASECAMP — MEDICAL. En route to C1."),
            ("p02", "TAC-1", ts(1.30),  False, "ALPHA-1 and BRAVO-1 — hold B2. Maintain radio silence on TAC-1. Medical is working."),
            # Medical on scene
            ("p10", "MED-1", ts(1.0),   False, "BASECAMP — MEDICAL. On scene with subject. Vitals: BP 148/92, HR 88, O2 96%. Responsive, alert, minor hypothermia. No cardiac event. Requesting litter transport as precaution per IC standing order."),
            ("p01", "TAC-1", ts(0.98),  False, "MEDICAL — BASECAMP. Copy. Good work. Litter team is staged. Begin extraction when ready."),
            ("p07", "TAC-1", ts(0.75),  False, "BASECAMP — CHARLIE-1. Subject and MEDICAL moving to trailhead. ETA 20 minutes."),
            ("p01", "TAC-1", ts(0.5),   False, "All units — BASECAMP. Subject is ambulatory and in medical care. Begin orderly extraction from your sectors. Return to base when clear. Outstanding work tonight."),
            # Wrap-up
            ("p03", "TAC-1", ts(0.35),  False, "BASECAMP — ALPHA-1. Clearing B2. En route to base. All 3 accounted for."),
            ("p05", "TAC-1", ts(0.30),  False, "BASECAMP — BRAVO-1. Clearing sector. En route. 3 personnel."),
            ("p07", "TAC-1", ts(0.15),  False, "BASECAMP — CHARLIE-1. Subject delivered to base. MEDICAL has him. We are clearing equipment."),
            ("p10", "MED-1", ts(0.10),  False, "BASECAMP — MEDICAL. Subject stable. Transferring to awaiting EMS for transport to Geisinger — precautionary cardiac eval. Incident medical phase complete."),
            ("p01", "TAC-1", ts(0.05),  False, "All units — BASECAMP. Outstanding operation. Subject located and in care in under 6 hours. ICS forms will be finalized tonight. Stand down when you are at base. BASECAMP out."),
        ]

        for entry in radio_entries:
            pid, channel, logged_at, missed, message = entry
            insert(db, "radio_log", {
                "id":               new_id(),
                "incident_id":      incident_id,
                "personnel_id":     pid,
                "channel":          channel,
                "message":          message,
                "logged_at":        logged_at,
                "is_missed_checkin":1 if missed else 0,
                "source":           "manual",
                "created_at":       logged_at,
            })

        print(f"  ✓ {len(radio_entries)} radio log entries")

        # -------------------------------------------------------------------
        # ICS-201 — Incident Briefing
        # -------------------------------------------------------------------
        insert(db, "ics_201", {
            "id":                new_id(),
            "incident_id":       incident_id,
            "situation_summary": (
                "Missing hiker — Raymond Kowalski, M/58. Known cardiac history. "
                "Last seen on Lookout Trail near South Lookout, Hawk Mountain Sanctuary, "
                "Schuylkill County PA at approximately 1400 hours. Vehicle remains in "
                "main parking area. Reported overdue by spouse at 1830. "
                "Weather: 44°F clearing, NW winds 12mph, sunset 1952."
            ),
            "initial_objectives": json.dumps([
                "Locate subject Raymond Kowalski in primary probability area.",
                "Ensure medical resources are staged for cardiac-risk subject.",
                "Clear segments A1, A2, B1, B2, C1 in priority order.",
                "Maintain radio accountability on 30-minute intervals.",
                "Coordinate with Schuylkill County 911 for EMS standby.",
            ]),
            "current_actions": (
                "Three teams deployed across six search segments. "
                "Alpha-1 cleared A1 and A2 with high POD. "
                "Bravo-1 working B1 and B2. Charlie-1 working C1. "
                "Medical officer staged at base with cardiac kit and AED. "
                "Subject located in C1 by Charlie-1 at approximately hour 4.5 of operation."
            ),
            "resource_summary": json.dumps({
                "teams": 3,
                "field_personnel": 9,
                "command_staff": 5,
                "vehicles": 4,
                "medical_kits": 2,
                "litter": 1,
            }),
            "prepared_by":  "p02",
            "prepared_at":  ts(5.5),
            "signed_by":    "p01",
            "signed_at":    ts(5.4),
            "version":      1,
            "created_at":   ts(5.5),
            "updated_at":   ts(0.1),
        })

        print(f"  ✓ ICS-201 Incident Briefing")

        # -------------------------------------------------------------------
        # ICS-209 — Incident Status Summary
        # -------------------------------------------------------------------
        insert(db, "ics_209", {
            "id":                new_id(),
            "incident_id":       incident_id,
            "operational_period":"2026-04-21 1800 – 2200",
            "incident_phase":    "Search — Subject Located, Extraction Complete",
            "total_personnel":   14,
            "current_situation": (
                "Subject Raymond Kowalski located in segment C1 at approximately 2115 by "
                "Charlie-1 team leader Owen Garrett. Subject ambulatory, disoriented, "
                "mild hypothermia. No cardiac event. Transferred to awaiting EMS at base "
                "at approximately 2145 for precautionary transport to Geisinger Medical Center."
            ),
            "primary_mission":   "Locate and extract overdue hiker, cardiac risk.",
            "planned_actions":   "Demobilize all teams. Complete ICS form package. Incident debrief 2230.",
            "resource_totals":   json.dumps({
                "personnel_total": 14,
                "teams_deployed":  3,
                "segments_searched": 5,
                "segments_cleared": 2,
                "gps_track_points": len(gps_entries),
            }),
            "prepared_by":  "p02",
            "prepared_at":  ts(0.3),
            "signed_by":    "p01",
            "signed_at":    ts(0.2),
            "version":      1,
            "created_at":   ts(0.3),
            "updated_at":   ts(0.2),
        })

        print(f"  ✓ ICS-209 Status Summary")

        # -------------------------------------------------------------------
        # USERS
        # -------------------------------------------------------------------
        users = [
            ("p01", "m.holloway",  "Keystone2026!", "IC"),
            ("p02", "d.vesper",    "Keystone2026!", "ops_chief"),
            ("p10", "t.drummond",  "Keystone2026!", "logistics"),
            (None,  "admin",       "Keystone2026!", "IC"),
        ]

        for u in users:
            pid, username, password, role = u
            now = now_utc()
            insert(db, "users", {
                "id":           new_id(),
                "personnel_id": pid,
                "username":     username,
                "password_hash":hash_password(password),
                "role":         role,
                "is_active":    1,
                "created_at":   now,
                "updated_at":   now,
            })

        print(f"  ✓ {len(users)} user accounts")

    print()
    print("=" * 60)
    print("Demo seed complete.")
    print()
    print("Incident:  KRS-2026-0047 — Hawk Mountain Missing Hiker")
    print("           Schuylkill County, PA")
    print()
    print("Login credentials:")
    print("  admin        / Keystone2026!  (IC)")
    print("  m.holloway   / Keystone2026!  (IC — Marcus Holloway)")
    print("  d.vesper     / Keystone2026!  (Ops Chief — Diane Vesper)")
    print("  t.drummond   / Keystone2026!  (Logistics — Tasha Drummond)")
    print()
    print("Open BASECAMP at http://localhost:8000")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SARPack demo seed script")
    parser.add_argument("--reset", action="store_true", help="Clear all data before seeding")
    args = parser.parse_args()

    initialize()

    if args.reset:
        reset_db()

    seed()