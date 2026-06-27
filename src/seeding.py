"""
Per-user default tag/rule seeding.

Each user gets their own copy of the default tags and rules (multi-tenant: tag
names are unique per user). Seeding reads data/seeds/tags.yaml when present,
otherwise a minimal built-in fallback set. Idempotent per user.
"""

import logging
import os
from typing import Optional

import yaml
from sqlalchemy.orm import Session

from src.models import Tag, TagRule

logger = logging.getLogger(__name__)

SEEDS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'seeds'
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


def _load_yaml(path: Optional[str] = None):
    path = path or TAGS_SEED_PATH
    if not os.path.exists(path):
        logger.warning(
            f"Tags seed file not found at {path}. Using built-in fallback set. "
            f"Copy data/seeds/tags.yaml.example to data/seeds/tags.yaml to customize."
        )
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def seed_default_tags(db: Session, user_id: int, path: Optional[str] = None) -> dict:
    """Seed default tags + rules for one user. Idempotent. Caller commits.

    Returns a dict with counts: {'tags': int, 'rules': int}.
    """
    data = _load_yaml(path)

    # ── Tags (scoped to this user) ───────────────────────────────────────
    tags_map = {t.name: t for t in db.query(Tag).filter_by(user_id=user_id).all()}
    created_tags = 0

    if data and 'tags' in data:
        tag_list = data['tags']
    else:
        tag_list = [{'name': n, 'color': c, 'icon': i} for n, c, i in FALLBACK_TAGS]

    for entry in tag_list:
        name = entry['name']
        if name not in tags_map:
            tag = Tag(
                user_id=user_id,
                name=name,
                color=entry.get('color', '#6366f1'),
                icon=entry.get('icon', '🏷️'),
                is_default=True,
            )
            db.add(tag)
            db.flush()
            tags_map[name] = tag
            created_tags += 1

    # ── Rules (scoped to this user) ──────────────────────────────────────
    existing_rules = {
        (r.tag_id, r.pattern)
        for r in db.query(TagRule).filter_by(user_id=user_id).all()
    }
    created_rules = 0

    if data and 'rules' in data:
        for entry in data['rules']:
            tag = tags_map.get(entry['tag'])
            if not tag:
                logger.warning(
                    f"Tag '{entry['tag']}' not found, skipping rule for '{entry['pattern']}'"
                )
                continue
            if (tag.id, entry['pattern']) not in existing_rules:
                db.add(TagRule(
                    user_id=user_id,
                    tag_id=tag.id,
                    pattern=entry['pattern'],
                    match_type=entry.get('match_type', 'contains'),
                    priority=entry.get('priority', 5),
                    is_default=True,
                ))
                created_rules += 1

    db.commit()
    logger.info(f"Seeded {created_tags} tags and {created_rules} rules for user {user_id}")
    return {'tags': created_tags, 'rules': created_rules}
