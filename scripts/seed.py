#!/usr/bin/env python3
"""
Seed default tags and rules for a user.

Reads from data/seeds/tags.yaml (falls back to a built-in set). Safe to run
repeatedly.

The seed file is the source of truth for DEFAULT rules: rerunning this adds new
ones, applies changed match_type/priority, and removes rules the file no longer
declares. Rules you created in the web UI are never touched. Tags are only ever
added — a stale tag is left in place because deleting one would also delete every
assignment of it.

Usage:
    ./scripts/seed.py                      # seed the sole/first admin
    ./scripts/seed.py --email you@host     # seed a specific user
    make seed
"""

import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import SessionLocal
from src.seeding import seed_default_tags
from src.users import resolve_owner, NoOwnerError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)-35s - %(levelname)-7s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Seed default tags/rules for a user.")
    parser.add_argument('--email', help='User to seed (default: sole/first admin)')
    args = parser.parse_args()

    db = SessionLocal()
    try:
        try:
            owner = resolve_owner(db, args.email)
        except NoOwnerError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        counts = seed_default_tags(db, owner.id)
        print(f"Seeding complete for {owner.email}: "
              f"{counts['tags']} tags added; rules "
              f"{counts['rules']} added, {counts['updated']} updated, "
              f"{counts['removed']} removed.")
    finally:
        db.close()


if __name__ == '__main__':
    main()
