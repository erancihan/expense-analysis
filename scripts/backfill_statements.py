#!/usr/bin/env python3
"""
Link existing expenses to their statements and fill installment fields, in place.

Dry run by default: parses every PDF in data/pdfs/, matches the transactions to
existing expenses and prints what would change. --apply first backs up the
database and every expense's tag set to data/backups/, then writes.

Usage:
    ./scripts/backfill_statements.py                         # dry run
    ./scripts/backfill_statements.py --apply                 # write
    ./scripts/backfill_statements.py --email you@example.com # choose the owner
"""

import glob
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sqlalchemy.engine import make_url

from src.backfill import TagSetChanged, backfill_statements, tag_snapshot
from src.config import DATABASE_URL
from src.database import SessionLocal
from src.import_pdfs import DATA_DIR
from src.parsers.isbank import IsbankParser
from src.users import NoOwnerError, resolve_owner

BACKUP_DIR = os.path.join(ROOT, 'data', 'backups')
SHOW = 20   # rows of each unmatched list to print


def _arg_value(flag):
    """Return the value following `--flag` in argv, or None."""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def _backup(db, user_id):
    """Copy the database and dump every tag set. Returns the two paths."""
    url = make_url(DATABASE_URL)
    if url.get_backend_name() != 'sqlite' or not url.database:
        sys.exit('Refusing to --apply: backups are only implemented for a SQLite file database.')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    os.makedirs(BACKUP_DIR, exist_ok=True)

    # The online-backup API, not a file copy: the database runs in WAL mode,
    # and copying expenses.db alone can miss committed pages still in the WAL.
    db_copy = os.path.join(BACKUP_DIR, f'expenses.{stamp}.db')
    source, target = sqlite3.connect(url.database), sqlite3.connect(db_copy)
    with target:
        source.backup(target)
    source.close()
    target.close()

    tags_path = os.path.join(BACKUP_DIR, f'tags.{stamp}.json')
    with open(tags_path, 'w') as f:
        json.dump({str(k): v for k, v in tag_snapshot(db, user_id).items()}, f, indent=1)
    return db_copy, tags_path


def _print_report(report, applied):
    print(f"\n{'='*60}")
    print('Backfill ' + ('APPLIED' if applied else 'DRY RUN — nothing written'))
    print(f"  Statements:                 {report.statements}")
    print(f"  Expenses matched:           {report.matched}")
    print(f"  With installment info:      {report.installments}")
    print(f"  Re-delivered PDFs skipped:  {len(report.duplicate_files)}")
    for skipped, kept in report.duplicate_files:
        print(f"      {skipped}  (same statement as {kept})")
    print(f"  PDFs with no closing date:  {len(report.files_without_closing_date)}")
    for name in report.files_without_closing_date:
        print(f"      {name}")
    print(f"  Period changes:             {sum(report.period_changes.values())} rows")
    for (old, new), n in sorted(report.period_changes.items(),
                                key=lambda kv: (kv[0][0] or '', kv[0][1])):
        print(f"      {old} → {new}: {n}")
    print(f"  Currency corrections:       {len(report.currency_changes)}")
    for expense_id, old, new in report.currency_changes[:SHOW]:
        print(f"      expense {expense_id}: {old} → {new}")
    print(f"  Parsed rows with no expense: {len(report.parsed_unmatched)} (not inserted)")
    for name, day, desc, amount in report.parsed_unmatched[:SHOW]:
        print(f"      {day} {amount:>12.2f}  {desc[:40]}  [{name}]")
    print(f"  Expenses with no parsed row: {len(report.db_unmatched)} (left as they are)")
    if report.db_unmatched:
        print(f"      ids: {report.db_unmatched[:SHOW]}")
    print(f"{'='*60}")
    if not applied:
        print('Re-run with --apply (make backfill-statements APPLY=1) to write.')


def main():
    apply = '--apply' in sys.argv
    owner_email = _arg_value('--email')
    # The parser logs one INFO line per PDF; keep the report readable.
    logging.basicConfig(level=logging.WARNING, handlers=[logging.StreamHandler(sys.stdout)])

    db = SessionLocal()
    try:
        try:
            user = resolve_owner(db, owner_email)
        except NoOwnerError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        if apply:
            db_copy, tags_path = _backup(db, user.id)
            print(f"Backup:       {db_copy}")
            print(f"Tag snapshot: {tags_path}")

        pdf_paths = sorted(
            glob.glob(os.path.join(DATA_DIR, 'bank(*.pdf'))
            + glob.glob(os.path.join(DATA_DIR, 'isbank_*.pdf'))
        )
        try:
            report = backfill_statements(
                db, user.id, pdf_paths, IsbankParser().extract_transactions, apply=apply)
        except TagSetChanged as e:
            print(f"ABORTED: {e}", file=sys.stderr)
            sys.exit(2)
        _print_report(report, apply)
    finally:
        db.close()


if __name__ == '__main__':
    main()
