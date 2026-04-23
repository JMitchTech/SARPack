"""
SARPack — warden/routes/users.py
User account management. Links personnel records to SARPack logins.
Handles account creation, role assignment, password resets, and login.

Auth endpoints used by all apps — login/logout live here because
WARDEN is the identity authority for the entire SARPack system.
"""

import logging
from flask import Blueprint, jsonify, request, make_response
from core.auth import (
    require_ic,
    require_role,
    get_current_user,
    create_user,
    authenticate,
    revoke_session,
    revoke_all_sessions,
    hash_password,
    change_own_password,
    ROLES,
    ROLE_LABELS,
    ROLE_PERMISSIONS,
)
from core.db import (
    versioned_update,
    get_record,
    local_db,
    now_utc,
    VersionConflictError,
)

log = logging.getLogger("warden.users")
users_bp = Blueprint("users", __name__)


# ---------------------------------------------------------------------------
# POST /api/users/login
# Authenticate and receive a session token.
# If must_change_password is set, the token is returned but the frontend
# must show the change-password screen before allowing normal access.
# ---------------------------------------------------------------------------

@users_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    result = authenticate(username, password)
    if not result:
        # Deliberately vague — don't reveal whether username exists
        return jsonify({"error": "Invalid username or password"}), 401

    # Set token as HTTP-only cookie AND return it in the body
    # Apps can use either depending on their client type
    response = make_response(jsonify({
        "message": "Login successful",
        "user_id": result["user_id"],
        "username": result["username"],
        "role": result["role"],
        "role_label": ROLE_LABELS.get(result["role"], result["role"]),
        "permissions": result["permissions"],
        "token": result["token"],
        "must_change_password": result["must_change_password"],
    }))
    response.set_cookie(
        "sarpack_token",
        result["token"],
        httponly=True,
        samesite="Strict",
        max_age=60 * 60 * 12,  # 12 hours — matches SESSION_EXPIRY_HOURS
    )
    log.info("Login: %s (role=%s, must_change=%s)",
             username, result["role"], result["must_change_password"])
    return response


# ---------------------------------------------------------------------------
# POST /api/users/logout
# Revoke the current session token
# ---------------------------------------------------------------------------

@users_bp.route("/logout", methods=["POST"])
def logout():
    token = request.cookies.get("sarpack_token") or \
            request.headers.get("Authorization", "").replace("Bearer ", "")

    if token:
        revoke_session(token)

    response = make_response(jsonify({"message": "Logged out"}))
    response.delete_cookie("sarpack_token")
    return response


# ---------------------------------------------------------------------------
# GET /api/users/me
# Return the current authenticated user's profile
# ---------------------------------------------------------------------------

@users_bp.route("/me", methods=["GET"])
def me():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Not authenticated"}), 401

    return jsonify({
        "user_id":              user["id"],
        "username":             user["username"],
        "role":                 user["role"],
        "role_label":           ROLE_LABELS.get(user["role"], user["role"]),
        "permissions":          list(ROLE_PERMISSIONS.get(user["role"], set())),
        "personnel_id":         user.get("personnel_id"),
        "must_change_password": bool(user.get("must_change_password", 0)),
    })


# ---------------------------------------------------------------------------
# POST /api/users/me/change-password
# Authenticated user changes their own password.
# Requires their current password — clears must_change_password on success.
# Does NOT require IC role — any logged-in user can change their own password.
# ---------------------------------------------------------------------------

@users_bp.route("/me/change-password", methods=["POST"])
def change_password():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    current_password = data.get("current_password", "")
    new_password     = data.get("new_password", "")

    if not current_password or not new_password:
        return jsonify({"error": "current_password and new_password are required"}), 400

    if len(new_password) < 10:
        return jsonify({"error": "New password must be at least 10 characters"}), 400

    if current_password == new_password:
        return jsonify({"error": "New password must be different from current password"}), 400

    try:
        success = change_own_password(user["id"], current_password, new_password)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if not success:
        return jsonify({"error": "Current password is incorrect"}), 401

    log.info("User '%s' changed their own password", user["username"])
    return jsonify({"message": "Password updated successfully"})


# ---------------------------------------------------------------------------
# GET /api/users/
# List all user accounts — IC only
# ---------------------------------------------------------------------------

@users_bp.route("/", methods=["GET"])
@require_ic
def list_users():
    with local_db() as db:
        rows = db.execute(
            """
            SELECT u.id, u.username, u.role, u.is_active,
                   u.must_change_password,
                   u.last_login_at, u.created_at,
                   p.first_name, p.last_name, p.call_sign
            FROM users u
            LEFT JOIN personnel p ON p.id = u.personnel_id
            ORDER BY u.username
            """,
        ).fetchall()

    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# POST /api/users/
# Create a new user account — IC only
# Links to a personnel record if personnel_id provided
# ---------------------------------------------------------------------------

@users_bp.route("/", methods=["POST"])
@require_ic
def create_user_account():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    required = ("username", "password", "role")
    missing = [f for f in required if not data.get(f, "").strip()]
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    if data["role"] not in ROLES:
        return jsonify({
            "error": f"Invalid role '{data['role']}'",
            "valid_roles": list(ROLES),
        }), 400

    if len(data["password"]) < 10:
        return jsonify({
            "error": "Password must be at least 10 characters"
        }), 400

    # Validate personnel_id if provided
    personnel_id = data.get("personnel_id")
    if personnel_id:
        person = get_record("personnel", personnel_id)
        if not person:
            return jsonify({"error": "Personnel record not found"}), 404

        # Check if personnel already has an account
        with local_db() as db:
            existing = db.execute(
                "SELECT id FROM users WHERE personnel_id = ?",
                (personnel_id,),
            ).fetchone()
        if existing:
            return jsonify({
                "error": "This personnel record already has a user account",
                "existing_user_id": existing["id"],
            }), 409

    must_change = bool(data.get("must_change_password", False))

    try:
        user_id = create_user(
            username=data["username"].strip(),
            password=data["password"],
            role=data["role"],
            personnel_id=personnel_id,
            must_change_password=must_change,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    log.info("Created user account: %s (role=%s, must_change=%s)",
             data["username"], data["role"], must_change)
    return jsonify({
        "message": "User account created",
        "id": user_id,
        "username": data["username"],
        "role": data["role"],
        "must_change_password": must_change,
    }), 201


# ---------------------------------------------------------------------------
# PATCH /api/users/<id>/role
# Change a user's role — IC only
# Revokes all existing sessions so the new role takes effect immediately
# ---------------------------------------------------------------------------

@users_bp.route("/<user_id>/role", methods=["PATCH"])
@require_ic
def change_role(user_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    new_role = data.get("role", "").strip()
    if new_role not in ROLES:
        return jsonify({
            "error": f"Invalid role '{new_role}'",
            "valid_roles": list(ROLES),
        }), 400

    user = get_record("users", user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Prevent IC from accidentally removing their own IC role
    current_user = get_current_user()
    if current_user["id"] == user_id and new_role != "IC":
        return jsonify({
            "error": "You cannot remove your own IC role. "
                     "Assign IC to another user first."
        }), 400

    try:
        versioned_update(
            "users", user_id,
            {"role": new_role},
            expected_version=user["version"],
        )
    except VersionConflictError:
        return jsonify({"error": "Version conflict — re-fetch and try again"}), 409

    # Revoke all sessions — forces re-login with new role
    revoke_all_sessions(user_id)

    log.info("Changed role for user %s: %s → %s",
             user["username"], user["role"], new_role)

    return jsonify({
        "message": f"Role updated to {new_role}. User must log in again.",
        "id": user_id,
        "new_role": new_role,
    })


# ---------------------------------------------------------------------------
# POST /api/users/<id>/reset-password
# Admin password reset — IC only.
# Sets a temporary password AND flags must_change_password = 1
# so the user is forced to set their own password on next login.
# ---------------------------------------------------------------------------

@users_bp.route("/<user_id>/reset-password", methods=["POST"])
@require_ic
def reset_password(user_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    new_password = data.get("new_password", "")
    if len(new_password) < 10:
        return jsonify({
            "error": "New password must be at least 10 characters"
        }), 400

    user = get_record("users", user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    try:
        versioned_update(
            "users", user_id,
            {
                "password_hash": hash_password(new_password),
                "must_change_password": 1,   # force change on next login
            },
            expected_version=user["version"],
        )
    except VersionConflictError:
        return jsonify({"error": "Version conflict — re-fetch and try again"}), 409

    # Revoke all sessions — forces re-login with new password
    revoke_all_sessions(user_id)

    log.info("Password reset for user: %s (will be forced to change)", user["username"])
    return jsonify({
        "message": "Password reset. User must log in and set a new password.",
        "id": user_id,
        "must_change_password": True,
    })


# ---------------------------------------------------------------------------
# POST /api/users/<id>/force-password-change
# Flag a user to change password on next login — IC only.
# Does not alter the current password.
# ---------------------------------------------------------------------------

@users_bp.route("/<user_id>/force-password-change", methods=["POST"])
@require_ic
def force_password_change(user_id):
    user = get_record("users", user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    try:
        versioned_update(
            "users", user_id,
            {"must_change_password": 1},
            expected_version=user["version"],
        )
    except VersionConflictError:
        return jsonify({"error": "Version conflict — re-fetch and try again"}), 409

    # Revoke sessions so flag takes effect immediately
    revoke_all_sessions(user_id)

    log.info("Flagged must_change_password for user: %s", user["username"])
    return jsonify({
        "message": f"'{user['username']}' will be required to change password on next login.",
        "id": user_id,
    })


# ---------------------------------------------------------------------------
# POST /api/users/<id>/deactivate
# Deactivate a user account — IC only
# Revokes all sessions immediately
# ---------------------------------------------------------------------------

@users_bp.route("/<user_id>/deactivate", methods=["POST"])
@require_ic
def deactivate_user(user_id):
    user = get_record("users", user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    current_user = get_current_user()
    if current_user["id"] == user_id:
        return jsonify({"error": "You cannot deactivate your own account"}), 400

    if not user["is_active"]:
        return jsonify({"error": "User account is already inactive"}), 400

    try:
        versioned_update(
            "users", user_id,
            {"is_active": 0},
            expected_version=user["version"],
        )
    except VersionConflictError:
        return jsonify({"error": "Version conflict — re-fetch and try again"}), 409

    revoke_all_sessions(user_id)

    log.info("Deactivated user account: %s", user["username"])
    return jsonify({
        "message": f"User account '{user['username']}' deactivated.",
        "id": user_id,
    })


# ---------------------------------------------------------------------------
# GET /api/users/roles
# Return all valid roles with labels and permissions
# Used by frontend role selector dropdowns
# ---------------------------------------------------------------------------

@users_bp.route("/roles", methods=["GET"])
@require_role("IC", "ops_chief", "logistics", "observer")
def list_roles():
    return jsonify([
        {
            "role": role,
            "label": ROLE_LABELS[role],
            "permissions": list(ROLE_PERMISSIONS.get(role, set())),
        }
        for role in ROLES
    ])