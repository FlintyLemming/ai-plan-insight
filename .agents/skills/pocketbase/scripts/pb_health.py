#!/usr/bin/env python3
"""PocketBase health check and connectivity test."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pb_config import (
    PB_URL, PB_SUPERUSER_EMAIL, PB_SUPERUSER_PASSWORD,
    pb_request, get_superuser_token, print_result, PBRequestError,
)


def main():
    # 1. Health endpoint
    print(f"Checking PocketBase at {PB_URL} ...")
    try:
        data = pb_request("GET", "/api/health")
        print_result(True, 200, {
            "message": "PocketBase is healthy",
            "health": data,
        })
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)
    except Exception as e:
        print_result(False, 0, {"message": f"Connection failed: {e}"})
        sys.exit(1)

    # 2. Superuser auth test (if credentials are set)
    if PB_SUPERUSER_EMAIL and PB_SUPERUSER_PASSWORD:
        print("\nTesting superuser authentication ...")
        try:
            token = get_superuser_token(force=True)
            print_result(True, 200, {
                "message": "Superuser authentication successful",
                "token_preview": token[:20] + "...",
            })
        except SystemExit:
            pass  # print_result already called inside get_superuser_token
    else:
        print("\nSkipping superuser auth test (credentials not set).")


if __name__ == "__main__":
    main()
