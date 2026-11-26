"""Account system phase 1: password login, sessions, and admin user management.

All password hashing and account writes happen here, in the main backend, once.
The admin console (separate repo) calls the narrowed admin.* RPCs; it never
writes these tables directly. Requires PostgreSQL (end/pg_store).

Security baseline (design §8):
- Argon2id password hashes; >=10 char passwords; first login forces a change.
- Session tokens are high-entropy random; only their SHA-256 hash is stored.
- user and admin audiences are separate sessions and never interchangeable.
- Login events record success/failure with IP/UA; users carry last/prev login.
"""

import hashlib
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from end import pg_store

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,32}$")
MIN_PASSWORD = 10
USER_SESSION_TTL = timedelta(days=7)
ADMIN_SESSION_TTL = timedelta(hours=12)
AUDIENCES = ("user", "admin")

_HASHER = None
_HASHER_LOCK = threading.Lock()
# Simple in-process login rate limit: (username, ip) -> [failure timestamps].
_FAILURES = {}
_FAILURES_LOCK = threading.Lock()
RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW = 60.0


class AuthError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _hasher():
    global _HASHER
    with _HASHER_LOCK:
        if _HASHER is None:
            from argon2 import PasswordHasher

            # argon2-cffi defaults are Argon2id, time_cost=3, memory_cost=64MiB,
            # parallelism=4 (OWASP baseline).
            _HASHER = PasswordHasher()
        return _HASHER


def hash_password(password):
    return _hasher().hash(password)


def verify_password(password_hash, password):
    from argon2.exceptions import VerifyMismatchError, VerificationError

    if not password_hash:
        return False
    try:
        return _hasher().verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def _token_hash(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def _user_out(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "status": row["status"],
        "must_change_password": bool(row["must_change_password"]),
        "last_login_at": row["last_login_at"].isoformat() if row.get("last_login_at") else None,
        "prev_login_at": row["prev_login_at"].isoformat() if row.get("prev_login_at") else None,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def validate_username(username):
    name = str(username or "").strip().lower()
    if not USERNAME_RE.match(name):
        raise AuthError(400, "登录名需为 3~32 位小写字母、数字或下划线")
    return name


def validate_password(password):
    text = str(password or "")
    if len(text) < MIN_PASSWORD:
        raise AuthError(400, "密码至少 %d 位" % MIN_PASSWORD)
    return text


def create_user(username, password, role="user", must_change_password=True):
    """Admin-side account creation: users + token_accounts in one transaction."""
    name = validate_username(username)
    secret = validate_password(password)
    if role not in AUDIENCES:
        raise AuthError(400, "role 只能是 user 或 admin")
    digest = hash_password(secret)
    with pg_store.pool().connection() as conn:
        with conn.transaction():
            exists = conn.execute("SELECT 1 FROM users WHERE username = %s", (name,)).fetchone()
            if exists:
                raise AuthError(409, "登录名已存在")
            row = conn.execute(
                "INSERT INTO users(username, password_hash, role, must_change_password) "
                "VALUES (%s, %s, %s, %s) RETURNING *",
                (name, digest, role, bool(must_change_password)),
            ).fetchone()
            conn.execute("INSERT INTO token_accounts(user_id) VALUES (%s)", (row["id"],))
    return _user_out(row)


def _rate_limited(name, ip):
    key = (name, str(ip or ""))
    cutoff = time.monotonic() - RATE_LIMIT_WINDOW
    with _FAILURES_LOCK:
        hits = [t for t in _FAILURES.get(key, []) if t > cutoff]
        _FAILURES[key] = hits
        return len(hits) >= RATE_LIMIT_MAX


def _record_failure(name, ip):
    key = (name, str(ip or ""))
    with _FAILURES_LOCK:
        _FAILURES.setdefault(key, []).append(time.monotonic())


def _record_event(conn, user_id, username, ok, audience, ip, user_agent):
    conn.execute(
        "INSERT INTO login_events(user_id, username, ok, audience, ip, user_agent) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, username, bool(ok), audience, str(ip or "")[:64], str(user_agent or "")[:256]),
    )


def login(username, password, audience="user", ip="", user_agent=""):
    """Verify password and mint a first-party session. Returns (token, user)."""
    if audience not in AUDIENCES:
        raise AuthError(400, "会话类型只能是 user 或 admin")
    name = str(username or "").strip().lower()
    if _rate_limited(name, ip):
        raise AuthError(429, "尝试次数过多，请稍后再试")
    with pg_store.pool().connection() as conn:
        with conn.transaction():
            row = conn.execute("SELECT * FROM users WHERE username = %s", (name,)).fetchone()
            ok = bool(row) and row["status"] == "active" and verify_password(row["password_hash"], str(password or ""))
            if audience == "admin" and ok and row["role"] != "admin":
                ok = False
            _record_event(conn, row["id"] if row else None, name, ok, audience, ip, user_agent)
            if not ok:
                _record_failure(name, ip)
                raise AuthError(401, "登录名或密码不对，或账号已停用")
            token = secrets.token_urlsafe(32)
            ttl = ADMIN_SESSION_TTL if audience == "admin" else USER_SESSION_TTL
            conn.execute(
                "INSERT INTO sessions(token_hash, user_id, audience, expires_at) VALUES (%s, %s, %s, %s)",
                (_token_hash(token), row["id"], audience, _now() + ttl),
            )
            conn.execute(
                "UPDATE users SET prev_login_at = last_login_at, last_login_at = now() WHERE id = %s",
                (row["id"],),
            )
            fresh = conn.execute("SELECT * FROM users WHERE id = %s", (row["id"],)).fetchone()
    return token, _user_out(fresh)


def session_user(token, audience="user"):
    """Resolve a bearer token to its user, enforcing audience and expiry."""
    if not token or audience not in AUDIENCES:
        return None
    with pg_store.pool().connection() as conn:
        row = conn.execute(
            "SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = %s AND s.audience = %s",
            (_token_hash(token), audience),
        ).fetchone()
        if row is None or row["status"] != "active":
            return None
        if row["expires_at"] <= _now():
            conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_token_hash(token),))
            return None
        conn.execute(
            "UPDATE sessions SET last_seen_at = now() WHERE token_hash = %s", (_token_hash(token),)
        )
    return _user_out(row)


def logout(token, audience="user"):
    if not token:
        return
    with pg_store.pool().connection() as conn:
        conn.execute(
            "DELETE FROM sessions WHERE token_hash = %s AND audience = %s",
            (_token_hash(token), audience),
        )


def change_password(user_id, old_password, new_password):
    secret = validate_password(new_password)
    with pg_store.pool().connection() as conn:
        with conn.transaction():
            row = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
            if row is None or not verify_password(row["password_hash"], str(old_password or "")):
                raise AuthError(401, "原密码不对")
            conn.execute(
                "UPDATE users SET password_hash = %s, must_change_password = FALSE WHERE id = %s",
                (hash_password(secret), user_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))


def reset_password(user_id, new_password):
    """Admin reset: force a change at next login and revoke existing sessions."""
    secret = validate_password(new_password)
    with pg_store.pool().connection() as conn:
        with conn.transaction():
            row = conn.execute(
                "UPDATE users SET password_hash = %s, must_change_password = TRUE WHERE id = %s RETURNING id",
                (hash_password(secret), user_id),
            ).fetchone()
            if row is None:
                raise AuthError(404, "没有这个账号")
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))


def set_disabled(user_id, disabled=True):
    with pg_store.pool().connection() as conn:
        with conn.transaction():
            row = conn.execute(
                "UPDATE users SET status = %s WHERE id = %s RETURNING id",
                ("disabled" if disabled else "active", user_id),
            ).fetchone()
            if row is None:
                raise AuthError(404, "没有这个账号")
            if disabled:
                conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))


def list_users():
    with pg_store.pool().connection() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    return [_user_out(row) for row in rows]


def get_user(user_id):
    with pg_store.pool().connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    return _user_out(row)