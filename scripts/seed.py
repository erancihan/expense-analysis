#!/usr/bin/env python3
"""
Seed default tags and rules for a user.

Reads from data/seeds/tags.yaml (falls back to a built-in set) and inserts any
tags/rules that user doesn't already have. Safe to run multiple times.

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
              f"{counts['tags']} tags, {counts['rules']} rules.")
    finally:
        db.close()


if __name__ == '__main__':
    main()
