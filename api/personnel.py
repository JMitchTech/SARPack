"""
SARPack 2.0 — api/personnel.py
Personnel roster, certifications, and asset management for WARDEN.
MFA verification required for sensitive operations.
"""

import uuid
from datetime import datetime, timezone, date
from flask import Blueprint, request, jsonify, g

from core.database import get_db, row_to_dict, rows_to_list
from core.auth import (
    require_auth, require_admin, require_logistics,
    verify_mfa_code, get_mfa_secret, user_requires_mfa,
    audit,
)

bp = Blueprint("personnel", __name__)


# ── MFA gate for WARDEN ───────────────────────────────────────────────────────

def _verify_warden_mfa(user_id: str, mfa_code: str = None) -> bool:
    """
    Check if WARDEN MFA is satisfied for this request.
    If user has MFA enabled, a valid code must be provided.
    If user does not have MFA enabled, access is granted.
    """
    if not user_requires_mfa(user_id):
        return True
    if not mfa_code:
        return False
    secret = get_mfa_secret(user_id)
    if not secret:
        return False
    return verify_mfa_code(secret, mfa_code)


def _require_warden_mfa(f):
    """
    Decorator that enforces WARDEN MFA on sensitive routes.
    MFA code passed in X-MFA-Code header or mfa_code in JSON body.
    """
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        mfa_code = (
            request.headers.get("X-MFA-Code") or
            (request.get_json(silent=True) or {}).get("mfa_code")
        )
        if not _verify_warden_mfa(g.user_id, mfa_code):
            return jsonify({
                "error": "WARDEN MFA verification required",
                "mfa_required": True,
            }), 403
        return f(*args, **kwargs)
    return decorated


# ── Helpers ───────────────────────────────────────────────────────────────────

def _person_or_404(personnel_id: str) -> dict:
    db  = get_db()
    row = db.execute(
        "SELECT * FROM personnel WHERE id = ?", (personnel_id,)
    ).fetchone()
    return row_to_dict(row)


def _enrich_person(person: dict) -> dict:
    """Add certifications and current deployment status."""
    db = get_db()

    person["certifications"] = rows_to_list(db.execute(
        "SELECT * FROM certifications WHERE personnel_id = ? ORDER BY cert_type",
        (person["id"],)
    ).fetchall())

    # Flag expiring/expired certs
    today = date.today().isoformat()
    for cert in person["certifications"]:
        if not cert.get("expiry_date"):
            cert["expiry_status"] = "none"
        elif cert["expiry_date"] < today:
            cert["expiry_status"] = "expired"
        elif cert["expiry_date"] <= date.today().replace(
                month=date.today().month + 2 if date.today().month <= 10
                else (date.today().month - 10),
                year=date.today().year if date.today().month <= 10
                else date.today().year + 1
        ).isoformat():
            cert["expiry_status"] = "expiring_soon"
        else:
            cert["expiry_status"] = "valid"

    # Current deployment
    person["current_deployment"] = row_to_dict(db.execute(
        """SELECT d.*, i.incident_name, i.incident_number
           FROM deployments d
           JOIN incidents i ON d.incident_id = i.id
           WHERE d.personnel_id = ? AND d.status = 'active'
           ORDER BY d.checked_in_at DESC LIMIT 1""",
        (person["id"],)
    ).fetchone())

    return person


# ── Personnel CRUD ────────────────────────────────────────────────────────────

@bp.route("/", methods=["GET"])
@require_auth
def list_personnel():
    """
    List all personnel. Supports search and filtering.
    Query params: search, is_active, cert_type, limit, offset
    """
    db        = get_db()
    search    = request.args.get("search", "").strip()
    is_active = request.args.get("is_active", "1")
    cert_type = request.args.get("cert_type")
    limit     = min(int(request.args.get("limit",  200)), 1000)
    offset    = int(request.args.get("offset", 0))

    query  = """SELECT DISTINCT p.* FROM personnel p"""
    params = []

    if cert_type:
        query += " JOIN certifications c ON p.id = c.personnel_id"

    query += " WHERE 1=1"

    if is_active in ("0", "1"):
        query += " AND p.is_active = ?"
        params.append(int(is_active))

    if search:
        query += """ AND (
            p.first_name LIKE ? OR p.last_name LIKE ? OR
            p.call_sign  LIKE ? OR p.email     LIKE ? OR
            p.phone      LIKE ? OR p.home_agency LIKE ?
        )"""
        s = f"%{search}%"
        params += [s, s, s, s, s, s]

    if cert_type:
        query += " AND c.cert_type = ?"
        params.append(cert_type)

    query += " ORDER BY p.last_name, p.first_name LIMIT ? OFFSET ?"
    params += [limit, offset]

    rows      = db.execute(query, params).fetchall()
    personnel = [row_to_dict(r) for r in rows]

    # Add cert summary to each person (not full enrichment for list view)
    for person in personnel:
        certs = db.execute(
            "SELECT cert_type, expiry_date FROM certifications WHERE personnel_id = ?",
            (person["id"],)
        ).fetchall()
        person["cert_count"]  = len(certs)
        person["cert_types"]  = [c["cert_type"] for c in certs]
        person["is_deployed"] = bool(db.execute(
            "SELECT id FROM deployments WHERE personnel_id = ? AND status = 'active'",
            (person["id"],)
        ).fetchone())

    total = db.execute(
        "SELECT COUNT(*) as c FROM personnel WHERE is_active = ?",
        (int(is_active) if is_active in ("0","1") else 1,)
    ).fetchone()["c"]

    return jsonify({
        "personnel": personnel,
        "total":     total,
        "limit":     limit,
        "offset":    offset,
    }), 200


@bp.route("/<personnel_id>", methods=["GET"])
@require_auth
def get_person(personnel_id):
    """Get a single personnel record with full details."""
    person = _person_or_404(personnel_id)
    if not person:
        return jsonify({"error": "Personnel not found"}), 404
    return jsonify(_enrich_person(person)), 200


@bp.route("/", methods=["POST"])
@require_logistics
def create_person():
    """Create a new personnel record."""
    data = request.get_json(silent=True) or {}

    required = ["first_name", "last_name"]
    missing  = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    db = get_db()

    # Check call sign uniqueness
    if data.get("call_sign"):
        existing = db.execute(
            "SELECT id FROM personnel WHERE call_sign = ?",
            (data["call_sign"].upper().strip(),)
        ).fetchone()
        if existing:
            return jsonify({"error": "Call sign already exists"}), 409

    person_id = str(uuid.uuid4())
    now       = datetime.now(timezone.utc).isoformat()

    db.execute(
        """INSERT INTO personnel
           (id, first_name, last_name, call_sign, blood_type,
            phone, email, home_agency, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            person_id,
            data["first_name"].strip(),
            data["last_name"].strip(),
            data["call_sign"].upper().strip() if data.get("call_sign") else None,
            data.get("blood_type"),
            data.get("phone"),
            data.get("email"),
            data.get("home_agency"),
            data.get("notes"),
            now, now,
        )
    )
    db.commit()

    audit("create_personnel", target_type="personnel", target_id=person_id,
          detail=f"{data['first_name']} {data['last_name']}")
    return jsonify(_enrich_person(_person_or_404(person_id))), 201


@bp.route("/<personnel_id>", methods=["PATCH"])
@require_logistics
def update_person(personnel_id):
    """Update a personnel record."""
    person = _person_or_404(personnel_id)
    if not person:
        return jsonify({"error": "Personnel not found"}), 404

    data    = request.get_json(silent=True) or {}
    db      = get_db()
    updates = []
    params  = []

    updatable = [
        "first_name", "last_name", "call_sign", "blood_type",
        "phone", "email", "home_agency", "notes", "is_active",
    ]

    for field in updatable:
        if field in data:
            val = data[field]
            if field == "call_sign" and val:
                val = val.upper().strip()
                # Check uniqueness excluding current record
                existing = db.execute(
                    "SELECT id FROM personnel WHERE call_sign = ? AND id != ?",
                    (val, personnel_id)
                ).fetchone()
                if existing:
                    return jsonify({"error": "Call sign already exists"}), 409
            updates.append(f"{field} = ?")
            params.append(val)

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    updates.append("updated_at = datetime('now')")
    params.append(personnel_id)

    db.execute(
        f"UPDATE personnel SET {', '.join(updates)} WHERE id = ?", params
    )
    db.commit()

    audit("update_personnel", target_type="personnel", target_id=personnel_id)
    return jsonify(_enrich_person(_person_or_404(personnel_id))), 200


@bp.route("/<personnel_id>", methods=["DELETE"])
@require_admin
@_require_warden_mfa
def delete_person(personnel_id):
    """
    Soft-delete a personnel record (sets is_active = 0).
    Requires admin role AND WARDEN MFA.
    Hard delete is intentionally not exposed.
    """
    person = _person_or_404(personnel_id)
    if not person:
        return jsonify({"error": "Personnel not found"}), 404

    db = get_db()
    db.execute(
        "UPDATE personnel SET is_active = 0, updated_at = datetime('now') WHERE id = ?",
        (personnel_id,)
    )
    db.commit()

    audit("deactivate_personnel", target_type="personnel", target_id=personnel_id,
          detail=f"{person['first_name']} {person['last_name']}")
    return jsonify({"message": "Personnel deactivated"}), 200


# ── Certifications ────────────────────────────────────────────────────────────

@bp.route("/<personnel_id>/certifications", methods=["GET"])
@require_auth
def list_certifications(personnel_id):
    """List all certifications for a personnel member."""
    db   = get_db()
    rows = db.execute(
        "SELECT * FROM certifications WHERE personnel_id = ? ORDER BY cert_type",
        (personnel_id,)
    ).fetchall()
    return jsonify(rows_to_list(rows)), 200


@bp.route("/<personnel_id>/certifications", methods=["POST"])
@require_logistics
def add_certification(personnel_id):
    """Add a certification to a personnel record."""
    person = _person_or_404(personnel_id)
    if not person:
        return jsonify({"error": "Personnel not found"}), 404

    data      = request.get_json(silent=True) or {}
    cert_type = data.get("cert_type", "").strip()

    if not cert_type:
        return jsonify({"error": "cert_type is required"}), 400

    cert_id = str(uuid.uuid4())
    db      = get_db()

    db.execute(
        """INSERT INTO certifications
           (id, personnel_id, cert_type, cert_number, issued_date,
            expiry_date, issuing_body, is_verified, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            cert_id, personnel_id, cert_type,
            data.get("cert_number"),
            data.get("issued_date"),
            data.get("expiry_date"),
            data.get("issuing_body"),
            1 if data.get("is_verified") else 0,
        )
    )
    db.commit()

    audit("add_certification", target_type="personnel", target_id=personnel_id,
          detail=cert_type)
    return jsonify(row_to_dict(db.execute(
        "SELECT * FROM certifications WHERE id = ?", (cert_id,)
    ).fetchone())), 201


@bp.route("/<personnel_id>/certifications/<cert_id>", methods=["PATCH"])
@require_logistics
def update_certification(personnel_id, cert_id):
    """Update a certification record."""
    db   = get_db()
    cert = db.execute(
        "SELECT id FROM certifications WHERE id = ? AND personnel_id = ?",
        (cert_id, personnel_id)
    ).fetchone()

    if not cert:
        return jsonify({"error": "Certification not found"}), 404

    data    = request.get_json(silent=True) or {}
    updates = []
    params  = []

    updatable = [
        "cert_type", "cert_number", "issued_date",
        "expiry_date", "issuing_body", "is_verified",
    ]
    for field in updatable:
        if field in data:
            updates.append(f"{field} = ?")
            params.append(data[field])

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    params.append(cert_id)
    db.execute(
        f"UPDATE certifications SET {', '.join(updates)} WHERE id = ?", params
    )
    db.commit()

    return jsonify(row_to_dict(db.execute(
        "SELECT * FROM certifications WHERE id = ?", (cert_id,)
    ).fetchone())), 200


@bp.route("/<personnel_id>/certifications/<cert_id>", methods=["DELETE"])
@require_logistics
def delete_certification(personnel_id, cert_id):
    """Delete a certification record."""
    db = get_db()
    db.execute(
        "DELETE FROM certifications WHERE id = ? AND personnel_id = ?",
        (cert_id, personnel_id)
    )
    db.commit()
    return jsonify({"message": "Certification removed"}), 200


# ── Expiry alerts ─────────────────────────────────────────────────────────────

@bp.route("/certifications/expiring", methods=["GET"])
@require_auth
def expiring_certifications():
    """
    List certifications expiring within the next 60 days or already expired.
    Used by WARDEN dashboard to surface urgent renewals.
    """
    db   = get_db()
    rows = db.execute(
        """SELECT c.*, p.first_name, p.last_name, p.call_sign
           FROM certifications c
           JOIN personnel p ON c.personnel_id = p.id
           WHERE p.is_active = 1
             AND c.expiry_date IS NOT NULL
             AND c.expiry_date <= date('now', '+60 days')
           ORDER BY c.expiry_date ASC""",
    ).fetchall()
    return jsonify(rows_to_list(rows)), 200


# ── Assets ────────────────────────────────────────────────────────────────────

@bp.route("/assets", methods=["GET"])
@require_auth
def list_assets():
    """List all assets."""
    db     = get_db()
    status = request.args.get("status")
    atype  = request.args.get("asset_type")

    query  = "SELECT * FROM assets WHERE 1=1"
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)
    if atype:
        query += " AND asset_type = ?"
        params.append(atype)

    query += " ORDER BY name"
    rows   = db.execute(query, params).fetchall()
    assets = rows_to_list(rows)

    # Add drone stream info if applicable
    for asset in assets:
        if asset["asset_type"] == "drone":
            drone = db.execute(
                "SELECT * FROM drone_assets WHERE asset_id = ?",
                (asset["id"],)
            ).fetchone()
            asset["drone"] = row_to_dict(drone)

    return jsonify(assets), 200


@bp.route("/assets", methods=["POST"])
@require_logistics
def create_asset():
    """Create a new asset record."""
    data = request.get_json(silent=True) or {}

    required = ["name", "asset_type"]
    missing  = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    valid_types = {"vehicle","atv","drone","k9","boat","equipment","other"}
    if data["asset_type"] not in valid_types:
        return jsonify({"error": f"Invalid asset_type"}), 400

    asset_id = str(uuid.uuid4())
    now      = datetime.now(timezone.utc).isoformat()
    db       = get_db()

    db.execute(
        """INSERT INTO assets
           (id, name, asset_type, serial_number, owner_agency,
            status, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'available', ?, ?, ?)""",
        (
            asset_id,
            data["name"].strip(),
            data["asset_type"],
            data.get("serial_number"),
            data.get("owner_agency"),
            data.get("notes"),
            now, now,
        )
    )

    # If drone, create drone record
    if data["asset_type"] == "drone":
        db.execute(
            """INSERT INTO drone_assets
               (id, asset_id, stream_url, operator_id, is_registered)
               VALUES (?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()), asset_id,
                data.get("stream_url"),
                data.get("operator_id"),
                1 if data.get("stream_url") else 0,
            )
        )

    db.commit()

    audit("create_asset", target_type="asset", target_id=asset_id,
          detail=data["name"])
    return jsonify(row_to_dict(db.execute(
        "SELECT * FROM assets WHERE id = ?", (asset_id,)
    ).fetchone())), 201


@bp.route("/assets/<asset_id>", methods=["PATCH"])
@require_logistics
def update_asset(asset_id):
    """Update an asset record including drone stream URL."""
    db    = get_db()
    asset = db.execute(
        "SELECT * FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()

    if not asset:
        return jsonify({"error": "Asset not found"}), 404

    data    = request.get_json(silent=True) or {}
    updates = []
    params  = []

    updatable = ["name","status","serial_number","owner_agency","notes"]
    for field in updatable:
        if field in data:
            updates.append(f"{field} = ?")
            params.append(data[field])

    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(asset_id)
        db.execute(
            f"UPDATE assets SET {', '.join(updates)} WHERE id = ?", params
        )

    # Update drone stream URL if provided
    if asset["asset_type"] == "drone" and "stream_url" in data:
        db.execute(
            """UPDATE drone_assets SET stream_url = ?, is_registered = ?
               WHERE asset_id = ?""",
            (data["stream_url"], 1 if data["stream_url"] else 0, asset_id)
        )

        # Notify all connected windows that drone stream is available
        if data["stream_url"]:
            try:
                from app import socketio
                socketio.emit("drone_stream_ready", {
                    "asset_id":   asset_id,
                    "asset_name": asset["name"],
                    "stream_url": data["stream_url"],
                })
            except Exception:
                pass

    db.commit()
    audit("update_asset", target_type="asset", target_id=asset_id)
    return jsonify(row_to_dict(db.execute(
        "SELECT * FROM assets WHERE id = ?", (asset_id,)
    ).fetchone())), 200


# ── Training materials (WARDEN) ───────────────────────────────────────────────

@bp.route("/training", methods=["GET"])
@require_auth
def list_training():
    """List all training materials."""
    db       = get_db()
    category = request.args.get("category")
    query    = "SELECT * FROM training_materials WHERE 1=1"
    params   = []

    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY created_at DESC"
    rows   = db.execute(query, params).fetchall()
    return jsonify(rows_to_list(rows)), 200


@bp.route("/training", methods=["POST"])
@require_logistics
def upload_training():
    """
    Upload a training material.
    Accepts multipart/form-data with file + metadata.
    """
    import os
    from core.config import Config
    from werkzeug.utils import secure_filename

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in Config.ALLOWED_EXTENSIONS:
        return jsonify({"error": f"File type not allowed. Allowed: {', '.join(Config.ALLOWED_EXTENSIONS)}"}), 400

    filename  = secure_filename(file.filename)
    unique_fn = f"{uuid.uuid4()}_{filename}"
    save_path = os.path.join(Config.UPLOAD_FOLDER, unique_fn)
    file.save(save_path)

    material_id = str(uuid.uuid4())
    db          = get_db()

    db.execute(
        """INSERT INTO training_materials
           (id, title, description, file_path, file_type,
            uploaded_by, category, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            material_id,
            request.form.get("title", filename),
            request.form.get("description"),
            unique_fn,
            ext,
            g.user_id,
            request.form.get("category", "general"),
        )
    )
    db.commit()

    audit("upload_training", target_type="training", target_id=material_id,
          detail=filename)
    return jsonify(row_to_dict(db.execute(
        "SELECT * FROM training_materials WHERE id = ?", (material_id,)
    ).fetchone())), 201


@bp.route("/training/<material_id>", methods=["DELETE"])
@require_admin
def delete_training(material_id):
    """Delete a training material."""
    import os
    from core.config import Config

    db       = get_db()
    material = db.execute(
        "SELECT * FROM training_materials WHERE id = ?", (material_id,)
    ).fetchone()

    if not material:
        return jsonify({"error": "Training material not found"}), 404

    # Remove file from disk
    file_path = os.path.join(Config.UPLOAD_FOLDER, material["file_path"])
    if os.path.exists(file_path):
        os.remove(file_path)

    db.execute("DELETE FROM training_materials WHERE id = ?", (material_id,))
    db.commit()

    audit("delete_training", target_type="training", target_id=material_id)
    return jsonify({"message": "Training material deleted"}), 200


# ── Radio registry ────────────────────────────────────────────────────────────

@bp.route("/<personnel_id>/radio", methods=["GET"])
@require_auth
def get_radio_registry(personnel_id):
    """Get radio registry entry for a personnel member."""
    db  = get_db()
    row = db.execute(
        "SELECT * FROM radio_registry WHERE personnel_id = ?",
        (personnel_id,)
    ).fetchone()
    return jsonify(row_to_dict(row) or {}), 200


@bp.route("/<personnel_id>/radio", methods=["POST"])
@require_logistics
def set_radio_registry(personnel_id):
    """Set or update radio registry for a personnel member."""
    data = request.get_json(silent=True) or {}
    db   = get_db()

    existing = db.execute(
        "SELECT id FROM radio_registry WHERE personnel_id = ?",
        (personnel_id,)
    ).fetchone()

    import json
    channels = data.get("programmed_channels", [])
    if isinstance(channels, list):
        channels = json.dumps(channels)

    if existing:
        db.execute(
            """UPDATE radio_registry
               SET radio_make=?, radio_model=?, radio_type=?,
                   programmed_channels=?, notes=?
               WHERE personnel_id=?""",
            (
                data.get("radio_make"),
                data.get("radio_model"),
                data.get("radio_type"),
                channels,
                data.get("notes"),
                personnel_id,
            )
        )
    else:
        db.execute(
            """INSERT INTO radio_registry
               (id, personnel_id, radio_make, radio_model, radio_type,
                programmed_channels, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                str(uuid.uuid4()), personnel_id,
                data.get("radio_make"),
                data.get("radio_model"),
                data.get("radio_type"),
                channels,
                data.get("notes"),
            )
        )

    db.commit()
    return jsonify(row_to_dict(db.execute(
        "SELECT * FROM radio_registry WHERE personnel_id = ?",
        (personnel_id,)
    ).fetchone())), 200