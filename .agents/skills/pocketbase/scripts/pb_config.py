"""
PocketBase shared configuration, authentication, and HTTP helpers.
All PB scripts import this module.
"""

import json
import os
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _load_env_file():
    """Read .env file from cwd or nearest parent directory (simple key=value parser).

    Searches the current working directory first, then walks up parent
    directories until a .env file is found or the filesystem root is reached.
    This supports monorepo setups where scripts run from a subdirectory.
    """
    # Walk up from cwd to find the nearest .env file
    search_dir = os.path.abspath(os.getcwd())
    env_path = None
    while True:
        candidate = os.path.join(search_dir, ".env")
        if os.path.isfile(candidate):
            env_path = candidate
            break
        parent = os.path.dirname(search_dir)
        if parent == search_dir:
            # Reached filesystem root without finding .env
            return
        search_dir = parent

    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value

_load_env_file()

PB_URL = os.environ.get("PB_URL", "http://127.0.0.1:8090").rstrip("/")
PB_SUPERUSER_EMAIL = os.environ.get("PB_SUPERUSER_EMAIL", "")
PB_SUPERUSER_PASSWORD = os.environ.get("PB_SUPERUSER_PASSWORD", "")

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def pb_request(method, path, data=None, token=None, raw_response=False):
    """
    Send an HTTP request to the PocketBase API.

    Args:
        method: HTTP method (GET, POST, PUT, PATCH, DELETE).
        path: API path (e.g. "/api/health"). Query string allowed.
        data: Dict to send as JSON body (for POST/PUT/PATCH).
        token: Auth token for Authorization header (raw, no Bearer prefix).
        raw_response: If True, return (status, parsed_json) tuple.

    Returns:
        Parsed JSON response, or (status, parsed_json) if raw_response=True.

    Raises:
        SystemExit on HTTP errors (after printing structured output).
    """
    url = PB_URL + path if path.startswith("/") else PB_URL + "/" + path

    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", token)

    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            raw = resp.read()
            parsed = json.loads(raw) if raw else None
            if raw_response:
                return status, parsed
            return parsed
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            parsed = json.loads(e.read())
        except Exception:
            parsed = {"message": str(e)}
        if raw_response:
            return status, parsed
        raise PBRequestError(status, parsed)


class PBRequestError(Exception):
    """Raised when PocketBase returns an HTTP error."""
    def __init__(self, status, data):
        self.status = status
        self.data = data
        super().__init__(f"HTTP {status}: {data}")


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

_cached_token = None


def get_superuser_token(force=False):
    """
    Authenticate as superuser and return the bearer token string.
    Caches the token for subsequent calls unless force=True.
    """
    global _cached_token
    if _cached_token and not force:
        return _cached_token

    if not PB_SUPERUSER_EMAIL or not PB_SUPERUSER_PASSWORD:
        print_result(False, 0, {
            "message": "PB_SUPERUSER_EMAIL and PB_SUPERUSER_PASSWORD must be set"
        })
        sys.exit(1)

    try:
        result = pb_request("POST",
            "/api/collections/_superusers/auth-with-password",
            {"identity": PB_SUPERUSER_EMAIL, "password": PB_SUPERUSER_PASSWORD})
        _cached_token = result["token"]
        return _cached_token
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def pb_authed_request(method, path, data=None, raw_response=False):
    """
    Like pb_request but automatically authenticates as superuser.
    On 401, retries once with a fresh token.
    """
    token = get_superuser_token()
    try:
        return pb_request(method, path, data=data, token=token,
                          raw_response=raw_response)
    except PBRequestError as e:
        if e.status == 401:
            token = get_superuser_token(force=True)
            return pb_request(method, path, data=data, token=token,
                              raw_response=raw_response)
        raise


# ---------------------------------------------------------------------------
# Output helper
# ---------------------------------------------------------------------------

def print_result(success, status, data):
    """Print structured JSON result to stdout."""
    print(json.dumps({"success": success, "status": status, "data": data},
                     indent=2, ensure_ascii=False))
