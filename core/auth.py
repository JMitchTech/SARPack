"""
SARPack — core/auth.py
Authentication and role-based access control.

Five roles enforced across all apps:
  IC          — Incident Commander. Full control. Only role that can sign ICS forms.
  ops_chief   — Operations Section Chief. Manages divisions, teams, segments.
  logistics   — Logistics / Admin. WARDEN access, resource management.
  field_op    — Field Operator. TRAILHEAD only. No BASECAMP access.
  observer    — Read-only. BASECAMP view only. No writes.

Usage in Flask routes:
    from core.auth import require_role, get_current_user

    @app.route("/incident/<id>/close", methods=["POST"])
    @require_role("IC")
    def close_incident(id):
        ...

    @app.route("/incident/<id>/sign", methods=["POST"])
    @require_role("IC")          # hard gate — only IC can sign
    def sign_form(id):
        ...
"""

import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import request, jsonify, g

from core.config import config
from core.db import local_db, versioned_insert, new_id, now_utc

log = logging.getLogger("sarpack.auth")


# ---------------------------------------------------------------------------
# Role definitions + hierarchy
# ---------------------------------------------------------------------------

ROLES = ("IC", "ops_chief", "logistics", "field_op", "observer")

# Permission hierarchy: each role inherits everything below it
# IC > ops_chief > logistics > observer
# field_op is a parallel track — access to TRAILHEAD only
_ROLE_RANK = {
    "IC": 50,
    "ops_chief": 40,
    "logistics": 30,
    "observer": 10,
    "field_op": 5,   # separate track, not in the main hierarchy
}

ROLE_LABELS = {
    "IC": "Incident Commander",
    "ops_chief": "Operations Section Chief",
    "logistics": "Logistics / Admin",
    "field_op": "Field Operator",
    "observer": "Observer",
}

# What each role can do — used for UI permission checks
ROLE_PERMISSIONS = {
    "IC": {
        "sign_forms", "export_forms", "close_incident", "manage_deployments",
        "manage_personnel", "manage_segments", "view_all", "edit_all",
        "manage_users", "manage_resources",
    },
    "ops_chief": {
        "manage_deployments", "manage_segments", "view_all", "edit_assignments",
        "manage_resources",
    },
    "logistics": {
        "manage_personnel", "manage_resources", "view_all", "edit_warden",
    },
    "field_op": {
        "trailhead_access", "submit_gps", "submit_patient_form",
    },
    "observer": {
        "view_all",
    },
}


def role_can(role: str, permission: str) -> bool:
    """Check if a role has a specific permission."""
    return permission in ROLE_PERMISSIONS.get(role, set())


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """
    Hash a password using PBKDF2-HMAC-SHA256 with a random salt.
    Returns a string in the format: algorithm$salt$hash
    """
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=260_000,   # OWASP 2024 recommendation
    )
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored hash. Timing-safe."""
    try:
        algorithm, salt, expected_hex = stored_hash.split("$")
    except ValueError:
        return False

    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=260_000,
    )
    return hmac.compare_digest(dk.hex(), expected_hex)


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------

def create_session(user_id: str) -> str:
    """
    Create a new session token for a user. Returns the token string.
    Token is stored in the sessions table and returned to the client
    as a Bearer token or cookie.
    """
    token = secrets.token_urlsafe(48)
    expires_at = (
        datetime.now(timezone.utc) +
        timedelta(hours=config.SESSION_EXPIRY_HOURS)
    ).isoformat()

    versioned_insert.__wrapped__ = getattr(versioned_insert, "__wrapped__", versioned_insert)

    with local_db() as db:
        db.execute(
            "INSERT INTO sessions (id, user_id, token, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (new_id(), user_id, token, expires_at, now_utc()),
        )

    return token


def validate_token(token: str) -> dict | None:
    """
    Validate a session token. Returns the user record if valid, None otherwise.
    Automatically invalidates expired sessions.
    """
    with local_db() as db:
        row = db.execute(
            """
            SELECT u.id, u.username, u.role, u.personnel_id, u.is_active,
                   s.expires_at, s.id as session_id
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()

    if not row:
        return None

    row = dict(row)

    # Check expiry
    expires_at = datetime.fromisoformat(row["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        revoke_session(token)
        return None

    # Check user still active
    if not row["is_active"]:
        return None

    return row


def revoke_session(token: str):
    """Delete a session token (logout)."""
    with local_db() as db:
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))


def revoke_all_sessions(user_id: str):
    """Revoke all sessions for a user (force logout everywhere)."""
    with local_db() as db:
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


# ---------------------------------------------------------------------------
# Token extraction from request
# ---------------------------------------------------------------------------

def _extract_token() -> str | None:
    """
    Extract the session token from the current Flask request.
    Checks Authorization header (Bearer) first, then cookie.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    return request.cookies.get("sarpack_token")


def get_current_user() -> dict | None:
    """
    Return the authenticated user for the current request.
    Caches result in Flask's g object for the request lifetime.
    Returns None if not authenticated.
    """
    if hasattr(g, "_sarpack_user"):
        return g._sarpack_user

    token = _extract_token()
    if not token:
        g._sarpack_user = None
        return None

    user = validate_token(token)
    g._sarpack_user = user
    return user


# ---------------------------------------------------------------------------
# Route decorators
# ---------------------------------------------------------------------------

def require_auth(f):
    """
    Decorator: requires any authenticated user.
    Returns 401 if no valid session token.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


def require_role(*allowed_roles: str):
    """
    Decorator factory: requires the user to have one of the specified roles.
    Returns 401 if not authenticated, 403 if authenticated but wrong role.

    Usage:
        @require_role("IC")                        # IC only
        @require_role("IC", "ops_chief")           # IC or ops chief
        @require_role("IC", "ops_chief", "logistics")  # any of these
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({"error": "Authentication required"}), 401
            if user["role"] not in allowed_roles:
                log.warning(
                    "Access denied: user %s (role=%s) tried to access %s "
                    "(requires %s)",
                    user["username"], user["role"],
                    request.endpoint, allowed_roles,
                )
                return jsonify({
                    "error": "Insufficient permissions",
                    "required": list(allowed_roles),
                    "your_role": user["role"],
                }), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def require_ic(f):
    """
    Shorthand for @require_role("IC").
    Use on any route that requires IC sign-off authority.
    The hard gate for ICS form signing and incident closure.
    """
    return require_role("IC")(f)


def require_permission(permission: str):
    """
    Decorator factory: requires a specific named permission.
    More granular than require_role when a permission spans multiple roles.

    Usage:
        @require_permission("manage_deployments")
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({"error": "Authentication required"}), 401
            if not role_can(user["role"], permission):
                return jsonify({
                    "error": "Insufficient permissions",
                    "required_permission": permission,
                    "your_role": user["role"],
                }), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

def create_user(username: str, password: str, role: str,
                personnel_id: str | None = None,
                must_change_password: bool = False) -> str:
    """
    Create a new SARPack user account.
    Returns the new user's id.

    Args:
        username:             Unique login name
        password:             Plain text password (hashed before storage)
        role:                 One of ROLES
        personnel_id:         Optional link to a personnel record in WARDEN
        must_change_password: If True, user is forced to set a new password
                              on their first login.
    """
    if role not in ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of: {ROLES}")

    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters.")

    user_id = new_id()
    ts = now_utc()

    with local_db() as db:
        db.execute(
            "INSERT INTO users (id, personnel_id, username, password_hash, role, "
            "is_active, must_change_password, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (user_id, personnel_id, username, hash_password(password), role,
             1 if must_change_password else 0, ts, ts),
        )

    log.info("Created user '%s' with role '%s' (must_change=%s)",
             username, role, must_change_password)
    return user_id


def authenticate(username: str, password: str) -> dict | None:
    """
    Attempt login. Returns the user record and a new session token on success.
    Returns None on failure. Logs all attempts.

    Returns dict with keys: user_id, username, role, token, must_change_password
    """
    with local_db() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1",
            (username,),
        ).fetchone()

    if not row:
        log.warning("Login failed: unknown user '%s'", username)
        return None

    row = dict(row)

    if not verify_password(password, row["password_hash"]):
        log.warning("Login failed: wrong password for '%s'", username)
        return None

    # Update last login timestamp
    with local_db() as db:
        db.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (now_utc(), now_utc(), row["id"]),
        )

    token = create_session(row["id"])
    log.info("User '%s' logged in (role=%s)", username, row["role"])

    return {
        "user_id":              row["id"],
        "username":             row["username"],
        "role":                 row["role"],
        "personnel_id":         row["personnel_id"],
        "token":                token,
        "permissions":          list(ROLE_PERMISSIONS.get(row["role"], set())),
        "must_change_password": bool(row.get("must_change_password", 0)),
    }


def change_own_password(user_id: str, current_password: str, new_password: str) -> bool:
    """
    Allow a user to change their own password.
    Verifies the current password first (prevents token-hijack escalation).
    Clears must_change_password on success.
    Returns True on success, False if current password is wrong.
    """
    if len(new_password) < 10:
        raise ValueError("New password must be at least 10 characters.")

    with local_db() as db:
        row = db.execute(
            "SELECT password_hash FROM users WHERE id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()

    if not row:
        return False

    if not verify_password(current_password, row["password_hash"]):
        log.warning("change_own_password: wrong current password for user_id=%s", user_id)
        return False

    with local_db() as db:
        db.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0, "
            "updated_at = ? WHERE id = ?",
            (hash_password(new_password), now_utc(), user_id),
        )

    log.info("User id=%s changed their own password", user_id)
    return True
    """
    Attempt login. Returns the user record and a new session token on success.
    Returns None on failure. Logs all attempts.

    Returns dict with keys: user_id, username, role, token
    """
    with local_db() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1",
            (username,),
        ).fetchone()

    if not row:
        log.warning("Login failed: unknown user '%s'", username)
        return None

    row = dict(row)

    if not verify_password(password, row["password_hash"]):
        log.warning("Login failed: wrong password for '%s'", username)
        return None

    # Update last login timestamp
    with local_db() as db:
        db.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (now_utc(), now_utc(), row["id"]),
        )

    token = create_session(row["id"])
    log.info("User '%s' logged in (role=%s)", username, row["role"])

    return {
        "user_id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "personnel_id": row["personnel_id"],
        "token": token,
        "permissions": list(ROLE_PERMISSIONS.get(row["role"], set())),
        "must_change_password": bool(row.get("must_change_password", 0)),
    }