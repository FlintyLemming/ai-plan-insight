# ai_plan_insight/admin_auth.py
"""Admin password, sessions and login rate limiting.

Zero new dependencies: scrypt from hashlib, tokens from secrets. Sessions
live in the `admin_session` table (SHA-256 of the token, never the token
itself), so a restart does not log anyone out and "log out everywhere" is
a single DELETE.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from . import config_store

logger = logging.getLogger(__name__)

COOKIE_NAME = "apiadmin"
SESSION_TTL = timedelta(days=7)
SESSION_RENEW_WITHIN = timedelta(days=1)
MIN_PASSWORD_LENGTH = 8

_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32

LOGIN_WINDOW_SECONDS = 60
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 60


# ── password ──────────────────────────────────────────────────

def hash_password(password: str, salt: bytes | None = None) -> str:
    """Return `scrypt$N$r$p$<salt_b64>$<hash_b64>`."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
    except Exception:
        return False
    return secrets.compare_digest(dk, expected)


def password_configured(conn: sqlite3.Connection) -> bool:
    return bool(config_store.get_setting(conn, "admin_password_hash"))


def init_admin_password(conn: sqlite3.Connection) -> None:
    """Apply the ADMIN_PASSWORD / ADMIN_PASSWORD_RESET environment variables.

    Precedence: RESET overwrites unconditionally and kills every session;
    otherwise a password already in the database wins and the env var is
    ignored; otherwise ADMIN_PASSWORD seeds it. With neither, the admin API
    stays 503 — there is never a window where it is reachable without a
    password.
    """
    reset = os.environ.get("ADMIN_PASSWORD_RESET", "").strip()
    if reset:
        config_store.set_setting(conn, "admin_password_hash", hash_password(reset))
        delete_all_sessions(conn)
        logger.warning(
            "admin password reset from ADMIN_PASSWORD_RESET; "
            "remove that environment variable and restart"
        )
        return

    if password_configured(conn):
        if os.environ.get("ADMIN_PASSWORD", "").strip():
            logger.debug("ADMIN_PASSWORD ignored: the stored password takes precedence")
        return

    env_password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if env_password:
        config_store.set_setting(conn, "admin_password_hash", hash_password(env_password))
        logger.info("admin password initialized from ADMIN_PASSWORD")
        return

    logger.warning(
        "no admin password configured; /api/admin/* returns 503 until "
        "ADMIN_PASSWORD is set and the service restarted"
    )


# ── sessions ──────────────────────────────────────────────────

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def purge_expired(conn: sqlite3.Connection, now: datetime | None = None) -> None:
    now = now or datetime.now().astimezone()
    conn.execute("DELETE FROM admin_session WHERE expires_at <= ?", (now.isoformat(),))
    conn.commit()


def create_session(conn: sqlite3.Connection, now: datetime | None = None) -> str:
    now = now or datetime.now().astimezone()
    purge_expired(conn, now)
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO admin_session (token_hash, created_at, expires_at) VALUES (?, ?, ?)",
        (_token_hash(token), now.isoformat(), (now + SESSION_TTL).isoformat()),
    )
    conn.commit()
    return token


def validate_session(
    conn: sqlite3.Connection, token: str | None, now: datetime | None = None
) -> bool:
    """True when the token maps to a live session; slides the expiry when
    less than SESSION_RENEW_WITHIN remains."""
    if not token:
        return False
    now = now or datetime.now().astimezone()
    digest = _token_hash(token)
    row = conn.execute(
        "SELECT expires_at FROM admin_session WHERE token_hash = ?", (digest,)
    ).fetchone()
    if row is None:
        return False
    try:
        expires = datetime.fromisoformat(row[0])
    except ValueError:
        conn.execute("DELETE FROM admin_session WHERE token_hash = ?", (digest,))
        conn.commit()
        return False
    if expires <= now:
        conn.execute("DELETE FROM admin_session WHERE token_hash = ?", (digest,))
        conn.commit()
        return False
    if expires - now < SESSION_RENEW_WITHIN:
        conn.execute(
            "UPDATE admin_session SET expires_at = ? WHERE token_hash = ?",
            ((now + SESSION_TTL).isoformat(), digest),
        )
        conn.commit()
    return True


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM admin_session WHERE token_hash = ?", (_token_hash(token),))
    conn.commit()


def delete_all_sessions(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM admin_session")
    conn.commit()


# ── login rate limiting ───────────────────────────────────────

class LoginRateLimiter:
    """In-process sliding window per client IP.

    LOGIN_MAX_FAILURES failures inside LOGIN_WINDOW_SECONDS lock the IP for
    LOGIN_LOCKOUT_SECONDS. State is per process and lost on restart, which
    is fine for a single-instance panel.
    """

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    @staticmethod
    def _now(now: float | None) -> float:
        return time.monotonic() if now is None else now

    def is_locked(self, ip: str, now: float | None = None) -> bool:
        now = self._now(now)
        until = self._locked_until.get(ip)
        if until is None:
            return False
        if until <= now:
            self._locked_until.pop(ip, None)
            self._failures.pop(ip, None)
            return False
        return True

    def record_failure(self, ip: str, now: float | None = None) -> None:
        now = self._now(now)
        hits = [t for t in self._failures.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
        hits.append(now)
        self._failures[ip] = hits
        if len(hits) >= LOGIN_MAX_FAILURES:
            self._locked_until[ip] = now + LOGIN_LOCKOUT_SECONDS

    def record_success(self, ip: str) -> None:
        self._failures.pop(ip, None)
        self._locked_until.pop(ip, None)


rate_limiter = LoginRateLimiter()


# ── FastAPI plumbing ──────────────────────────────────────────

def open_conn() -> sqlite3.Connection:
    """A fresh connection to the usage DB.

    Imported lazily so this module has no import-time dependency on web.py
    (web.py -> admin_api.py -> admin_auth.py at import time).
    """
    from . import web
    return web._usage_conn()


def set_session_cookie(response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True, samesite="lax", path="/",
        max_age=int(SESSION_TTL.total_seconds()),
        secure=request.url.scheme == "https",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


async def require_admin(request: Request) -> None:
    """FastAPI dependency: 503 when no password is configured, 401 when the
    request carries no live session cookie."""
    with closing(open_conn()) as conn:
        if not password_configured(conn):
            raise HTTPException(status_code=503, detail="管理密码未设置")
        if not validate_session(conn, request.cookies.get(COOKIE_NAME)):
            raise HTTPException(status_code=401, detail="Unauthorized")
