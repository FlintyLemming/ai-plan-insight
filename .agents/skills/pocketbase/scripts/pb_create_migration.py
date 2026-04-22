#!/usr/bin/env python3
"""
PocketBase migration file generator.

Usage:
  python scripts/pb_create_migration.py "create_posts_collection"
  python scripts/pb_create_migration.py "add_status_field" --dir ./pb_migrations
"""

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pb_config import print_result

TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "migration-template.js"
)

DEFAULT_MIGRATIONS_DIR = "pb_migrations"


def sanitize_name(name):
    """Convert a description to a safe filename component."""
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9_]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name


def main():
    parser = argparse.ArgumentParser(
        description="Generate a PocketBase migration file")
    parser.add_argument("description", help="Migration description (e.g. 'create_posts_collection')")
    parser.add_argument("--dir", default=DEFAULT_MIGRATIONS_DIR,
                        help=f"Output directory (default: {DEFAULT_MIGRATIONS_DIR})")
    args = parser.parse_args()

    # Read template
    if not os.path.isfile(TEMPLATE_PATH):
        print_result(False, 0, {
            "message": f"Template not found: {TEMPLATE_PATH}"
        })
        sys.exit(1)

    with open(TEMPLATE_PATH, "r") as f:
        template = f.read()

    # Generate filename with timestamp
    timestamp = int(time.time())
    safe_name = sanitize_name(args.description)
    if not safe_name:
        print_result(False, 0, {"message": "Invalid migration description"})
        sys.exit(1)

    filename = f"{timestamp}_{safe_name}.js"
    out_dir = args.dir
    os.makedirs(out_dir, exist_ok=True)
    filepath = os.path.join(out_dir, filename)

    with open(filepath, "w") as f:
        f.write(template)

    abs_path = os.path.abspath(filepath)
    print_result(True, 0, {
        "message": f"Migration file created: {filepath}",
        "file": abs_path,
        "filename": filename,
    })


if __name__ == "__main__":
    main()
