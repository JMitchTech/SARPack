"""
SARPack 2.0 — portal/routes.py
Serves the portal shell and standalone popout pages.
"""

from flask import Blueprint, render_template, request, redirect, url_for

bp = Blueprint("portal", __name__)


@bp.route("/")
def index():
    """Main portal shell — all modules load as tabs inside this page."""
    return render_template("index.html")


@bp.route("/popout/<module>")
def popout(module):
    """
    Standalone module page for popout windows.
    Each module can render as a full-page standalone when opened
    in a new browser window (second monitor support).
    Valid modules: basecamp | warden | logbook | relay
    """
    valid = {"basecamp", "warden", "logbook", "relay"}
    if module not in valid:
        return redirect(url_for("portal.index"))
    return render_template(f"popout_{module}.html", popout=True)


@bp.route("/drone")
def drone_feed():
    """Standalone drone feed window."""
    asset_id = request.args.get("asset")
    return render_template("drone_feed.html", asset_id=asset_id)