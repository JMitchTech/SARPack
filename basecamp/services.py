"""
SARPack — basecamp/services.py
Background services that run for the lifetime of the BASECAMP process.

1. Check-in watcher — monitors active deployments for overdue operators
   and fires missed_checkin alerts via SocketIO.

2. Sync broadcaster — pushes cloud sync status to connected clients
   every 30 seconds so the UI connectivity indicator stays current.

Both run as daemon threads started once from app.py.
"""

import logging
import threading
import time
from datetime import datetime, timezone, timedelta

log = logging.getLogger("basecamp.services")

# Default check-in interval — how long before an operator is considered overdue
# IC can override per-incident in future phases
DEFAULT_CHECKIN_INTERVAL_MINUTES = 30

_services_started = False


def start_background_services(socketio):
    """
    Start all background service threads.
    Safe to call multiple times — only starts once.
    """
    global _services_started
    if _services_started:
        return
    _services_started = True

    threading.Thread(
        target=_checkin_watcher,
        args=(socketio,),
        name="basecamp-checkin-watcher",
        daemon=True,
    ).start()

    threading.Thread(
        target=_sync_broadcaster,
        args=(socketio,),
        name="basecamp-sync-broadcaster",
        daemon=True,
    ).start()

    log.info("BASECAMP background services started")


# ---------------------------------------------------------------------------
# Check-in watcher
# ---------------------------------------------------------------------------

def _checkin_watcher(socketio):
    """
    Polls every 60 seconds for active deployments where the operator
    has not radioed in within DEFAULT_CHECKIN_INTERVAL_MINUTES.

    Logic:
        For each active deployment on an active incident:
        - Find the most recent radio_log entry for that personnel_id/incident_id
        - If none exists, use checked_in_at as the baseline
        - If now - last_contact > interval → flag as missed check-in
        - Emit missed_checkin SocketIO event to the incident room
        - Log to radio_log so it appears in the IC's feed

    Avoids duplicate alerts by checking if a missed_checkin entry
    already exists within the last interval window.
    """
    log.info("Check-in watcher started (interval: %dm)", DEFAULT_CHECKIN_INTERVAL_MINUTES)

    while True:
        time.sleep(60)  # check every minute
        try:
            _run_checkin_check(socketio)
        except Exception as e:
            log.exception("Error in check-in watcher: %s", e)


def _run_checkin_check(socketio):
    """Run one check-in audit cycle."""
    from core.db import local_db, append_only_insert, now_utc, get_record

    now = datetime.now(timezone.utc)
    overdue_cutoff = (
        now - timedelta(minutes=DEFAULT_CHECKIN_INTERVAL_MINUTES)
    ).isoformat()
    dedup_cutoff = (
        now - timedelta(minutes=DEFAULT_CHECKIN_INTERVAL_MINUTES)
    ).isoformat()

    with local_db() as db:
        # All active deployments on active incidents
        deployments = db.execute(
            """
            SELECT d.id, d.incident_id, d.personnel_id, d.checked_in_at,
                   p.first_name, p.last_name, p.call_sign, p.phone
            FROM deployments d
            JOIN incidents i ON i.id = d.incident_id
            JOIN personnel p ON p.id = d.personnel_id
            WHERE d.status = 'active'
            AND i.status = 'active'
            """,
        ).fetchall()

        for dep in deployments:
            dep = dict(dep)

            # Last contact = most recent radio_log entry (non-missed)
            last_contact = db.execute(
                """
                SELECT MAX(logged_at) as last
                FROM radio_log
                WHERE incident_id = ? AND personnel_id = ?
                AND is_missed_checkin = 0
                """,
                (dep["incident_id"], dep["personnel_id"]),
            ).fetchone()["last"]

            baseline = last_contact or dep["checked_in_at"]

            if not baseline or baseline > overdue_cutoff:
                continue  # checked in recently — all good

            # Check we haven't already fired an alert in this window
            recent_alert = db.execute(
                """
                SELECT id FROM radio_log
                WHERE incident_id = ? AND personnel_id = ?
                AND is_missed_checkin = 1
                AND logged_at >= ?
                """,
                (dep["incident_id"], dep["personnel_id"], dedup_cutoff),
            ).fetchone()

            if recent_alert:
                continue  # already alerted this window

            # Fire the missed check-in
            ts = now_utc()
            name = dep["call_sign"] or f"{dep['first_name']} {dep['last_name']}"
            message = f"MISSED CHECK-IN — {name} (last contact: {baseline[:16]})"

            entry_id = append_only_insert("radio_log", {
                "incident_id":       dep["incident_id"],
                "personnel_id":      dep["personnel_id"],
                "message":           message,
                "logged_at":         ts,
                "is_missed_checkin": 1,
                "source":            "watcher",
            })

            log.warning(
                "MISSED CHECK-IN: %s on incident %s (last contact: %s)",
                name, dep["incident_id"], baseline,
            )

            # Emit to incident room
            socketio.emit("missed_checkin", {
                "incident_id":  dep["incident_id"],
                "personnel_id": dep["personnel_id"],
                "name":         f"{dep['first_name']} {dep['last_name']}",
                "call_sign":    dep["call_sign"],
                "phone":        dep["phone"],
                "last_contact": baseline,
                "logged_at":    ts,
                "entry_id":     entry_id,
            }, room=dep["incident_id"])


# ---------------------------------------------------------------------------
# Sync status broadcaster
# ---------------------------------------------------------------------------

def _sync_broadcaster(socketio):
    """
    Broadcasts the current cloud sync status to all connected clients
    every 30 seconds. Powers the connectivity indicator in the BASECAMP
    status bar — green = synced, yellow = pending, red = offline.
    """
    log.info("Sync broadcaster started")

    while True:
        time.sleep(30)
        try:
            from core.sync import sync_status
            from core.db import now_utc
            status = sync_status()
            socketio.emit("sync_status", {
                **status,
                "broadcast_at": now_utc(),
            })
        except Exception as e:
            log.exception("Error in sync broadcaster: %s", e)
