#!/usr/bin/env python3
"""
Seed default tags and rules into the database.

Reads from data/seeds/tags.yaml and inserts any tags/rules that
don't already exist. Safe to run multiple times (idempotent).

Falls back to a minimal built-in tag set if no YAML file is found.

Usage:
    ./scripts/seed.py
    make seed
"""

import logging
import sys
import os

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import SessionLocal
from src.models import Tag, TagRule

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)-35s - %(levelname)-7s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

SEEDS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'seeds'
)
TAGS_SEED_PATH = os.path.join(SEEDS_DIR, 'tags.yaml')

# Minimal fallback tags (no merchant-specific rules)
FALLBACK_TAGS = [
    ('restaurant',    '#ef4444', '🍽️'),
    ('grocery',       '#22c55e', '🛒'),
    ('transport',     '#3b82f6', '🚕'),
    ('gas',           '#f97316', '⛽'),
    ('entertainment', '#a855f7', '🎮'),
    ('subscription',  '#6366f1', '💻'),
    ('shopping',      '#ec4899', '🛍️'),
    ('food_delivery', '#f59e0b', '🍕'),
    ('payment',       '#10b981', '💳'),
    ('bills',         '#64748b', '🏛️'),
    ('transit',       '#0ea5e9', '🚇'),
    ('cafe',          '#78716c', '☕'),
    ('health',        '#14b8a6', '🏥'),
    ('education',     '#8b5cf6', '📚'),
]


def _load_yaml():
    """Load tags and rules from the YAML seed file."""
    if not os.path.exists(TAGS_SEED_PATH):
        logger.warning(
            f"Tags seed file not found at {TAGS_SEED_PATH}. "
            f"Copy data/seeds/tags.yaml.example to data/seeds/tags.yaml and customize."
        )
        return None

    with open(TAGS_SEED_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def seed_defaults(db):
    """
    Seed default tags and rules into the database.

    Reads from data/seeds/tags.yaml if available, otherwise uses
    a minimal fallback tag set (no rules).

    Only inserts tags/rules that don't already exist (idempotent).
    """
    data = _load_yaml()

    # ── Seed tags ────────────────────────────────────────────────────────
    existing_tags = {t.name: t for t in db.query(Tag).all()}
    tags_map = dict(existing_tags)
    created_tags = 0

    if data and 'tags' in data:
        tag_list = data['tags']
    else:
        tag_list = [
            {'name': n, 'color': c, 'icon': i}
            for n, c, i in FALLBACK_TAGS
        ]

    for entry in tag_list:
        name = entry['name']
        if name not in tags_map:
            tag = Tag(
                name=name,
                color=entry.get('color', '#6366f1'),
                icon=entry.get('icon', '🏷️'),
                is_default=True,
            )
            db.add(tag)
            db.flush()
            tags_map[name] = tag
            created_tags += 1

    # ── Seed rules ───────────────────────────────────────────────────────
    existing_rules = set()
    for r in db.query(TagRule).all():
        existing_rules.add((r.tag_id, r.pattern))

    created_rules = 0

    if data and 'rules' in data:
        for entry in data['rules']:
            tag_name = entry['tag']
            tag = tags_map.get(tag_name)
            if not tag:
                logger.warning(f"Tag '{tag_name}' not found, skipping rule for '{entry['pattern']}'")
                continue

            if (tag.id, entry['pattern']) not in existing_rules:
                rule = TagRule(
                    tag_id=tag.id,
                    pattern=entry['pattern'],
                    match_type=entry.get('match_type', 'contains'),
                    priority=entry.get('priority', 5),
                    is_default=True,
                )
                db.add(rule)
                created_rules += 1

    db.commit()
    logger.info(f"Seeded {created_tags} tags and {created_rules} rules")


def main():
    db = SessionLocal()
    try:
        seed_defaults(db)
        print("Seeding complete.")
    finally:
        db.close()


if __name__ == '__main__':
    main()
