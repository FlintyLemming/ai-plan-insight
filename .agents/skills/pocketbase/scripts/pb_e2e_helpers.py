"""
Reusable E2E test helpers for PocketBase projects.

Provides a test runner, HTTP helpers, and user lifecycle management
for writing project-specific access-control tests.

Usage (see references/e2e-testing.md for path setup):
    from pb_e2e_helpers import TestRunner, req, user_login, create_test_user, ...
"""

import urllib.parse

from pb_config import pb_request, pb_authed_request, PBRequestError


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

class TestRunner:
    """Simple test runner that tracks pass/fail counts."""

    def __init__(self, title):
        self.title = title
        self.passed = 0
        self.failed = 0
        print("=" * 60)
        print(title)
        print("=" * 60)

    def ok(self, label):
        """Record a passing test."""
        self.passed += 1
        print(f"  \u2713 {label}")

    def fail(self, label, detail=""):
        """Record a failing test."""
        self.failed += 1
        suffix = f": {detail}" if detail else ""
        print(f"  \u2717 {label}{suffix}")

    def check(self, label, condition, detail=""):
        """Assert a condition — records pass or fail."""
        if condition:
            self.ok(label)
        else:
            self.fail(label, detail)

    def section(self, title):
        """Print a section header."""
        print(f"\n--- {title} ---")

    def summary(self):
        """Print results and return exit code (0=pass, 1=fail)."""
        print("\n" + "=" * 60)
        print(f"Results: {self.passed} passed, {self.failed} failed")
        print("=" * 60)
        return 0 if self.failed == 0 else 1


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def req(method, path, data=None, token=None):
    """
    Make an HTTP request as a regular user (or unauthenticated).

    Returns:
        (status, response_dict) — always a tuple, never raises on HTTP errors.
    """
    return pb_request(method, path, data=data, token=token, raw_response=True)


# ---------------------------------------------------------------------------
# User lifecycle helpers
# ---------------------------------------------------------------------------

def user_login(email, password, collection="users"):
    """
    Authenticate a regular user.

    Returns:
        (token, user_id)
    """
    status, data = req(
        "POST", f"/api/collections/{collection}/auth-with-password",
        {"identity": email, "password": password},
    )
    if status != 200:
        raise RuntimeError(f"Login failed for {email}: HTTP {status} {data}")
    return data["token"], data["record"]["id"]


def create_test_user(email, password, name, collection="users"):
    """
    Create a user via the public API (assumes createRule is open).

    Returns:
        user_id
    """
    status, data = req("POST", f"/api/collections/{collection}/records", {
        "email": email,
        "password": password,
        "passwordConfirm": password,
        "name": name,
    })
    if status not in (200, 201):
        raise RuntimeError(
            f"Failed to create user {email}: HTTP {status} {data}")
    return data["id"]


def superuser_create_user(email, password, name, collection="users"):
    """
    Create a user via superuser (for collections with locked createRule).

    Returns:
        user_id
    """
    try:
        data = pb_authed_request(
            "POST", f"/api/collections/{collection}/records", {
                "email": email,
                "password": password,
                "passwordConfirm": password,
                "name": name,
            })
        return data["id"]
    except PBRequestError as e:
        raise RuntimeError(
            f"Superuser failed to create user {email}: HTTP {e.status} {e.data}"
        ) from e


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------

def pre_cleanup(emails, collection="users"):
    """Delete stale test users from previous runs via superuser."""
    for email in emails:
        try:
            f = urllib.parse.quote(f'email = "{email}"')
            data = pb_authed_request(
                "GET",
                f"/api/collections/{collection}/records?filter={f}&perPage=5",
            )
            for r in data.get("items", []):
                pb_authed_request(
                    "DELETE",
                    f"/api/collections/{collection}/records/{r['id']}",
                )
        except PBRequestError:
            pass


# ---------------------------------------------------------------------------
# Superuser record helpers
# ---------------------------------------------------------------------------

def superuser_delete(collection, record_id):
    """Delete a record as superuser. Ignores 404."""
    try:
        pb_authed_request(
            "DELETE", f"/api/collections/{collection}/records/{record_id}")
    except PBRequestError:
        pass


def superuser_get(collection, record_id):
    """
    GET a record as superuser.

    Returns:
        (status, data)
    """
    try:
        data = pb_authed_request(
            "GET", f"/api/collections/{collection}/records/{record_id}")
        return 200, data
    except PBRequestError as e:
        return e.status, e.data


def superuser_list(collection, filter_expr=None):
    """
    List records as superuser.

    Returns:
        (status, data)
    """
    path = f"/api/collections/{collection}/records"
    if filter_expr:
        path += f"?filter={urllib.parse.quote(filter_expr)}"
    try:
        data = pb_authed_request("GET", path)
        return 200, data
    except PBRequestError as e:
        return e.status, e.data
