"""
SARPack — sarpack.py
System tray launcher for the Toughbook base-of-operations deployment.
Starts all five apps as background subprocesses, opens BASECAMP in the
browser, and provides a tray menu to control each app individually.

Run from the SARPack root directory:
    python sarpack.py

Requires:
    pip install pystray pillow flask flask-socketio

Architecture mirrors arcane.py from ADS — single instance via mutex,
tray icon with per-app controls, graceful shutdown on quit.
"""

import os
import sys
import time
import signal
import logging
import subprocess
import threading
import webbrowser
import ctypes
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Bootstrap — ensure we can import core before anything else
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))

from core.config import config
from core.db import init_db
from core.sync import start_sync_engine, sync_status

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sarpack.launcher")


# ---------------------------------------------------------------------------
# Single instance lock (Windows mutex)
# ---------------------------------------------------------------------------

_MUTEX_NAME = "SARPack_Toughbook_Mutex"

def _acquire_instance_lock() -> bool:
    """
    Attempt to acquire a Windows named mutex.
    Returns True if this is the first instance, False if already running.
    On non-Windows systems, always returns True (no lock needed for dev).
    """
    if sys.platform != "win32":
        return True
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return False
    return True


# ---------------------------------------------------------------------------
# App definitions
# ---------------------------------------------------------------------------

APPS = {
    "basecamp": {
        "label":   "BASECAMP",
        "module":  "basecamp.app",
        "port":    config.PORT_BASECAMP,
        "color":   "#ff8c00",    # orange — Wizardwerks brand
    },
    "trailhead": {
        "label":   "TRAILHEAD",
        "module":  "trailhead.app",
        "port":    config.PORT_TRAILHEAD,
        "color":   "#29aacc",    # cyan
    },
    "relay": {
        "label":   "RELAY",
        "module":  "relay.app",
        "port":    config.PORT_RELAY,
        "color":   "#aa88ff",    # purple
    },
    "logbook": {
        "label":   "LOGBOOK",
        "module":  "logbook.app",
        "port":    config.PORT_LOGBOOK,
        "color":   "#00e676",    # green
    },
    "warden": {
        "label":   "WARDEN",
        "module":  "warden.app",
        "port":    config.PORT_WARDEN,
        "color":   "#ffd000",    # gold
    },
}

# Track running subprocesses
_procs: dict[str, subprocess.Popen | None] = {k: None for k in APPS}
_proc_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------

def start_app(name: str) -> bool:
    """
    Start a single app as a background subprocess.
    Returns True if started successfully, False if already running.
    """
    with _proc_lock:
        proc = _procs.get(name)
        if proc and proc.poll() is None:
            log.debug("%s already running (pid %d)", name, proc.pid)
            return False

        app = APPS[name]
        log.info("Starting %s on port %d", app["label"], app["port"])

        try:
            p = subprocess.Popen(
                [sys.executable, "-m", app["module"]],
                cwd=str(ROOT),
                env={**os.environ, "SARPACK_APP": name},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if sys.platform == "win32" else 0
                ),
            )
            _procs[name] = p
            log.info("%s started (pid %d)", app["label"], p.pid)
            return True
        except FileNotFoundError:
            log.warning(
                "%s module not found — skipping. "
                "This is expected during Phase 0 before app code is written.",
                app["label"]
            )
            return False
        except Exception as e:
            log.error("Failed to start %s: %s", app["label"], e)
            return False


def stop_app(name: str):
    """Gracefully stop a running app subprocess."""
    with _proc_lock:
        proc = _procs.get(name)
        if not proc or proc.poll() is not None:
            return

        app = APPS[name]
        log.info("Stopping %s (pid %d)", app["label"], proc.pid)

        try:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            log.info("%s stopped", app["label"])
        except Exception as e:
            log.error("Error stopping %s: %s", app["label"], e)
        finally:
            _procs[name] = None


def restart_app(name: str):
    """Stop then start an app."""
    stop_app(name)
    time.sleep(1)
    start_app(name)


def is_running(name: str) -> bool:
    """Return True if the app subprocess is alive."""
    proc = _procs.get(name)
    return bool(proc and proc.poll() is None)


def open_dashboard(name: str):
    """Open the app's dashboard in the default browser."""
    port = APPS[name]["port"]
    webbrowser.open(f"http://localhost:{port}")


# ---------------------------------------------------------------------------
# Tray icon drawing
# ---------------------------------------------------------------------------

def _make_tray_icon(size: int = 64) -> Image.Image:
    """
    Draw the SARPack tray icon — a shield with an 'S' consistent
    with Wizardwerks brand (dark background, orange/gold accent).
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Shield shape
    margin = 4
    cx = size // 2

    # Shield body
    draw.polygon([
        (margin, margin),
        (size - margin, margin),
        (size - margin, size * 0.6),
        (cx, size - margin),
        (margin, size * 0.6),
    ], fill="#ff8c00")

    # Inner shield (dark background)
    inner = margin + 5
    draw.polygon([
        (inner, inner),
        (size - inner, inner),
        (size - inner, size * 0.58),
        (cx, size - inner - 4),
        (inner, size * 0.58),
    ], fill="#0a0a0a")

    # 'S' letterform in gold
    draw.text(
        (cx - 6, size // 2 - 10),
        "S",
        fill="#ffd000",
    )

    return img


def _status_label(name: str) -> str:
    state = "running" if is_running(name) else "stopped"
    return f"{APPS[name]['label']} ({state})"


# ---------------------------------------------------------------------------
# Tray menu builder
# ---------------------------------------------------------------------------

def _build_menu(icon: pystray.Icon) -> pystray.Menu:
    """Build the right-click tray menu. Rebuilt on each click for live status."""

    items = []

    # Header — sync status
    sync = sync_status()
    online_str = "online" if sync.get("online") else "offline"
    items.append(pystray.MenuItem(
        f"SARPack — {online_str} | mode: {config.MODE}",
        None,
        enabled=False,
    ))
    items.append(pystray.Menu.SEPARATOR)

    # Per-app controls
    for name, app in APPS.items():
        running = is_running(name)
        label = app["label"]
        port = app["port"]

        def make_open(n=name):
            return lambda icon, item: open_dashboard(n)

        def make_restart(n=name):
            return lambda icon, item: threading.Thread(
                target=restart_app, args=(n,), daemon=True
            ).start()

        def make_stop(n=name):
            return lambda icon, item: stop_app(n)

        sub_items = [
            pystray.MenuItem(
                f"Open (port {port})", make_open(),
                enabled=running,
            ),
            pystray.MenuItem("Restart", make_restart()),
            pystray.MenuItem("Stop", make_stop(), enabled=running),
        ]

        status_icon = "● " if running else "○ "
        items.append(pystray.MenuItem(
            f"{status_icon}{label}",
            pystray.Menu(*sub_items),
        ))

    items.append(pystray.Menu.SEPARATOR)

    # Open BASECAMP (primary action)
    items.append(pystray.MenuItem(
        "Open BASECAMP",
        lambda icon, item: open_dashboard("basecamp"),
    ))

    items.append(pystray.Menu.SEPARATOR)

    # Quit
    items.append(pystray.MenuItem(
        "Quit SARPack",
        lambda icon, item: _shutdown(icon),
    ))

    return pystray.Menu(*items)


# ---------------------------------------------------------------------------
# Startup + shutdown
# ---------------------------------------------------------------------------

def _startup():
    """
    Full startup sequence:
    1. Validate config
    2. Initialize database
    3. Start sync engine
    4. Start all app subprocesses
    5. Open BASECAMP after a short delay
    """
    log.info("=" * 60)
    log.info("SARPack launcher starting")
    log.info(config.summary())

    try:
        config.validate()
    except ValueError as e:
        log.error("Configuration error:\n%s", e)
        sys.exit(1)

    init_db()
    log.info("Database initialized")

    start_sync_engine()
    log.info("Sync engine started")

    # Start all apps
    for name in APPS:
        start_app(name)
        time.sleep(0.3)  # stagger starts slightly

    # Open BASECAMP in browser after apps have time to bind their ports
    def open_basecamp():
        time.sleep(3)
        log.info("Opening BASECAMP in browser")
        open_dashboard("basecamp")

    threading.Thread(target=open_basecamp, daemon=True).start()
    log.info("Startup complete")


def _shutdown(icon: pystray.Icon):
    """Graceful shutdown — stop all apps then exit."""
    log.info("SARPack shutting down")

    for name in list(APPS.keys()):
        stop_app(name)

    icon.stop()
    log.info("SARPack stopped")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Watchdog — auto-restart crashed apps
# ---------------------------------------------------------------------------

def _watchdog():
    """
    Background thread that monitors app subprocesses and restarts any
    that have crashed unexpectedly. Checks every 15 seconds.
    Field operations cannot tolerate silent crashes.
    """
    while True:
        time.sleep(15)
        for name in APPS:
            proc = _procs.get(name)
            if proc is not None and proc.poll() is not None:
                # Process ended — check stderr for clue before restarting
                try:
                    _, stderr = proc.communicate(timeout=1)
                    if stderr:
                        log.error(
                            "%s crashed. stderr: %s",
                            APPS[name]["label"],
                            stderr.decode(errors="replace")[:500],
                        )
                except Exception:
                    pass

                log.warning(
                    "%s exited unexpectedly — restarting",
                    APPS[name]["label"]
                )
                with _proc_lock:
                    _procs[name] = None
                start_app(name)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not _acquire_instance_lock():
        log.error(
            "SARPack is already running. "
            "Check the system tray or Task Manager."
        )
        sys.exit(1)

    _startup()

    # Start watchdog
    threading.Thread(target=_watchdog, daemon=True, name="sarpack-watchdog").start()

    # Build and run tray icon (blocks main thread)
    icon = pystray.Icon(
        name="SARPack",
        icon=_make_tray_icon(),
        title="SARPack — Keystone Rescue Service",
        menu=pystray.Menu(lambda: _build_menu(icon)),
    )

    log.info("Tray icon active. Right-click for controls.")
    icon.run()
