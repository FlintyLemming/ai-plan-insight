#!/usr/bin/env python3
"""
PocketBase authentication script.

Usage:
  # Superuser auth (uses env vars)
  python scripts/pb_auth.py

  # User auth against a specific collection
  python scripts/pb_auth.py --collection users --identity user@example.com --password secret
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pb_config import (
    pb_request, get_superuser_token, print_result, PBRequestError,
)


def auth_user(collection, identity, password):
    """Authenticate a user against an auth collection."""
    try:
        result = pb_request("POST",
            f"/api/collections/{collection}/auth-with-password",
            {"identity": identity, "password": password})
        print_result(True, 200, {
            "message": f"Authentication successful for {collection}",
            "token": result["token"],
            "record": result.get("record"),
        })
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="PocketBase authentication")
    parser.add_argument("--collection", help="Auth collection name (default: _superusers)")
    parser.add_argument("--identity", help="Username or email")
    parser.add_argument("--password", help="Password")
    args = parser.parse_args()

    if args.collection and args.collection != "_superusers":
        if not args.identity or not args.password:
            parser.error("--identity and --password are required for user auth")
        auth_user(args.collection, args.identity, args.password)
    else:
        # Superuser auth
        try:
            token = get_superuser_token(force=True)
            print_result(True, 200, {
                "message": "Superuser authentication successful",
                "token": token,
            })
        except SystemExit:
            pass  # print_result already called


if __name__ == "__main__":
    main()
