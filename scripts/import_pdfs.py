#!/usr/bin/env python3
"""
Import İşbank PDFs from data/pdfs/ into the expense database.

Usage:
    ./scripts/import_pdfs.py                 # Import all PDFs
    ./scripts/import_pdfs.py --clear         # Clear existing data first
    ./scripts/import_pdfs.py --dry-run       # Parse but don't insert
"""

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import SessionLocal
from src.import_pdfs import import_pdfs

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)-35s - %(levelname)-7s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)


def main():
    clear = '--clear' in sys.argv
    dry_run = '--dry-run' in sys.argv

    db = SessionLocal()

    try:
        if dry_run:
            logger.info("DRY RUN — no data will be written")

        stats = import_pdfs(db, clear=clear, dry_run=dry_run)

        print(f"\n{'='*50}")
        print(f"Import Summary:")
        print(f"  Files found:         {stats['files_found']}")
        print(f"  Files skipped:       {stats['files_skipped']}")
        print(f"  Files parsed:        {stats['files_parsed']}")
        print(f"  Transactions parsed: {stats['transactions_parsed']}")
        print(f"  New inserted:        {stats['new_inserted']}")
        print(f"  Duplicates skipped:  {stats['duplicates_skipped']}")
        print(f"  Errors:              {stats['errors']}")
        print(f"  Tags applied:        {stats['tags_applied']}")
        print(f"{'='*50}")
    finally:
        db.close()


if __name__ == '__main__':
    main()
