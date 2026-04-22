#!/usr/bin/env python3
"""
PocketBase collection management.

Usage:
  python scripts/pb_collections.py list
  python scripts/pb_collections.py get <name_or_id>
  python scripts/pb_collections.py create '<json>'
  python scripts/pb_collections.py create --file schema.json
  python scripts/pb_collections.py update <name_or_id> '<json>'
  python scripts/pb_collections.py delete <name_or_id>
  python scripts/pb_collections.py import --file collections.json
"""

import argparse
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pb_config import pb_authed_request, print_result, PBRequestError


def cmd_list(args):
    try:
        params = []
        if args.filter:
            params.append(f"filter={_encode(args.filter)}")
        if args.sort:
            params.append(f"sort={_encode(args.sort)}")
        if args.page:
            params.append(f"page={args.page}")
        if args.perPage:
            params.append(f"perPage={args.perPage}")
        qs = "?" + "&".join(params) if params else ""
        data = pb_authed_request("GET", f"/api/collections{qs}")
        print_result(True, 200, data)
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def cmd_get(args):
    if not args.name_or_id:
        print_result(False, 0, {"message": "Collection name or ID is required"})
        sys.exit(1)
    try:
        data = pb_authed_request("GET", f"/api/collections/{args.name_or_id}")
        print_result(True, 200, data)
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def cmd_create(args):
    body = _get_body(args)
    try:
        data = pb_authed_request("POST", "/api/collections", data=body)
        print_result(True, 200, data)
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def cmd_update(args):
    if not args.name_or_id:
        print_result(False, 0, {"message": "Collection name or ID is required"})
        sys.exit(1)
    body = _get_body(args)
    try:
        data = pb_authed_request("PATCH", f"/api/collections/{args.name_or_id}", data=body)
        print_result(True, 200, data)
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def cmd_delete(args):
    if not args.name_or_id:
        print_result(False, 0, {"message": "Collection name or ID is required"})
        sys.exit(1)
    try:
        pb_authed_request("DELETE", f"/api/collections/{args.name_or_id}")
        print_result(True, 204, {"message": f"Collection '{args.name_or_id}' deleted"})
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


def cmd_import(args):
    if not args.file:
        print_result(False, 0, {"message": "--file is required for import"})
        sys.exit(1)
    body = _load_json_file(args.file)
    # Wrap in {collections: [...]} if the file is an array
    if isinstance(body, list):
        body = {"collections": body}
    try:
        pb_authed_request("PUT", "/api/collections/import", data=body)
        print_result(True, 204, {"message": "Collections imported successfully"})
    except PBRequestError as e:
        print_result(False, e.status, e.data)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _encode(value):
    """URL-encode a value."""
    import urllib.parse
    return urllib.parse.quote(str(value), safe="")


def _get_body(args):
    """Extract JSON body from --file or positional json_data argument."""
    if getattr(args, "file", None):
        return _load_json_file(args.file)
    if getattr(args, "json_data", None):
        try:
            return json.loads(args.json_data)
        except json.JSONDecodeError as e:
            print_result(False, 0, {"message": f"Invalid JSON: {e}"})
            sys.exit(1)
    print_result(False, 0, {"message": "JSON data or --file is required"})
    sys.exit(1)


def _load_json_file(path):
    """Load and parse a JSON file."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print_result(False, 0, {"message": f"File not found: {path}"})
        sys.exit(1)
    except json.JSONDecodeError as e:
        print_result(False, 0, {"message": f"Invalid JSON in file: {e}"})
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="PocketBase collection management")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    # list
    p_list = sub.add_parser("list", help="List collections")
    p_list.add_argument("--filter", help="Filter expression")
    p_list.add_argument("--sort", help="Sort expression")
    p_list.add_argument("--page", type=int, help="Page number")
    p_list.add_argument("--perPage", type=int, help="Items per page")
    p_list.set_defaults(func=cmd_list)

    # get
    p_get = sub.add_parser("get", help="Get a collection")
    p_get.add_argument("name_or_id", help="Collection name or ID")
    p_get.set_defaults(func=cmd_get)

    # create
    p_create = sub.add_parser("create", help="Create a collection")
    p_create.add_argument("json_data", nargs="?", help="JSON body")
    p_create.add_argument("--file", help="JSON file with collection schema")
    p_create.set_defaults(func=cmd_create)

    # update
    p_update = sub.add_parser("update", help="Update a collection")
    p_update.add_argument("name_or_id", help="Collection name or ID")
    p_update.add_argument("json_data", nargs="?", help="JSON body")
    p_update.add_argument("--file", help="JSON file with update data")
    p_update.set_defaults(func=cmd_update)

    # delete
    p_delete = sub.add_parser("delete", help="Delete a collection")
    p_delete.add_argument("name_or_id", help="Collection name or ID")
    p_delete.set_defaults(func=cmd_delete)

    # import
    p_import = sub.add_parser("import", help="Import collections from file")
    p_import.add_argument("--file", required=True, help="JSON file with collections")
    p_import.set_defaults(func=cmd_import)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
