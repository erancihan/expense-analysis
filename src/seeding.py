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

    # Pass 1: create every tag without parents, so a child may be declared
    # before its parent in the file.
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

    # Pass 2: link parents, rejecting unknown parents and depth > 2. Depth is
    # capped so every rollup stays a single join and the chart's drill-down is
    # exactly two levels.
    declared_parent = {e['name']: e.get('parent') for e in tag_list}
    for entry in tag_list:
        parent_name = entry.get('parent')
        if not parent_name:
            continue
        if parent_name not in tags_map:
            raise ValueError(
                f"Tag '{entry['name']}' names parent '{parent_name}', "
                f"which is not defined in the seed file."
            )
        if declared_parent.get(parent_name):
            raise ValueError(
                f"Tag '{entry['name']}' would be at depth 3: its parent "
                f"'{parent_name}' already has a parent. Only two levels are "
                f"supported."
            )
        tags_map[entry['name']].parent_id = tags_map[parent_name].id

    # ── Rules (scoped to this user) ──────────────────────────────────────
    # Reconciled, not merely inserted: the seed file is the source of truth for
    # default rules, so a rule edited or deleted there must take effect on an
    # already-seeded user. Insert-only seeding made the file misleading — an
    # edited priority or match_type was ignored, and a deleted rule stayed live
    # alongside its replacement, so both fired.
    #
    # Only is_default rules are touched. Rules the user added through the UI
    # carry is_default=False (src/web.py) and are never updated or removed.
    created_rules = updated_rules = removed_rules = 0

    desired = {}
    if data and 'rules' in data:
        for entry in data['rules']:
            tag = tags_map.get(entry['tag'])
            if not tag:
                logger.warning(
                    f"Tag '{entry['tag']}' not found, skipping rule for '{entry['pattern']}'"
                )
                continue
            desired[(tag.id, entry['pattern'])] = (
                entry.get('match_type', 'contains'),
                entry.get('priority', 5),
            )

    existing_defaults = {}
    for rule in db.query(TagRule).filter_by(user_id=user_id, is_default=True).all():
        existing_defaults[(rule.tag_id, rule.pattern)] = rule

    # Drop default rules the seed file no longer declares.
    for key, rule in existing_defaults.items():
        if key not in desired:
            db.delete(rule)
            removed_rules += 1

    # Update match_type / priority where the seed file has changed.
    for key, (match_type, priority) in desired.items():
        rule = existing_defaults.get(key)
        if rule is None:
            continue
        if rule.match_type != match_type or rule.priority != priority:
            rule.match_type = match_type
            rule.priority = priority
            updated_rules += 1

    # A user-created rule can shadow a default one with the same tag+pattern;
    # don't insert a duplicate default alongside it.
    user_owned = {
        (r.tag_id, r.pattern)
        for r in db.query(TagRule).filter_by(user_id=user_id, is_default=False).all()
    }

    for (tag_id, pattern), (match_type, priority) in desired.items():
        if (tag_id, pattern) in existing_defaults or (tag_id, pattern) in user_owned:
            continue
        db.add(TagRule(
            user_id=user_id,
            tag_id=tag_id,
            pattern=pattern,
            match_type=match_type,
            priority=priority,
            is_default=True,
        ))
        created_rules += 1

    db.commit()
    logger.info(
        f"Seeded {created_tags} tags for user {user_id}; rules "
        f"+{created_rules} ~{updated_rules} -{removed_rules}"
    )
    return {
        'tags': created_tags,
        'rules': created_rules,
        'updated': updated_rules,
        'removed': removed_rules,
    }
