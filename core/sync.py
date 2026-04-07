"""
SARPack — core/sync.py
Background sync engine. Replays the SQLite outbox queue to PostgreSQL
whenever connectivity is available. Runs as a daemon thread — survives
app restarts because the outbox persists in SQLite.

Usage (call once at app startup):
    from core.sync import start_sync_engine
    start_sync_engine()
"""

import json
import logging
import threading
import time
import socket
from typing import Optional

from core.config import config
from core.db import (
    get_pending_outbox,
    mark_outbox_synced,
    mark_outbox_failed,
    get_cloud_conn,
)

log = logging.getLogger("sarpack.sync")

_sync_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_last_sync_result: dict = {
    "synced": 0,
    "failed": 0,
    "last_attempt": None,
    "last_success": None,
    "online": False,
}


# ---------------------------------------------------------------------------
# Connectivity check
# ---------------------------------------------------------------------------

def is_online(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.0) -> bool:
    """
    Quick connectivity check. Attempts a TCP connection to a known host.
    Does not send any data — just verifies the network is reachable.
    Falls back gracefully if network is completely down.
    """
    try:
        socket.setdefaulttimeout(timeout)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((host, port))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Replay logic
# ---------------------------------------------------------------------------

def _apply_to_cloud(conn, entry: dict):
    """
    Replay a single outbox entry to the cloud PostgreSQL database.
    Handles INSERT, UPDATE, DELETE operations.
    Uses ON CONFLICT DO UPDATE for inserts so replays are idempotent.
    """
    table = entry["table_name"]
    operation = entry["operation"]
    payload = json.loads(entry["payload"])

    with conn.cursor() as cur:
        if operation == "INSERT":
            cols = list(payload.keys())
            values = [payload[c] for c in cols]
            col_str = ", ".join(f'"{c}"' for c in cols)
            placeholder_str = ", ".join("%s" for _ in cols)
            # Upsert — safe to replay if already synced
            update_str = ", ".join(
                f'"{c}" = EXCLUDED."{c}"'
                for c in cols
                if c != "id"
            )
            cur.execute(
                f'INSERT INTO {table} ({col_str}) VALUES ({placeholder_str}) '
                f'ON CONFLICT (id) DO UPDATE SET {update_str}',
                values,
            )

        elif operation == "UPDATE":
            cols = [c for c in payload.keys() if c not in ("id",)]
            values = [payload[c] for c in cols] + [payload["id"]]
            set_str = ", ".join(f'"{c}" = %s' for c in cols)
            cur.execute(
                f'UPDATE {table} SET {set_str} WHERE id = %s',
                values,
            )

        elif operation == "DELETE":
            cur.execute(
                f'DELETE FROM {table} WHERE id = %s',
                (payload["id"],),
            )


def run_sync_cycle() -> dict:
    """
    Run one sync cycle: fetch pending outbox entries, replay to cloud.
    Returns a summary dict with counts of synced/failed entries.
    Safe to call manually for testing or forced sync.
    """
    summary = {"synced": 0, "failed": 0}

    if config.MODE == "local":
        return summary  # sync disabled in local mode

    if not is_online():
        _last_sync_result["online"] = False
        log.debug("Sync skipped — no network connectivity")
        return summary

    _last_sync_result["online"] = True

    pending = get_pending_outbox(limit=config.SYNC_BATCH_SIZE)
    if not pending:
        return summary

    log.info("Sync cycle: %d pending entries", len(pending))

    try:
        conn = get_cloud_conn()
    except Exception as e:
        log.warning("Cannot connect to cloud DB: %s", e)
        return summary

    try:
        for entry in pending:
            if entry["sync_attempts"] >= config.SYNC_MAX_RETRIES:
                log.error(
                    "Outbox entry %s exceeded max retries (%d). "
                    "Last error: %s. Skipping.",
                    entry["id"], config.SYNC_MAX_RETRIES, entry["last_error"]
                )
                continue

            try:
                _apply_to_cloud(conn, entry)
                conn.commit()
                mark_outbox_synced(entry["id"])
                summary["synced"] += 1
                log.debug("Synced %s %s/%s", entry["operation"],
                          entry["table_name"], entry["record_id"])
            except Exception as e:
                conn.rollback()
                mark_outbox_failed(entry["id"], str(e))
                summary["failed"] += 1
                log.warning(
                    "Failed to sync %s %s/%s: %s",
                    entry["operation"], entry["table_name"], entry["record_id"], e
                )
    finally:
        conn.close()

    _last_sync_result["synced"] += summary["synced"]
    _last_sync_result["failed"] += summary["failed"]
    _last_sync_result["last_success"] = (
        _last_sync_result.get("last_attempt") if summary["synced"] > 0
        else _last_sync_result["last_success"]
    )

    if summary["synced"] or summary["failed"]:
        log.info(
            "Sync complete: %d synced, %d failed",
            summary["synced"], summary["failed"]
        )

    return summary


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------

def _sync_loop():
    """
    Background thread target. Runs sync cycles on an interval until stopped.
    Daemon thread — exits automatically when the main process exits.
    """
    log.info(
        "Sync engine started | interval=%ds | mode=%s",
        config.SYNC_INTERVAL_SECONDS, config.MODE
    )
    while not _stop_event.is_set():
        _last_sync_result["last_attempt"] = (
            __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()
        )
        try:
            run_sync_cycle()
        except Exception as e:
            log.exception("Unexpected error in sync cycle: %s", e)

        # Wait for interval or until stop is signaled
        _stop_event.wait(timeout=config.SYNC_INTERVAL_SECONDS)

    log.info("Sync engine stopped")


def start_sync_engine():
    """
    Start the background sync thread. Safe to call multiple times —
    only one thread will run at a time.
    Call once during app initialization.
    """
    global _sync_thread

    if config.MODE == "local":
        log.info("Sync engine disabled (MODE=local)")
        return

    if _sync_thread and _sync_thread.is_alive():
        log.debug("Sync engine already running")
        return

    _stop_event.clear()
    _sync_thread = threading.Thread(
        target=_sync_loop,
        name="sarpack-sync",
        daemon=True,
    )
    _sync_thread.start()


def stop_sync_engine():
    """
    Signal the sync thread to stop. Blocks until it exits or times out.
    Call during graceful shutdown.
    """
    _stop_event.set()
    if _sync_thread:
        _sync_thread.join(timeout=10)


def sync_status() -> dict:
    """
    Return current sync engine status.
    Used by BASECAMP dashboard to show connectivity indicator.
    """
    return {
        **_last_sync_result,
        "mode": config.MODE,
        "interval_seconds": config.SYNC_INTERVAL_SECONDS,
        "thread_alive": _sync_thread.is_alive() if _sync_thread else False,
    }
