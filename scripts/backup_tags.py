#!/usr/bin/env python3
"""
Backup tags and rules from the database to data/seeds/tags.yaml.

Exports every Tag and TagRule currently in the database into the same
YAML format that seed.py reads, so after a DB reset you can restore
everything with `make seed`.

Usage:
    ./scripts/backup_tags.py              # overwrite data/seeds/tags.yaml
    ./scripts/backup_tags.py -o backup.yaml  # write to a custom path
    make backup-tags
"""

import argparse
import logging
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import SessionLocal
from src.models import Tag, TagRule
from src.users import resolve_owner, NoOwnerError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)-35s - %(levelname)-7s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'seeds', 'tags.yaml',
)


def backup_tags(output_path: str, email: str = None) -> None:
    """Export one user's tags and rules from the DB to a YAML file."""
    db = SessionLocal()
    try:
        owner = resolve_owner(db, email)
        tags = db.query(Tag).filter_by(user_id=owner.id).order_by(Tag.name).all()
        rules = (
            db.query(TagRule)
            .filter_by(user_id=owner.id)
            .order_by(TagRule.tag_id, TagRule.priority.desc(), TagRule.id)
            .all()
        )

        # Build a tag-id → name lookup for rules
        tag_name_by_id = {t.id: t.name for t in tags}

        # ── Tags section ────────────────────────────────────────────
        tags_list = []
        for t in tags:
            tags_list.append({
                'name': t.name,
                'color': t.color,
                'icon': t.icon,
            })

        # ── Rules section ───────────────────────────────────────────
        rules_list = []
        for r in rules:
            tag_name = tag_name_by_id.get(r.tag_id)
            if not tag_name:
                logger.warning(f"Skipping rule {r.id}: tag_id {r.tag_id} not found")
                continue

            entry = {
                'tag': tag_name,
                'pattern': r.pattern,
                'match_type': r.match_type,
                'priority': r.priority,
            }
            rules_list.append(entry)

        data = {
            'tags': tags_list,
            'rules': rules_list,
        }

        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("# Tag Backup — exported from database\n")
            f.write("# Restore with: make seed\n\n")
            yaml.dump(
                data, f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

        logger.info(f"Backed up {len(tags_list)} tags and {len(rules_list)} rules to {output_path}")

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description='Backup tags and rules to YAML')
    parser.add_argument(
        '-o', '--output',
        default=DEFAULT_OUTPUT,
        help=f'Output YAML path (default: {DEFAULT_OUTPUT})',
    )
    parser.add_argument('--email', help='User whose tags to back up (default: sole/first admin)')
    args = parser.parse_args()

    try:
        backup_tags(args.output, args.email)
    except NoOwnerError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Backup complete → {args.output}")


if __name__ == '__main__':
    main()
