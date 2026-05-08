"""
SARPack 2.0 — api/users.py
User management, login, logout, MFA setup and verification.
"""

import uuid
from flask import Blueprint, request, jsonify, g

from core.database import get_db, row_to_dict, rows_to_list
from core.auth import (
    authenticate_user, create_user, get_user_by_id,
    generate_token, get_token_from_request, decode_token,
    hash_password, check_password,
    generate_mfa_secret, get_mfa_qr_code, verify_mfa_code,
    enable_mfa, disable_mfa, user_requires_mfa, get_mfa_secret,
    require_auth, require_admin, require_ic,
    audit,
)

bp = Blueprint("users", __name__)


# ── Login ─────────────────────────────────────────────────────────────────────

@bp.route("/login", methods=["POST"])
def login():
    """
    Step 1 of login — validate username + password.
    If user has MFA enabled, returns mfa_required: true.
    Client must then call /login/mfa with the TOTP code.
    """
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    user = authenticate_user(username, password)
    if not user:
        return jsonify({"error": "Invalid username or password"}), 401

    if user["must_change_password"]:
        return jsonify({
            "error": "Password change required",
            "must_change_password": True,
            "user_id": user["id"],
        }), 403

    # If MFA is enabled, return a partial auth signal
    if user["mfa_enabled"]:
        # Issue a short-lived pre-auth token (no full access)
        pre_token = generate_token(user["id"], "__mfa_pending__")
        return jsonify({
            "mfa_required": True,
            "pre_token":    pre_token,
            "username":     user["username"],
        }), 200

    # Full login — issue JWT
    token = generate_token(user["id"], user["role"])
    audit("login", target_type="user", target_id=user["id"])

    return jsonify({
        "token":    token,
        "id":       user["id"],
        "username": user["username"],
        "role":     user["role"],
        "personnel_id": user["personnel_id"],
        "mfa_enabled":  user["mfa_enabled"],
    }), 200


@bp.route("/login/mfa", methods=["POST"])
def login_mfa():
    """
    Step 2 of login — verify TOTP code.
    Requires the pre_token from /login response.
    """
    data      = request.get_json(silent=True) or {}
    pre_token = data.get("pre_token", "")
    code      = data.get("code", "").strip()

    if not pre_token or not code:
        return jsonify({"error": "pre_token and code are required"}), 400

    payload = decode_token(pre_token)
    if not payload or payload.get("role") != "__mfa_pending__":
        return jsonify({"error": "Invalid or expired pre-auth token"}), 401

    user_id = payload["sub"]
    secret  = get_mfa_secret(user_id)
    if not secret:
        return jsonify({"error": "MFA not configured for this account"}), 400

    if not verify_mfa_code(secret, code):
        return jsonify({"error": "Invalid MFA code"}), 401

    user = get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    token = generate_token(user["id"], user["role"])
    audit("login_mfa", target_type="user", target_id=user["id"])

    return jsonify({
        "token":    token,
        "id":       user["id"],
        "username": user["username"],
        "role":     user["role"],
        "personnel_id": user["personnel_id"],
        "mfa_enabled":  user["mfa_enabled"],
    }), 200


@bp.route("/logout", methods=["POST"])
@require_auth
def logout():
    """
    Logout — client should discard the token.
    Server-side we just audit the action (JWTs are stateless).
    """
    audit("logout", target_type="user", target_id=g.user_id)
    return jsonify({"message": "Logged out"}), 200


# ── Current user ──────────────────────────────────────────────────────────────

@bp.route("/me", methods=["GET"])
@require_auth
def me():
    """Return the current authenticated user's profile."""
    user = get_user_by_id(g.user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Fetch linked personnel record if exists
    personnel = None
    if user.get("personnel_id"):
        db        = get_db()
        personnel = row_to_dict(db.execute(
            "SELECT id, first_name, last_name, call_sign FROM personnel WHERE id = ?",
            (user["personnel_id"],)
        ).fetchone())

    return jsonify({
        "id":           user["id"],
        "username":     user["username"],
        "role":         user["role"],
        "mfa_enabled":  user["mfa_enabled"],
        "personnel_id": user["personnel_id"],
        "personnel":    personnel,
        "last_login_at": user["last_login_at"],
    }), 200


@bp.route("/me/password", methods=["POST"])
@require_auth
def change_password():
    """Change the current user's password."""
    data         = request.get_json(silent=True) or {}
    current_pass = data.get("current_password", "")
    new_pass     = data.get("new_password", "")

    if not current_pass or not new_pass:
        return jsonify({"error": "current_password and new_password are required"}), 400

    if len(new_pass) < 8:
        return jsonify({"error": "New password must be at least 8 characters"}), 400

    db   = get_db()
    user = db.execute(
        "SELECT password_hash FROM users WHERE id = ?", (g.user_id,)
    ).fetchone()

    from core.auth import check_password
    if not check_password(current_pass, user["password_hash"]):
        return jsonify({"error": "Current password is incorrect"}), 401

    db.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
        (hash_password(new_pass), g.user_id)
    )
    db.commit()
    audit("change_password", target_type="user", target_id=g.user_id)

    return jsonify({"message": "Password updated"}), 200


# ── MFA setup ─────────────────────────────────────────────────────────────────

@bp.route("/me/mfa/setup", methods=["POST"])
@require_auth
def mfa_setup():
    """
    Begin MFA setup — generates a secret and returns a QR code.
    User scans QR code with their authenticator app, then calls
    /me/mfa/confirm to verify and activate MFA.
    """
    secret  = generate_mfa_secret()
    user    = get_user_by_id(g.user_id)
    qr_b64  = get_mfa_qr_code(user["username"], secret)

    # Store the pending secret (not activated until confirmed)
    db = get_db()
    db.execute(
        "UPDATE users SET mfa_secret = ? WHERE id = ?",
        (secret, g.user_id)
    )
    db.commit()

    return jsonify({
        "secret":   secret,
        "qr_code":  f"data:image/png;base64,{qr_b64}",
        "message":  "Scan the QR code with your authenticator app, then confirm.",
    }), 200


@bp.route("/me/mfa/confirm", methods=["POST"])
@require_auth
def mfa_confirm():
    """Confirm MFA setup by verifying the first TOTP code."""
    data   = request.get_json(silent=True) or {}
    code   = data.get("code", "").strip()
    secret = get_mfa_secret(g.user_id)

    if not secret:
        return jsonify({"error": "MFA setup not initiated"}), 400
    if not code:
        return jsonify({"error": "code is required"}), 400

    if not enable_mfa(g.user_id, secret, code):
        return jsonify({"error": "Invalid code — try again"}), 401

    audit("mfa_enabled", target_type="user", target_id=g.user_id)
    return jsonify({"message": "MFA enabled successfully"}), 200


@bp.route("/me/mfa/disable", methods=["POST"])
@require_auth
def mfa_disable():
    """
    Disable MFA for the current user.
    Requires password confirmation for security.
    """
    data     = request.get_json(silent=True) or {}
    password = data.get("password", "")

    if not password:
        return jsonify({"error": "Password confirmation required"}), 400

    db   = get_db()
    user = db.execute(
        "SELECT password_hash FROM users WHERE id = ?", (g.user_id,)
    ).fetchone()

    if not check_password(password, user["password_hash"]):
        return jsonify({"error": "Incorrect password"}), 401

    disable_mfa(g.user_id)
    audit("mfa_disabled", target_type="user", target_id=g.user_id)
    return jsonify({"message": "MFA disabled"}), 200


# ── Admin — user management ───────────────────────────────────────────────────

@bp.route("/", methods=["GET"])
@require_admin
def list_users():
    """List all users (admin only)."""
    db    = get_db()
    users = db.execute(
        """SELECT u.id, u.username, u.role, u.is_active,
                  u.mfa_enabled, u.created_at, u.last_login_at,
                  p.first_name, p.last_name, p.call_sign
           FROM users u
           LEFT JOIN personnel p ON u.personnel_id = p.id
           ORDER BY u.username"""
    ).fetchall()
    return jsonify(rows_to_list(users)), 200


@bp.route("/", methods=["POST"])
@require_admin
def create_user_route():
    """Create a new user account (admin only)."""
    data     = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role     = data.get("role", "field_op")
    personnel_id = data.get("personnel_id")

    valid_roles = {"super_admin","admin","ic","logistics","field_op","observer"}
    if role not in valid_roles:
        return jsonify({"error": f"Invalid role. Must be one of: {', '.join(valid_roles)}"}), 400

    if not username or not password:
        return jsonify({"error": "username and password are required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    try:
        user = create_user(username, password, role, personnel_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409

    audit("create_user", target_type="user", target_id=user["id"],
          detail=f"role={role}")
    return jsonify(user), 201


@bp.route("/<user_id>", methods=["PATCH"])
@require_admin
def update_user(user_id):
    """Update a user's role or active status (admin only)."""
    data = request.get_json(silent=True) or {}
    db   = get_db()

    user = db.execute(
        "SELECT id FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not user:
        return jsonify({"error": "User not found"}), 404

    updates = []
    params  = []

    if "role" in data:
        valid_roles = {"super_admin","admin","ic","logistics","field_op","observer"}
        if data["role"] not in valid_roles:
            return jsonify({"error": "Invalid role"}), 400
        updates.append("role = ?")
        params.append(data["role"])

    if "is_active" in data:
        updates.append("is_active = ?")
        params.append(1 if data["is_active"] else 0)

    if "personnel_id" in data:
        updates.append("personnel_id = ?")
        params.append(data["personnel_id"])

    if "must_change_password" in data:
        updates.append("must_change_password = ?")
        params.append(1 if data["must_change_password"] else 0)

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    params.append(user_id)
    db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
    db.commit()

    audit("update_user", target_type="user", target_id=user_id)
    return jsonify({"message": "User updated"}), 200


@bp.route("/<user_id>/reset-password", methods=["POST"])
@require_admin
def reset_password(user_id):
    """
    Admin resets a user's password and forces change on next login.
    """
    data         = request.get_json(silent=True) or {}
    new_password = data.get("new_password", "")

    if not new_password or len(new_password) < 8:
        return jsonify({"error": "new_password must be at least 8 characters"}), 400

    db = get_db()
    db.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
        (hash_password(new_password), user_id)
    )
    db.commit()

    audit("reset_password", target_type="user", target_id=user_id)
    return jsonify({"message": "Password reset — user must change on next login"}), 200


@bp.route("/<user_id>/mfa/disable", methods=["POST"])
@require_admin
def admin_disable_mfa(user_id):
    """Admin disables MFA for a user (e.g. lost authenticator)."""
    disable_mfa(user_id)
    audit("admin_mfa_disabled", target_type="user", target_id=user_id)
    return jsonify({"message": "MFA disabled for user"}), 200


# ── Bootstrap — create first admin ────────────────────────────────────────────

@bp.route("/bootstrap", methods=["POST"])
def bootstrap():
    """
    Create the first super_admin account.
    Only works if NO users exist yet — locked out after first use.
    """
    db    = get_db()
    count = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    if count > 0:
        return jsonify({"error": "Bootstrap already completed"}), 403

    data     = request.get_json(silent=True) or {}
    username = data.get("username", "admin").strip()
    password = data.get("password", "")

    if not password or len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    user = create_user(username, password, "super_admin")
    return jsonify({
        "message": f"Super admin '{username}' created. "
                   "This endpoint is now locked.",
        "id": user["id"],
    }), 201