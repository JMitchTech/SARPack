"""
SARPack — basecamp/events.py
SocketIO real-time event handlers.
Manages client connections, incident room subscriptions,
and inbound events from TRAILHEAD and RELAY.

Event flow:
  TRAILHEAD/RELAY → POST /api/map/.../position → emit gps_update → all BASECAMP clients
  BASECAMP client → join_incident room → receives all events for that incident
  Check-in watcher → emit missed_checkin → IC dashboard alert
"""

import logging
from flask_socketio import SocketIO, join_room, leave_room, emit

log = logging.getLogger("basecamp.events")

# Connected clients: {sid: {"incident_id": str, "user": dict}}
_connected: dict = {}


def register(socketio: SocketIO):
    """
    Register all SocketIO event handlers on the given SocketIO instance.
    Called once from app.py create_app().
    """

    # -----------------------------------------------------------------------
    # Connection lifecycle
    # -----------------------------------------------------------------------

    @socketio.on("connect")
    def on_connect():
        log.debug("Client connected: %s", _sid())
        _connected[_sid()] = {}
        emit("connected", {"message": "Connected to BASECAMP"})

    @socketio.on("disconnect")
    def on_disconnect():
        log.debug("Client disconnected: %s", _sid())
        _connected.pop(_sid(), None)

    # -----------------------------------------------------------------------
    # Incident room management
    # Room-based broadcasting: only clients watching an incident
    # receive its events (GPS updates, alerts, radio log, etc.)
    # -----------------------------------------------------------------------

    @socketio.on("join_incident")
    def on_join_incident(data):
        """
        Client joins the SocketIO room for a specific incident.
        After joining, client receives all events scoped to that incident.
        Expected data: {"incident_id": "uuid"}
        """
        incident_id = data.get("incident_id")
        if not incident_id:
            emit("error", {"message": "incident_id is required"})
            return

        join_room(incident_id)
        _connected[_sid()] = {"incident_id": incident_id}
        log.info("Client %s joined incident room: %s", _sid(), incident_id)

        emit("joined_incident", {
            "incident_id": incident_id,
            "message":     f"Joined incident {incident_id}",
        })

    @socketio.on("leave_incident")
    def on_leave_incident(data):
        """
        Client leaves an incident room.
        Expected data: {"incident_id": "uuid"}
        """
        incident_id = data.get("incident_id")
        if incident_id:
            leave_room(incident_id)
            _connected[_sid()] = {}
            log.debug("Client %s left incident room: %s", _sid(), incident_id)
            emit("left_incident", {"incident_id": incident_id})

    # -----------------------------------------------------------------------
    # Inbound GPS position from TRAILHEAD (WebSocket path)
    # Alternative to POST /api/map/.../position for lower latency
    # -----------------------------------------------------------------------

    @socketio.on("position_update")
    def on_position_update(data):
        """
        Receive a GPS fix from a TRAILHEAD client via WebSocket.
        Persists to DB then broadcasts to incident room.
        Expected data: {incident_id, personnel_id, lat, lng, elevation?, accuracy?, recorded_at?}
        """
        incident_id  = data.get("incident_id")
        personnel_id = data.get("personnel_id")
        lat          = data.get("lat")
        lng          = data.get("lng")

        if not all([incident_id, personnel_id, lat is not None, lng is not None]):
            emit("error", {"message": "incident_id, personnel_id, lat, lng required"})
            return

        try:
            from core.db import append_only_insert, now_utc
            record = {
                "incident_id":  incident_id,
                "personnel_id": personnel_id,
                "lat":          float(lat),
                "lng":          float(lng),
                "elevation":    data.get("elevation"),
                "accuracy":     data.get("accuracy"),
                "recorded_at":  data.get("recorded_at", now_utc()),
                "source":       "trailhead",
            }
            append_only_insert("gps_tracks", record)

            # Broadcast to everyone in the incident room
            socketio.emit("gps_update", record, room=incident_id)

        except Exception as e:
            log.error("Error persisting position update: %s", e)
            emit("error", {"message": "Failed to persist position"})

    # -----------------------------------------------------------------------
    # Ping / heartbeat — keeps connections alive through NAT/proxies
    # -----------------------------------------------------------------------

    @socketio.on("ping")
    def on_ping():
        from core.db import now_utc
        emit("pong", {"server_time": now_utc()})

    # -----------------------------------------------------------------------
    # Request current positions for an incident (on-demand map refresh)
    # -----------------------------------------------------------------------

    @socketio.on("request_positions")
    def on_request_positions(data):
        """
        Client requests the latest GPS fix for all operators on an incident.
        Used when first loading the map after joining an incident room.
        """
        incident_id = data.get("incident_id")
        if not incident_id:
            emit("error", {"message": "incident_id is required"})
            return

        try:
            from core.db import get_recent_gps
            positions = get_recent_gps(incident_id)
            emit("positions_snapshot", {
                "incident_id": incident_id,
                "positions":   positions,
            })
        except Exception as e:
            log.error("Error fetching positions: %s", e)
            emit("error", {"message": "Failed to fetch positions"})


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _sid() -> str:
    """Return current request's session ID."""
    from flask import request
    return getattr(request, "sid", "unknown")
