#!/usr/bin/env python3
"""
PocketBase record management.

Usage:
  python scripts/pb_records.py list <collection> [--filter "..."] [--sort "..."] [--expand "..."] [--page N] [--perPage N]
  python scripts/pb_records.py get <collection> <record_id>
  python scripts/pb_records.py create <collection> '<json>'
  python scripts/pb_records.py create <collection> --file data.json
  python scripts/pb_records.py update <collection> <record_id> '<json>'
  python scripts/pb_records.py update <collection> <record_id> --file data.json
  python scripts/pb_records.py delete <collection> <record_id>
"""

import argparse
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pb_config import pb_authed_request, print_result, PBRequestError


def _encode(value):
    import urllib.parse
    return urllib.parse.quote(str(value), safe="")


def _build_qs(args):
    """Build query string from common list parameters."""
    params = []
    if getattr(args, "filter", None):
        params.append(f"filter={_encode(args.filter)}")
    if getattr(args, "sort", None):
        params.append(f"sort={_encode(args.sort)}")
    if getattr(args, "expand", None):
        params.append(f"expand={_encode(args.expand)}")
    if getattr(args, "fields", None):
        params.append(f"fields={_encode(args.fields)}")
    if getattr(args, "page", None):
        params.append(f"page={args.page}")
    if getattr(args, "perPage", None):
        params.append(f"perPage={args.perPage}")
    return "?" + "&".join(params) if params else ""


def _get_body(args):
    """Extract JSON body from --file or positional json_data argument."""
    if getattr(args, "file", None):
        try:
            with open(args.file, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            print_result(False, 0, {"message": f"File not found: {args.file}"})
            sys.exit(1)
        except json.JSONDecodeError as e:
            print_result(False, 0, {"message": f"Invalid JSON in file: {e}"})
            sys.exit(1)
    if getattr(args, "json_data", None):
        try:
            return json.loads(args.json_data)
        except json.JSONDecodeError as e:
            print_result(False, 0, {"message": f"Invalid JSON: {e}"})
            sys.exit(1)
    print_result(False, 0, {"message": "JSON data or --file is required"})
    sys.exit(1)


def cmd_list(args):
    qs = _build_qs(args)
    try:
        data = pb_authed_request("GET",
            f"/api/collections/{args.collection}/records{qs}")
        print_result(True, 200, data)
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def cmd_get(args):
    qs_parts = []
    if getattr(args, "expand", None):
        qs_parts.append(f"expand={_encode(args.expand)}")
    if getattr(args, "fields", None):
        qs_parts.append(f"fields={_encode(args.fields)}")
    qs = "?" + "&".join(qs_parts) if qs_parts else ""
    try:
        data = pb_authed_request("GET",
            f"/api/collections/{args.collection}/records/{args.record_id}{qs}")
        print_result(True, 200, data)
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def cmd_create(args):
    body = _get_body(args)
    qs_parts = []
    if getattr(args, "expand", None):
        qs_parts.append(f"expand={_encode(args.expand)}")
    qs = "?" + "&".join(qs_parts) if qs_parts else ""
    try:
        data = pb_authed_request("POST",
            f"/api/collections/{args.collection}/records{qs}", data=body)
        print_result(True, 200, data)
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def cmd_update(args):
    body = _get_body(args)
    qs_parts = []
    if getattr(args, "expand", None):
        qs_parts.append(f"expand={_encode(args.expand)}")
    qs = "?" + "&".join(qs_parts) if qs_parts else ""
    try:
        data = pb_authed_request("PATCH",
            f"/api/collections/{args.collection}/records/{args.record_id}{qs}",
            data=body)
        print_result(True, 200, data)
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def cmd_delete(args):
    try:
        pb_authed_request("DELETE",
            f"/api/collections/{args.collection}/records/{args.record_id}")
        print_result(True, 204, {
            "message": f"Record '{args.record_id}' deleted from '{args.collection}'"
        })
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="PocketBase record management")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    # list
    p_list = sub.add_parser("list", help="List records")
    p_list.add_argument("collection", help="Collection name or ID")
    p_list.add_argument("--filter", help="Filter expression")
    p_list.add_argument("--sort", help="Sort expression")
    p_list.add_argument("--expand", help="Expand relations")
    p_list.add_argument("--fields", help="Fields to return")
    p_list.add_argument("--page", type=int, help="Page number")
    p_list.add_argument("--perPage", type=int, help="Items per page")
    p_list.set_defaults(func=cmd_list)

    # get
    p_get = sub.add_parser("get", help="Get a record")
    p_get.add_argument("collection", help="Collection name or ID")
    p_get.add_argument("record_id", help="Record ID")
    p_get.add_argument("--expand", help="Expand relations")
    p_get.add_argument("--fields", help="Fields to return")
    p_get.set_defaults(func=cmd_get)

    # create
    p_create = sub.add_parser("create", help="Create a record")
    p_create.add_argument("collection", help="Collection name or ID")
    p_create.add_argument("json_data", nargs="?", help="JSON body")
    p_create.add_argument("--file", help="JSON file with record data")
    p_create.add_argument("--expand", help="Expand relations in response")
    p_create.set_defaults(func=cmd_create)

    # update
    p_update = sub.add_parser("update", help="Update a record")
    p_update.add_argument("collection", help="Collection name or ID")
    p_update.add_argument("record_id", help="Record ID")
    p_update.add_argument("json_data", nargs="?", help="JSON body")
    p_update.add_argument("--file", help="JSON file with update data")
    p_update.add_argument("--expand", help="Expand relations in response")
    p_update.set_defaults(func=cmd_update)

    # delete
    p_delete = sub.add_parser("delete", help="Delete a record")
    p_delete.add_argument("collection", help="Collection name or ID")
    p_delete.add_argument("record_id", help="Record ID")
    p_delete.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
