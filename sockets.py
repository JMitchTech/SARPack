"""
SARPack 2.0 — portal/sockets.py
All SocketIO event handlers. Imported by app.py on startup.
Every connected window (portal + all popouts) receives broadcast events.
"""

from app import socketio
from flask_socketio import emit, join_room, leave_room
from flask import request


# ── Connection lifecycle ──────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    print(f"[Socket] Client connected: {request.sid}")


@socketio.on("disconnect")
def on_disconnect():
    print(f"[Socket] Client disconnected: {request.sid}")


# ── Incident rooms ────────────────────────────────────────────────────────────

@socketio.on("join_incident")
def on_join_incident(data):
    """
    Client joins an incident room. All broadcast events are scoped
    to the incident room so windows only receive relevant updates.
    """
    incident_id = data.get("incident_id")
    if incident_id:
        room = f"incident_{incident_id}"
        join_room(room)
        emit("joined", {"room": room})


@socketio.on("leave_incident")
def on_leave_incident(data):
    incident_id = data.get("incident_id")
    if incident_id:
        leave_room(f"incident_{incident_id}")


# ── SOS alert ─────────────────────────────────────────────────────────────────

@socketio.on("sos_alert")
def on_sos_alert(data):
    """
    Fired when a TRAILHEAD operator triggers SOS.
    Broadcasts to ALL windows in the incident room simultaneously —
    portal window AND any popped-out module windows.
    The client renders a flashing red banner regardless of which
    tab/module is currently active.
    """
    incident_id = data.get("incident_id")
    if incident_id:
        emit(
            "sos_alert",
            data,
            room=f"incident_{incident_id}",
            include_self=True,
        )


@socketio.on("sos_acknowledged")
def on_sos_acknowledged(data):
    """IC clicks the SOS banner — broadcasts acknowledgement to all windows."""
    incident_id = data.get("incident_id")
    if incident_id:
        emit(
            "sos_acknowledged",
            data,
            room=f"incident_{incident_id}",
            include_self=True,
        )


# ── LKP updates ──────────────────────────────────────────────────────────────

@socketio.on("lkp_updated")
def on_lkp_updated(data):
    """Broadcast LKP change to all windows in the incident."""
    incident_id = data.get("incident_id")
    if incident_id:
        emit("lkp_updated", data, room=f"incident_{incident_id}", include_self=False)


@socketio.on("lkp_cleared")
def on_lkp_cleared(data):
    incident_id = data.get("incident_id")
    if incident_id:
        emit("lkp_cleared", data, room=f"incident_{incident_id}", include_self=False)


# ── GPS positions ─────────────────────────────────────────────────────────────

@socketio.on("position_update")
def on_position_update(data):
    """Broadcast operator GPS position to all windows in the incident."""
    incident_id = data.get("incident_id")
    if incident_id:
        emit("position_update", data, room=f"incident_{incident_id}", include_self=False)


# ── Map markers (LZ pins, DZ targets) ────────────────────────────────────────

@socketio.on("marker_added")
def on_marker_added(data):
    """
    Broadcast new map marker to all windows.
    LZ pins from TRAILHEAD appear on BASECAMP map instantly.
    DZ targets from BASECAMP appear on target TRAILHEAD device.
    """
    incident_id = data.get("incident_id")
    if incident_id:
        emit("marker_added", data, room=f"incident_{incident_id}", include_self=False)


@socketio.on("marker_removed")
def on_marker_removed(data):
    incident_id = data.get("incident_id")
    if incident_id:
        emit("marker_removed", data, room=f"incident_{incident_id}", include_self=False)


# ── DZ targeting (BASECAMP → specific TRAILHEAD device) ──────────────────────

@socketio.on("dz_target")
def on_dz_target(data):
    """
    IC pushes a Drop Zone target to a specific TRAILHEAD device.
    Routes to the target personnel's personal room rather than
    the whole incident room.
    """
    target_personnel_id = data.get("target_personnel_id")
    if target_personnel_id:
        emit("dz_target", data, room=f"operator_{target_personnel_id}")


# ── Radio log ─────────────────────────────────────────────────────────────────

@socketio.on("radio_entry")
def on_radio_entry(data):
    """New radio entry — update all open windows."""
    incident_id = data.get("incident_id")
    if incident_id:
        emit("radio_entry", data, room=f"incident_{incident_id}", include_self=False)


@socketio.on("missed_checkin")
def on_missed_checkin(data):
    """Missed check-in alert — broadcast to all windows."""
    incident_id = data.get("incident_id")
    if incident_id:
        emit("missed_checkin", data, room=f"incident_{incident_id}", include_self=True)


# ── Drone feed ────────────────────────────────────────────────────────────────

@socketio.on("drone_stream_ready")
def on_drone_stream_ready(data):
    """Air asset is streaming — notify IC to open drone feed window."""
    incident_id = data.get("incident_id")
    if incident_id:
        emit("drone_stream_ready", data, room=f"incident_{incident_id}", include_self=True)


# ── Segment updates ───────────────────────────────────────────────────────────

@socketio.on("segment_updated")
def on_segment_updated(data):
    incident_id = data.get("incident_id")
    if incident_id:
        emit("segment_updated", data, room=f"incident_{incident_id}", include_self=False)


# ── Operator personal rooms (for DZ targeting, direct messages) ───────────────

@socketio.on("join_operator_room")
def on_join_operator_room(data):
    """TRAILHEAD devices join their personal room for direct messages."""
    personnel_id = data.get("personnel_id")
    if personnel_id:
        join_room(f"operator_{personnel_id}")
        emit("joined_operator_room", {"room": f"operator_{personnel_id}"})