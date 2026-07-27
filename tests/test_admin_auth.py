import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ai_plan_insight import admin_auth, config_store

UTC8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=UTC8)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "usage.db")
    config_store.init_config_schema(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD_RESET", raising=False)


class TestHashing:
    def test_hash_format(self):
        stored = admin_auth.hash_password("hunter2000")
        parts = stored.split("$")
        assert parts[0] == "scrypt"
        assert parts[1:4] == ["16384", "8", "1"]
        assert len(parts) == 6

    def test_verify_accepts_the_right_password(self):
        stored = admin_auth.hash_password("hunter2000")
        assert admin_auth.verify_password("hunter2000", stored) is True

    def test_verify_rejects_the_wrong_password(self):
        stored = admin_auth.hash_password("hunter2000")
        assert admin_auth.verify_password("hunter2001", stored) is False

    def test_salt_makes_hashes_unique(self):
        assert admin_auth.hash_password("same") != admin_auth.hash_password("same")

    def test_verify_rejects_garbage_stored_value(self):
        assert admin_auth.verify_password("x", "not-a-hash") is False
        assert admin_auth.verify_password("x", "") is False


class TestPasswordInit:
    def test_env_password_is_stored_when_db_has_none(self, conn, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "from-env-123")
        admin_auth.init_admin_password(conn)
        stored = config_store.get_setting(conn, "admin_password_hash")
        assert admin_auth.verify_password("from-env-123", stored)

    def test_no_env_and_no_db_leaves_password_unconfigured(self, conn):
        admin_auth.init_admin_password(conn)
        assert config_store.get_setting(conn, "admin_password_hash") is None
        assert admin_auth.password_configured(conn) is False

    def test_db_password_wins_over_env(self, conn, monkeypatch):
        config_store.set_setting(conn, "admin_password_hash", admin_auth.hash_password("in-db-123"))
        monkeypatch.setenv("ADMIN_PASSWORD", "from-env-123")
        admin_auth.init_admin_password(conn)
        stored = config_store.get_setting(conn, "admin_password_hash")
        assert admin_auth.verify_password("in-db-123", stored)

    def test_reset_env_overwrites_unconditionally(self, conn, monkeypatch):
        config_store.set_setting(conn, "admin_password_hash", admin_auth.hash_password("old-one-1"))
        monkeypatch.setenv("ADMIN_PASSWORD_RESET", "brand-new-1")
        admin_auth.init_admin_password(conn)
        stored = config_store.get_setting(conn, "admin_password_hash")
        assert admin_auth.verify_password("brand-new-1", stored)

    def test_reset_env_clears_all_sessions(self, conn, monkeypatch):
        admin_auth.create_session(conn, now=NOW)
        monkeypatch.setenv("ADMIN_PASSWORD_RESET", "brand-new-1")
        admin_auth.init_admin_password(conn)
        assert conn.execute("SELECT COUNT(*) FROM admin_session").fetchone()[0] == 0


class TestSessions:
    def test_create_returns_a_token_and_stores_only_its_hash(self, conn):
        token = admin_auth.create_session(conn, now=NOW)
        assert len(token) > 20
        stored = conn.execute("SELECT token_hash FROM admin_session").fetchone()[0]
        assert token not in stored

    def test_validate_accepts_a_fresh_token(self, conn):
        token = admin_auth.create_session(conn, now=NOW)
        assert admin_auth.validate_session(conn, token, now=NOW) is True

    def test_validate_rejects_unknown_and_empty_tokens(self, conn):
        assert admin_auth.validate_session(conn, "nope", now=NOW) is False
        assert admin_auth.validate_session(conn, None, now=NOW) is False

    def test_validate_rejects_and_deletes_an_expired_token(self, conn):
        token = admin_auth.create_session(conn, now=NOW)
        later = NOW + timedelta(days=8)
        assert admin_auth.validate_session(conn, token, now=later) is False
        assert conn.execute("SELECT COUNT(*) FROM admin_session").fetchone()[0] == 0

    def test_session_slides_when_under_one_day_remains(self, conn):
        token = admin_auth.create_session(conn, now=NOW)
        near_expiry = NOW + timedelta(days=6, hours=12)
        assert admin_auth.validate_session(conn, token, now=near_expiry) is True
        expires = datetime.fromisoformat(
            conn.execute("SELECT expires_at FROM admin_session").fetchone()[0]
        )
        assert expires == near_expiry + admin_auth.SESSION_TTL

    def test_session_does_not_slide_while_fresh(self, conn):
        token = admin_auth.create_session(conn, now=NOW)
        original = conn.execute("SELECT expires_at FROM admin_session").fetchone()[0]
        admin_auth.validate_session(conn, token, now=NOW + timedelta(days=1))
        assert conn.execute("SELECT expires_at FROM admin_session").fetchone()[0] == original

    def test_delete_session_removes_only_that_token(self, conn):
        keep = admin_auth.create_session(conn, now=NOW)
        drop = admin_auth.create_session(conn, now=NOW)
        admin_auth.delete_session(conn, drop)
        assert admin_auth.validate_session(conn, keep, now=NOW) is True
        assert admin_auth.validate_session(conn, drop, now=NOW) is False

    def test_delete_all_sessions_clears_the_table(self, conn):
        admin_auth.create_session(conn, now=NOW)
        admin_auth.create_session(conn, now=NOW)
        admin_auth.delete_all_sessions(conn)
        assert conn.execute("SELECT COUNT(*) FROM admin_session").fetchone()[0] == 0

    def test_login_purges_expired_rows(self, conn):
        admin_auth.create_session(conn, now=NOW - timedelta(days=30))
        admin_auth.create_session(conn, now=NOW)
        assert conn.execute("SELECT COUNT(*) FROM admin_session").fetchone()[0] == 1


class TestRateLimiter:
    def test_not_locked_initially(self):
        rl = admin_auth.LoginRateLimiter()
        assert rl.is_locked("1.2.3.4", now=0.0) is False

    def test_locks_after_five_failures_in_the_window(self):
        rl = admin_auth.LoginRateLimiter()
        for i in range(5):
            rl.record_failure("1.2.3.4", now=float(i))
        assert rl.is_locked("1.2.3.4", now=5.0) is True

    def test_four_failures_do_not_lock(self):
        rl = admin_auth.LoginRateLimiter()
        for i in range(4):
            rl.record_failure("1.2.3.4", now=float(i))
        assert rl.is_locked("1.2.3.4", now=5.0) is False

    def test_failures_outside_the_window_do_not_count(self):
        rl = admin_auth.LoginRateLimiter()
        for i in range(4):
            rl.record_failure("1.2.3.4", now=float(i))
        rl.record_failure("1.2.3.4", now=200.0)
        assert rl.is_locked("1.2.3.4", now=200.0) is False

    def test_lock_expires_after_sixty_seconds(self):
        rl = admin_auth.LoginRateLimiter()
        for i in range(5):
            rl.record_failure("1.2.3.4", now=float(i))
        assert rl.is_locked("1.2.3.4", now=70.0) is False

    def test_lock_is_per_ip(self):
        rl = admin_auth.LoginRateLimiter()
        for i in range(5):
            rl.record_failure("1.2.3.4", now=float(i))
        assert rl.is_locked("5.6.7.8", now=5.0) is False

    def test_success_clears_the_counter(self):
        rl = admin_auth.LoginRateLimiter()
        for i in range(4):
            rl.record_failure("1.2.3.4", now=float(i))
        rl.record_success("1.2.3.4")
        rl.record_failure("1.2.3.4", now=5.0)
        assert rl.is_locked("1.2.3.4", now=5.0) is False
