#!/usr/bin/env python3
"""
Add a tag and optionally tag rules to the database.

Usage:
    # Add a tag only
    ./scripts/add_tag.py restaurant --color '#ef4444' --icon '🍽️'

    # Add a tag with rules
    ./scripts/add_tag.py cafe --color '#78716c' --icon '☕' \\
        --rule STARBUCKS \\
        --rule KRONOTROP \\
        --rule "KAHVE"

    # Add rules to an existing tag
    ./scripts/add_tag.py grocery --rule "CARREFOUR" --rule "A101"

    # Use starts_with matching
    ./scripts/add_tag.py transport --rule "UBER" --match-type starts_with

    # Set priority (default: 100 for user rules)
    ./scripts/add_tag.py shopping --rule "TRENDYOL" --priority 150
"""

import argparse
import logging
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import init_db, SessionLocal
from src.models import Tag, TagRule

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)-35s - %(levelname)-7s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='Add a tag and optionally tag rules.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('name', help='Tag name (lowercase, e.g. restaurant)')
    parser.add_argument('--color', default='#6366f1', help='Hex color for the UI (default: #6366f1)')
    parser.add_argument('--icon', default='🏷️', help='Emoji icon (default: 🏷️)')
    parser.add_argument('--rule', action='append', dest='rules', default=[],
                        help='Pattern to match in expense descriptions. Can be specified multiple times.')
    parser.add_argument('--match-type', default='contains',
                        choices=['contains', 'starts_with', 'regex'],
                        help='How to match patterns (default: contains)')
    parser.add_argument('--priority', type=int, default=100,
                        help='Rule priority, higher = checked first (default: 100)')
    args = parser.parse_args()

    name = args.name.strip().lower()
    if not name:
        print("Error: tag name cannot be empty", file=sys.stderr)
        sys.exit(1)

    init_db()
    db = SessionLocal()

    try:
        # Create or get tag
        tag = db.query(Tag).filter_by(name=name).first()
        if tag:
            print(f"Tag '{name}' already exists (id={tag.id})")
            # Update color/icon if provided and different
            updated = False
            if args.color != '#6366f1' and tag.color != args.color:
                tag.color = args.color
                updated = True
            if args.icon != '🏷️' and tag.icon != args.icon:
                tag.icon = args.icon
                updated = True
            if updated:
                db.commit()
                print(f"  Updated: color={tag.color}, icon={tag.icon}")
        else:
            tag = Tag(
                name=name,
                color=args.color,
                icon=args.icon,
                is_default=False,
            )
            db.add(tag)
            db.flush()
            print(f"Created tag '{name}' (id={tag.id}, color={args.color}, icon={args.icon})")

        # Add rules
        if args.rules:
            existing_rules = {
                r.pattern
                for r in db.query(TagRule).filter_by(tag_id=tag.id).all()
            }

            added = 0
            for pattern in args.rules:
                pattern = pattern.strip()
                if not pattern:
                    continue
                if pattern in existing_rules:
                    print(f"  Rule '{pattern}' already exists, skipping")
                    continue

                rule = TagRule(
                    tag_id=tag.id,
                    pattern=pattern,
                    match_type=args.match_type,
                    priority=args.priority,
                    is_default=False,
                )
                db.add(rule)
                added += 1
                print(f"  Added rule: '{pattern}' ({args.match_type}, priority={args.priority})")

            if added:
                db.commit()
                print(f"\nAdded {added} rule(s) to '{name}'")
                print("Run `make retag` to apply new rules to existing expenses.")
        else:
            db.commit()

    finally:
        db.close()


if __name__ == '__main__':
    main()
