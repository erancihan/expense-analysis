#!/usr/bin/env python3
"""
Re-tag expenses or show tagging statistics.

Usage:
    ./scripts/retag.py                   # Re-tag the owner's expenses
    ./scripts/retag.py --stats           # Show tagging statistics
    ./scripts/retag.py --email you@host  # Target a specific user
"""

import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import SessionLocal
from src.tag_engine import TagEngine
from src.users import resolve_owner, NoOwnerError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)-35s - %(levelname)-7s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)


def main():
    parser = argparse.ArgumentParser(description="Re-tag expenses / show stats.")
    parser.add_argument('--stats', action='store_true', help='Show statistics only')
    parser.add_argument('--email', help='Target user (default: sole/first admin)')
    args = parser.parse_args()

    db = SessionLocal()

    try:
        try:
            owner = resolve_owner(db, args.email)
        except NoOwnerError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        engine = TagEngine(db, owner.id)

        if args.stats:
            stats = engine.get_stats()
            print(f"\nTagging Statistics:")
            print(f"  Total expenses: {stats['total_expenses']}")
            print(f"  Tagged:         {stats['tagged']} ({stats['coverage_pct']}%)")
            print(f"  Untagged:       {stats['untagged']}")
            print(f"\n  By tag:")
            for tag_name, count in stats['by_tag'].items():
                print(f"    {tag_name:20s}: {count}")
        else:
            print("Re-tagging all expenses...")
            applied = engine.retag_all()
            print(f"Done. Applied {applied} tags.")

            stats = engine.get_stats()
            print(f"\nCoverage: {stats['coverage_pct']}% ({stats['tagged']}/{stats['total_expenses']})")
    finally:
        db.close()


if __name__ == '__main__':
    main()
