"""
SARPack — manage_users.py
Command-line tool to create, list, update, and deactivate user accounts.

Usage:
    python manage_users.py list
    python manage_users.py add
    python manage_users.py reset-password <username>
    python manage_users.py force-password-change <username>
    python manage_users.py set-role <username> <role>
    python manage_users.py deactivate <username>
    python manage_users.py reactivate <username>

Roles available:
    IC          — Incident Commander  (full control, form signing)
    ops_chief   — Operations Section Chief  (deployments, segments)
    logistics   — Logistics / Admin  (WARDEN access, resources)
    field_op    — Field Operator  (TRAILHEAD only)
    observer    — Read-only  (BASECAMP view only)
"""

import sys
import os
import getpass
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# ── Locate the database ────────────────────────────────────────────────────
_BASE = Path(__file__).parent
_DEFAULT_DB = _BASE / "database" / "sarpack.db"
DB_PATH = os.getenv("SARPACK_SQLITE_PATH", str(_DEFAULT_DB))

ROLES = ("IC", "ops_chief", "logistics", "field_op", "observer")
ROLE_LABELS = {
    "IC":         "Incident Commander",
    "ops_chief":  "Operations Section Chief",
    "logistics":  "Logistics / Admin",
    "field_op":   "Field Operator",
    "observer":   "Observer",
}

# ── ANSI colours ───────────────────────────────────────────────────────────
GREEN  = "\033[92m"
AMBER  = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def g(s): return f"{GREEN}{s}{RESET}"
def a(s): return f"{AMBER}{s}{RESET}"
def r(s): return f"{RED}{s}{RESET}"
def b(s): return f"{BOLD}{s}{RESET}"
def d(s): return f"{DIM}{s}{RESET}"


# ── DB helpers ─────────────────────────────────────────────────────────────
def get_db():
    if not Path(DB_PATH).exists():
        print(r(f"\n  ✖  Database not found: {DB_PATH}"))
        print(d("     Run the SARPack app once to initialise the schema, then retry.\n"))
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def new_id():
    return secrets.token_hex(8)   # 16-char hex, matches core/db.py style


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Crypto (mirrors core/auth.py exactly) ─────────────────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=260_000,
    )
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        _, salt, expected_hex = stored_hash.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=260_000,
    )
    return hmac.compare_digest(dk.hex(), expected_hex)


# ── Password prompt with confirmation ─────────────────────────────────────
def prompt_password(label="Password") -> str:
    while True:
        pw = getpass.getpass(f"  {label} (min 10 chars): ")
        if len(pw) < 10:
            print(a("  ⚠  Password must be at least 10 characters. Try again."))
            continue
        confirm = getpass.getpass(f"  Confirm {label}: ")
        if pw != confirm:
            print(a("  ⚠  Passwords do not match. Try again."))
            continue
        return pw


# ── Commands ───────────────────────────────────────────────────────────────

def cmd_list():
    """List all users."""
    db = get_db()
    rows = db.execute(
        "SELECT username, role, is_active, must_change_password, created_at, last_login_at "
        "FROM users ORDER BY created_at"
    ).fetchall()
    db.close()

    if not rows:
        print(d("\n  No users found.\n"))
        return

    print(f"\n  {b('SARPACK USERS')}\n")
    print(f"  {'USERNAME':<20} {'ROLE':<12} {'LABEL':<28} {'STATUS':<18} {'MUST CHANGE':<13} {'LAST LOGIN'}")
    print(f"  {'-'*20} {'-'*12} {'-'*28} {'-'*18} {'-'*13} {'-'*20}")
    for row in rows:
        status = g("active") if row["is_active"] else r("inactive")
        must   = a("YES") if row["must_change_password"] else d("no")
        role_label = ROLE_LABELS.get(row["role"], row["role"])
        last_login = row["last_login_at"][:10] if row["last_login_at"] else d("never")
        print(f"  {row['username']:<20} {row['role']:<12} {role_label:<28} {status:<26} {must:<21} {last_login}")
    print()


def cmd_add():
    """Interactively create a new user."""
    print(f"\n  {b('ADD USER')}\n")

    # Username
    while True:
        username = input("  Username: ").strip()
        if not username:
            print(a("  ⚠  Username cannot be empty."))
            continue
        if len(username) < 3:
            print(a("  ⚠  Username must be at least 3 characters."))
            continue
        # Check for duplicates
        db = get_db()
        exists = db.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        db.close()
        if exists:
            print(a(f"  ⚠  Username '{username}' already exists."))
            continue
        break

    # Role
    print(f"\n  Roles:")
    for i, role in enumerate(ROLES, 1):
        print(f"    {b(str(i))}. {role:<12}  {d(ROLE_LABELS[role])}")
    while True:
        choice = input("\n  Select role (1–5): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(ROLES):
            role = ROLES[int(choice) - 1]
            break
        print(a("  ⚠  Enter a number between 1 and 5."))

    # Password
    print()
    password = prompt_password()

    # Must change password on first login?
    print()
    force = input("  Require password change on first login? [y/N]: ").strip().lower()
    must_change = force in ("y", "yes")

    # Write to DB
    db = get_db()
    uid  = new_id()
    ts   = now_utc()
    db.execute(
        "INSERT INTO users (id, personnel_id, username, password_hash, role, "
        "is_active, must_change_password, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (uid, None, username, hash_password(password), role, 1 if must_change else 0, ts, ts),
    )
    db.commit()
    db.close()

    note = f"  {a('⚠  Will be prompted to set a new password on first login.')}" if must_change else ""
    print(f"\n  {g('✔')}  User {b(username)} created with role {b(role)} ({ROLE_LABELS[role]}).")
    if note:
        print(note)
    print()


def cmd_reset_password(username: str):
    """Reset a user's password."""
    db = get_db()
    row = db.execute(
        "SELECT id, is_active FROM users WHERE username = ?", (username,)
    ).fetchone()

    if not row:
        print(r(f"\n  ✖  User '{username}' not found.\n"))
        db.close()
        sys.exit(1)

    if not row["is_active"]:
        print(a(f"\n  ⚠  User '{username}' is inactive. Reactivate first if needed.\n"))

    print(f"\n  {b('RESET PASSWORD')} — {username}\n")
    password = prompt_password("New password")

    db.execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE username = ?",
        (hash_password(password), now_utc(), username),
    )
    db.commit()
    db.close()
    print(f"\n  {g('✔')}  Password updated for {b(username)}.\n")


def cmd_set_role(username: str, role: str):
    """Change a user's role."""
    if role not in ROLES:
        print(r(f"\n  ✖  Invalid role '{role}'. Choose from: {', '.join(ROLES)}\n"))
        sys.exit(1)

    db = get_db()
    row = db.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not row:
        print(r(f"\n  ✖  User '{username}' not found.\n"))
        db.close()
        sys.exit(1)

    db.execute(
        "UPDATE users SET role = ?, updated_at = ? WHERE username = ?",
        (role, now_utc(), username),
    )
    db.commit()
    db.close()
    print(f"\n  {g('✔')}  {b(username)} role set to {b(role)} ({ROLE_LABELS[role]}).\n")



def cmd_force_password_change(username: str):
    """Flag a user to change password on next login (does not alter current password)."""
    db = get_db()
    row = db.execute(
        "SELECT id, is_active, must_change_password FROM users WHERE username = ?", (username,)
    ).fetchone()

    if not row:
        print(r(f"\n  ✖  User '{username}' not found.\n"))
        db.close()
        sys.exit(1)

    if not row["is_active"]:
        print(a(f"\n  ⚠  User '{username}' is inactive.\n"))
        db.close()
        return

    if row["must_change_password"]:
        print(a(f"\n  ⚠  User '{username}' is already flagged for password change.\n"))
        db.close()
        return

    db.execute(
        "UPDATE users SET must_change_password = 1, updated_at = ? WHERE username = ?",
        (now_utc(), username),
    )
    db.commit()
    db.close()
    print(f"\n  {g('✔')}  {b(username)} will be required to change password on next login.\n")

def cmd_deactivate(username: str):
    """Deactivate a user account (disables login, preserves data)."""
    _set_active(username, False)


def cmd_reactivate(username: str):
    """Reactivate a previously deactivated account."""
    _set_active(username, True)


def _set_active(username: str, active: bool):
    db = get_db()
    row = db.execute(
        "SELECT id, is_active FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not row:
        print(r(f"\n  ✖  User '{username}' not found.\n"))
        db.close()
        sys.exit(1)

    if bool(row["is_active"]) == active:
        state = "already active" if active else "already inactive"
        print(a(f"\n  ⚠  User '{username}' is {state}.\n"))
        db.close()
        return

    db.execute(
        "UPDATE users SET is_active = ?, updated_at = ? WHERE username = ?",
        (1 if active else 0, now_utc(), username),
    )
    db.commit()
    db.close()
    verb = g("reactivated") if active else r("deactivated")
    print(f"\n  {g('✔') if active else r('✖')}  User {b(username)} {verb}.\n")


# ── Entry point ────────────────────────────────────────────────────────────

USAGE = f"""
{b('SARPack User Manager')}

  {b('python manage_users.py list')}
      Show all users, roles, and status.

  {b('python manage_users.py add')}
      Interactively create a new user.

  {b('python manage_users.py reset-password')} {d('<username>')}
      Set a new password and force a change on next login.

  {b('python manage_users.py force-password-change')} {d('<username>')}
      Flag an existing user to change their password on next login.
      Does not alter their current password.

  {b('python manage_users.py set-role')} {d('<username> <role>')}
      Change a user's role.
      Roles: IC | ops_chief | logistics | field_op | observer

  {b('python manage_users.py deactivate')} {d('<username>')}
      Disable login. Preserves all data.

  {b('python manage_users.py reactivate')} {d('<username>')}
      Re-enable a deactivated account.
"""

def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return

    cmd = args[0]

    if cmd == "list":
        cmd_list()

    elif cmd == "add":
        cmd_add()

    elif cmd == "reset-password":
        if len(args) < 2:
            print(a("\n  Usage: python manage_users.py reset-password <username>\n"))
            sys.exit(1)
        cmd_reset_password(args[1])

    elif cmd == "force-password-change":
        if len(args) < 2:
            print(a("\n  Usage: python manage_users.py force-password-change <username>\n"))
            sys.exit(1)
        cmd_force_password_change(args[1])

    elif cmd == "set-role":
        if len(args) < 3:
            print(a("\n  Usage: python manage_users.py set-role <username> <role>\n"))
            sys.exit(1)
        cmd_set_role(args[1], args[2])

    elif cmd == "deactivate":
        if len(args) < 2:
            print(a("\n  Usage: python manage_users.py deactivate <username>\n"))
            sys.exit(1)
        cmd_deactivate(args[1])

    elif cmd == "reactivate":
        if len(args) < 2:
            print(a("\n  Usage: python manage_users.py reactivate <username>\n"))
            sys.exit(1)
        cmd_reactivate(args[1])

    else:
        print(r(f"\n  ✖  Unknown command '{cmd}'."))
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()