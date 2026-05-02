"""
SARPack 2.0 — api/relay.py
Meshtastic node management, mesh network status, and RELAY configuration.
"""

import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, g

from core.database import get_db, row_to_dict, rows_to_list
from core.auth import require_auth, require_ic, require_logistics, audit

bp = Blueprint("relay", __name__)


# ── Meshtastic node registry ──────────────────────────────────────────────────

@bp.route("/nodes", methods=["GET"])
@require_auth
def list_nodes():
    """
    List all registered Meshtastic nodes.
    Query params: incident_id, node_type
    """
    db          = get_db()
    incident_id = request.args.get("incident_id")
    node_type   = request.args.get("node_type")

    query  = "SELECT * FROM relay_nodes WHERE 1=1"
    params = []

    if incident_id:
        query += " AND incident_id = ?"
        params.append(incident_id)
    if node_type:
        query += " AND node_type = ?"
        params.append(node_type)

    query += " ORDER BY last_seen_at DESC"
    rows   = db.execute(query, params).fetchall()
    nodes  = rows_to_list(rows)

    # Flag online/offline (seen within last 5 minutes)
    now = datetime.now(timezone.utc)
    for node in nodes:
        if node.get("last_seen_at"):
            try:
                from datetime import timedelta
                last = datetime.fromisoformat(
                    node["last_seen_at"].replace("Z", "+00:00")
                )
                node["is_online"] = (now - last).total_seconds() < 300
            except Exception:
                node["is_online"] = False
        else:
            node["is_online"] = False

    return jsonify(nodes), 200


@bp.route("/nodes", methods=["POST"])
@require_auth
def register_node():
    """
    Register or update a Meshtastic node.
    Called automatically when a node checks in via the mesh.
    """
    data    = request.get_json(silent=True) or {}
    node_id = data.get("node_id", "").strip()

    if not node_id:
        return jsonify({"error": "node_id is required"}), 400

    db  = get_db()
    now = datetime.now(timezone.utc).isoformat()

    existing = db.execute(
        "SELECT id FROM relay_nodes WHERE node_id = ?", (node_id,)
    ).fetchone()

    if existing:
        db.execute(
            """UPDATE relay_nodes
               SET node_name = ?, node_type = ?, incident_id = ?,
                   last_seen_at = ?, battery_pct = ?, snr = ?
               WHERE node_id = ?""",
            (
                data.get("node_name"),
                data.get("node_type", "field"),
                data.get("incident_id"),
                now,
                data.get("battery_pct"),
                data.get("snr"),
                node_id,
            )
        )
    else:
        db.execute(
            """INSERT INTO relay_nodes
               (id, node_id, node_name, node_type, incident_id,
                last_seen_at, battery_pct, snr, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()), node_id,
                data.get("node_name"),
                data.get("node_type", "field"),
                data.get("incident_id"),
                now,
                data.get("battery_pct"),
                data.get("snr"),
                now,
            )
        )

    db.commit()

    # Broadcast node update to all connected windows
    try:
        from app import socketio
        socketio.emit("relay_node_update", {
            "node_id":    node_id,
            "node_name":  data.get("node_name"),
            "node_type":  data.get("node_type", "field"),
            "battery_pct": data.get("battery_pct"),
            "snr":        data.get("snr"),
            "is_online":  True,
        })
    except Exception:
        pass

    return jsonify(row_to_dict(db.execute(
        "SELECT * FROM relay_nodes WHERE node_id = ?", (node_id,)
    ).fetchone())), 200


@bp.route("/nodes/<node_id>", methods=["PATCH"])
@require_logistics
def update_node(node_id):
    """Update node metadata."""
    db   = get_db()
    node = db.execute(
        "SELECT id FROM relay_nodes WHERE node_id = ?", (node_id,)
    ).fetchone()

    if not node:
        return jsonify({"error": "Node not found"}), 404

    data    = request.get_json(silent=True) or {}
    updates = []
    params  = []

    for field in ["node_name", "node_type", "incident_id"]:
        if field in data:
            updates.append(f"{field} = ?")
            params.append(data[field])

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    params.append(node_id)
    db.execute(
        f"UPDATE relay_nodes SET {', '.join(updates)} WHERE node_id = ?", params
    )
    db.commit()
    return jsonify(row_to_dict(db.execute(
        "SELECT * FROM relay_nodes WHERE node_id = ?", (node_id,)
    ).fetchone())), 200


@bp.route("/nodes/<node_id>", methods=["DELETE"])
@require_ic
def remove_node(node_id):
    """Remove a node from the registry."""
    db = get_db()
    db.execute("DELETE FROM relay_nodes WHERE node_id = ?", (node_id,))
    db.commit()
    return jsonify({"message": "Node removed"}), 200


# ── Mesh network status ───────────────────────────────────────────────────────

@bp.route("/status", methods=["GET"])
@require_auth
def mesh_status():
    """
    Overall mesh network status for the RELAY dashboard.
    Returns online/offline nodes, coverage summary, and alerts.
    """
    db          = get_db()
    incident_id = request.args.get("incident_id")

    query  = "SELECT * FROM relay_nodes WHERE 1=1"
    params = []
    if incident_id:
        query += " AND incident_id = ?"
        params.append(incident_id)

    nodes = rows_to_list(db.execute(query, params).fetchall())

    now    = datetime.now(timezone.utc)
    online = []
    offline = []
    alerts  = []

    from datetime import timedelta
    for node in nodes:
        if node.get("last_seen_at"):
            try:
                last = datetime.fromisoformat(
                    node["last_seen_at"].replace("Z", "+00:00")
                )
                secs = (now - last).total_seconds()
                node["seconds_since_seen"] = int(secs)
                node["is_online"] = secs < 300

                if node["is_online"]:
                    online.append(node)
                else:
                    offline.append(node)
                    if secs > 600:
                        alerts.append({
                            "node_id":   node["node_id"],
                            "node_name": node["node_name"],
                            "alert":     f"Node offline for {int(secs // 60)} minutes",
                        })
            except Exception:
                node["is_online"] = False
                offline.append(node)
        else:
            node["is_online"] = False
            offline.append(node)

        # Battery alerts
        if node.get("battery_pct") is not None and node["battery_pct"] < 20:
            alerts.append({
                "node_id":   node["node_id"],
                "node_name": node["node_name"],
                "alert":     f"Low battery: {node['battery_pct']}%",
            })

    return jsonify({
        "total":        len(nodes),
        "online_count": len(online),
        "offline_count": len(offline),
        "online":       online,
        "offline":      offline,
        "alerts":       alerts,
        "checked_at":   now.isoformat(),
    }), 200


# ── Channel configuration ─────────────────────────────────────────────────────

@bp.route("/channels", methods=["GET"])
@require_auth
def get_channels():
    """
    Return the standard Meshtastic channel plan for SARPack.
    These are the recommended channel/PSK assignments for
    interoperability between SARPack nodes.
    """
    return jsonify({
        "channels": [
            {
                "index":   0,
                "name":    "SARPACK-CMD",
                "role":    "PRIMARY",
                "function": "Command — IC to Section Chiefs",
                "psk":     "Use SARPack default PSK (set in .env)",
                "modem":   "LONG_FAST",
                "notes":   "All nodes must be on this channel",
            },
            {
                "index":   1,
                "name":    "SARPACK-OPS",
                "role":    "SECONDARY",
                "function": "Operations — Field teams",
                "psk":     "Use SARPack default PSK",
                "modem":   "LONG_FAST",
                "notes":   "Field operators and TRAILHEAD devices",
            },
            {
                "index":   2,
                "name":    "SARPACK-MED",
                "role":    "SECONDARY",
                "function": "Medical coordination",
                "psk":     "Use SARPack default PSK",
                "modem":   "LONG_FAST",
                "notes":   "Medical personnel only",
            },
            {
                "index":   3,
                "name":    "SARPACK-GPS",
                "role":    "SECONDARY",
                "function": "Automated GPS position reports",
                "psk":     "Use SARPack default PSK",
                "modem":   "LONG_FAST",
                "notes":   "Automated only — do not use for voice-equivalent messaging",
            },
        ],
        "setup_notes": [
            "All SARPack Meshtastic nodes should run firmware 2.3+",
            "Set region to US (or appropriate local region)",
            "GPS-enabled nodes broadcast position automatically on SARPACK-GPS",
            "Router nodes should be placed at high elevation for maximum coverage",
            "Battery-powered repeater nodes extend mesh to ravines and valleys",
        ],
        "interop_note": (
            "For cross-org interoperability with non-SARPack Meshtastic networks, "
            "coordinate PSK sharing with the other org's radio officer. "
            "SARPack RELAY will display all nodes visible on the mesh regardless of org."
        ),
    }), 200


# ── Radio interoperability registry ──────────────────────────────────────────

@bp.route("/interop", methods=["GET"])
@require_auth
def interop_registry():
    """
    Cross-org radio interoperability registry.
    Shows all radio types on the current incident and
    generates a compatibility matrix.
    """
    db          = get_db()
    incident_id = request.args.get("incident_id")

    query  = """SELECT rr.*, p.first_name, p.last_name, p.call_sign,
                       p.home_agency, d.division, d.team
                FROM radio_registry rr
                JOIN personnel p ON rr.personnel_id = p.id"""
    params = []

    if incident_id:
        query += """ JOIN deployments d ON d.personnel_id = p.id
                    WHERE d.incident_id = ? AND d.status = 'active'"""
        params.append(incident_id)
    else:
        query += " LEFT JOIN deployments d ON d.personnel_id = p.id WHERE 1=1"

    query += " ORDER BY rr.radio_type, p.call_sign"
    rows   = rows_to_list(db.execute(query, params).fetchall())

    import json
    for row in rows:
        if row.get("programmed_channels"):
            try:
                row["programmed_channels"] = json.loads(row["programmed_channels"])
            except Exception:
                pass

    # Build compatibility matrix
    radio_types = list({r.get("radio_type", "unknown") for r in rows})

    compatibility = {
        ("analog",     "analog"):     "Full",
        ("analog",     "p25"):        "Partial (P25 conventional mode)",
        ("analog",     "dmr"):        "None (requires gateway)",
        ("analog",     "nxdn"):       "None (requires gateway)",
        ("analog",     "meshtastic"): "None (different medium)",
        ("p25",        "p25"):        "Full",
        ("p25",        "dmr"):        "None (requires gateway)",
        ("p25",        "nxdn"):       "None (requires gateway)",
        ("p25",        "meshtastic"): "None (different medium)",
        ("dmr",        "dmr"):        "Full (same color code required)",
        ("dmr",        "nxdn"):       "None (requires gateway)",
        ("dmr",        "meshtastic"): "None (different medium)",
        ("nxdn",       "nxdn"):       "Full",
        ("nxdn",       "meshtastic"): "None (different medium)",
        ("meshtastic", "meshtastic"): "Full (same PSK required)",
    }

    matrix = {}
    for t1 in radio_types:
        matrix[t1] = {}
        for t2 in radio_types:
            key = tuple(sorted([t1, t2]))
            matrix[t1][t2] = compatibility.get(key, "Unknown")

    return jsonify({
        "operators":         rows,
        "radio_types_found": radio_types,
        "compatibility":     matrix,
        "hardware_note": (
            "Full cross-platform interoperability between digital radio systems "
            "(P25, DMR, NXDN) requires a hardware gateway device such as the "
            "DVSwitch or MMDVM. SARPack documents what radios are on the incident "
            "so the IC can plan channel assignments accordingly."
        ),
    }), 200


# ── Heartbeat (nodes call this to stay online) ────────────────────────────────

@bp.route("/heartbeat", methods=["POST"])
@require_auth
def heartbeat():
    """
    Meshtastic gateway calls this periodically to keep node status current.
    Lightweight — just updates last_seen_at, battery, and SNR.
    """
    data    = request.get_json(silent=True) or {}
    node_id = data.get("node_id", "").strip()

    if not node_id:
        return jsonify({"error": "node_id is required"}), 400

    db  = get_db()
    now = datetime.now(timezone.utc).isoformat()

    db.execute(
        """UPDATE relay_nodes
           SET last_seen_at = ?, battery_pct = ?, snr = ?
           WHERE node_id = ?""",
        (now, data.get("battery_pct"), data.get("snr"), node_id)
    )
    db.commit()

    return jsonify({"acknowledged": True, "server_time": now}), 200