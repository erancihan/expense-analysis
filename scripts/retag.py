#!/usr/bin/env python3
"""
Re-tag expenses or show tagging statistics.

Usage:
    ./scripts/retag.py                   # Re-tag all expenses
    ./scripts/retag.py --stats           # Show tagging statistics
"""

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import SessionLocal
from src.tag_engine import TagEngine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)-35s - %(levelname)-7s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)


def main():
    db = SessionLocal()

    try:
        engine = TagEngine(db)

        if '--stats' in sys.argv:
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
