#!/usr/bin/env python3
"""
PocketBase backup management.

Usage:
  python scripts/pb_backups.py list
  python scripts/pb_backups.py create [name.zip]
  python scripts/pb_backups.py restore <key>
  python scripts/pb_backups.py delete <key>
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pb_config import pb_authed_request, print_result, PBRequestError


def cmd_list(args):
    try:
        data = pb_authed_request("GET", "/api/backups")
        print_result(True, 200, data)
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def cmd_create(args):
    body = {}
    if args.name:
        body["name"] = args.name
    try:
        pb_authed_request("POST", "/api/backups", data=body)
        print_result(True, 204, {
            "message": f"Backup created{' as ' + args.name if args.name else ''}"
        })
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def cmd_restore(args):
    try:
        pb_authed_request("POST", f"/api/backups/{args.key}/restore")
        print_result(True, 204, {
            "message": f"Backup '{args.key}' restore initiated"
        })
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def cmd_delete(args):
    try:
        pb_authed_request("DELETE", f"/api/backups/{args.key}")
        print_result(True, 204, {
            "message": f"Backup '{args.key}' deleted"
        })
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="PocketBase backup management")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    # list
    p_list = sub.add_parser("list", help="List backups")
    p_list.set_defaults(func=cmd_list)

    # create
    p_create = sub.add_parser("create", help="Create a backup")
    p_create.add_argument("name", nargs="?", help="Backup filename (e.g. backup.zip)")
    p_create.set_defaults(func=cmd_create)

    # restore
    p_restore = sub.add_parser("restore", help="Restore a backup")
    p_restore.add_argument("key", help="Backup key/filename")
    p_restore.set_defaults(func=cmd_restore)

    # delete
    p_delete = sub.add_parser("delete", help="Delete a backup")
    p_delete.add_argument("key", help="Backup key/filename")
    p_delete.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
