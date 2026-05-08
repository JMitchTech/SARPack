"""
SARPack 2.0 — core/auth.py
Authentication, authorization, JWT, password hashing, and MFA.
"""

import uuid
import bcrypt
import jwt
import pyotp
import qrcode
import io
import base64
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import request, jsonify, g

from core.config import Config
from core.database import get_db, row_to_dict


# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# ── JWT ───────────────────────────────────────────────────────────────────────

def generate_token(user_id: str, role: str) -> str:
    """Generate a signed JWT for the given user."""
    payload = {
        "sub":  user_id,
        "role": role,
        "iat":  datetime.now(timezone.utc),
        "exp":  datetime.now(timezone.utc) + timedelta(hours=Config.JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, Config.SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    """
    Decode and validate a JWT.
    Returns the payload dict or None if invalid/expired.
    """
    try:
        return jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def get_token_from_request() -> str | None:
    """
    Extract JWT from the request.
    Checks Authorization header (Bearer token) first, then cookie.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return request.cookies.get("sarpack_token")


# ── Auth decorators ───────────────────────────────────────────────────────────

def require_auth(f):
    """
    Decorator — requires a valid JWT.
    Sets g.user_id and g.role for the route handler.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token_from_request()
        if not token:
            return jsonify({"error": "Authentication required"}), 401

        payload = decode_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401

        g.user_id = payload["sub"]
        g.role    = payload["role"]

        # Verify user still exists and is active
        db   = get_db()
        user = db.execute(
            "SELECT id, is_active FROM users WHERE id = ?",
            (g.user_id,)
        ).fetchone()

        if not user or not user["is_active"]:
            return jsonify({"error": "Account inactive"}), 401

        return f(*args, **kwargs)
    return decorated


def require_role(*roles):
    """
    Decorator — requires auth AND one of the specified roles.
    Usage: @require_role('admin', 'ic')
    """
    def decorator(f):
        @wraps(f)
        @require_auth
        def decorated(*args, **kwargs):
            if g.role not in roles:
                return jsonify({"error": "Insufficient permissions"}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


# Role shorthand decorators
def require_admin(f):
    return require_role("super_admin", "admin")(f)


def require_ic(f):
    return require_role("super_admin", "admin", "ic")(f)


def require_logistics(f):
    return require_role("super_admin", "admin", "ic", "logistics")(f)


# ── User management ───────────────────────────────────────────────────────────

def create_user(username: str, password: str, role: str = "field_op",
                personnel_id: str = None) -> dict:
    """
    Create a new user account.
    Returns the created user dict or raises ValueError on conflict.
    """
    db = get_db()

    existing = db.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()
    if existing:
        raise ValueError(f"Username '{username}' already exists")

    user_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO users
           (id, username, password_hash, role, personnel_id)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, username.lower().strip(), hash_password(password),
         role, personnel_id)
    )
    db.commit()

    return {"id": user_id, "username": username, "role": role}


def authenticate_user(username: str, password: str) -> dict | None:
    """
    Verify credentials. Returns user dict if valid, None if not.
    Does NOT check MFA here — that's a separate step for WARDEN.
    """
    db   = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE username = ? AND is_active = 1",
        (username.lower().strip(),)
    ).fetchone()

    if not user:
        return None
    if not check_password(password, user["password_hash"]):
        return None

    # Update last login
    db.execute(
        "UPDATE users SET last_login_at = datetime('now') WHERE id = ?",
        (user["id"],)
    )
    db.commit()

    return row_to_dict(user)


def get_user_by_id(user_id: str) -> dict | None:
    db   = get_db()
    user = db.execute(
        "SELECT id, username, role, is_active, mfa_enabled, personnel_id, "
        "must_change_password, last_login_at FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()
    return row_to_dict(user)


# ── MFA (WARDEN) ──────────────────────────────────────────────────────────────

def generate_mfa_secret() -> str:
    """Generate a new TOTP secret for a user."""
    return pyotp.random_base32()


def get_mfa_qr_code(username: str, secret: str) -> str:
    """
    Generate a QR code for the user to scan with their authenticator app.
    Returns a base64-encoded PNG image string.
    """
    totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=username,
        issuer_name=Config.MFA_ISSUER
    )

    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(totp_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def verify_mfa_code(secret: str, code: str) -> bool:
    """
    Verify a 6-digit TOTP code.
    Allows 1 window (30 seconds) of drift in either direction.
    """
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def enable_mfa(user_id: str, secret: str, code: str) -> bool:
    """
    Enable MFA for a user after verifying their first code.
    Returns True if enabled successfully.
    """
    if not verify_mfa_code(secret, code):
        return False

    db = get_db()
    db.execute(
        "UPDATE users SET mfa_secret = ?, mfa_enabled = 1 WHERE id = ?",
        (secret, user_id)
    )
    db.commit()
    return True


def disable_mfa(user_id: str) -> None:
    """Disable MFA for a user (admin action)."""
    db = get_db()
    db.execute(
        "UPDATE users SET mfa_secret = NULL, mfa_enabled = 0 WHERE id = ?",
        (user_id,)
    )
    db.commit()


def user_requires_mfa(user_id: str) -> bool:
    """Check if a user has MFA enabled."""
    db   = get_db()
    user = db.execute(
        "SELECT mfa_enabled FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return bool(user and user["mfa_enabled"])


def get_mfa_secret(user_id: str) -> str | None:
    """Retrieve a user's MFA secret (for verification)."""
    db   = get_db()
    user = db.execute(
        "SELECT mfa_secret FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return user["mfa_secret"] if user else None


# ── Audit logging ─────────────────────────────────────────────────────────────

def audit(action: str, target_type: str = None, target_id: str = None,
          detail: str = None) -> None:
    """
    Log an auditable action. Called from route handlers.
    Silently fails if g.user_id is not set (unauthenticated actions).
    """
    try:
        user_id = getattr(g, "user_id", None)
        db = get_db()
        db.execute(
            """INSERT INTO audit_log
               (id, user_id, action, target_type, target_id, detail, ip_address)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), user_id, action, target_type, target_id,
             detail, request.remote_addr)
        )
        db.commit()
    except Exception:
        pass  # Audit failures should never break the main flow