"""
Tagging engine for expense categorization.

Applies tag rules (pattern matching on expense descriptions) to automatically
categorize expenses. Supports both auto-tagging and manual overrides.
"""

import re
import logging
from typing import List, Optional
from sqlalchemy.orm import Session
from src.models import Expense, Tag, TagRule, ExpenseTag

logger = logging.getLogger(__name__)


class TagEngine:
    """
    Applies tag rules to expenses based on description pattern matching.

    Rules are ordered by priority (highest first). Each rule matches against
    the expense description using contains, starts_with, or regex matching.
    """

    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id
        self._rules: Optional[List[TagRule]] = None

    @property
    def rules(self) -> List[TagRule]:
        """Lazily load this user's rules sorted by priority (highest first)."""
        if self._rules is None:
            self._rules = (
                self.db.query(TagRule)
                .filter_by(user_id=self.user_id)
                .order_by(TagRule.priority.desc(), TagRule.id.asc())
                .all()
            )
        return self._rules

    def invalidate_cache(self):
        """Clear the cached rules (call after rules are added/modified)."""
        self._rules = None

    def match_expense(self, expense: Expense) -> List[TagRule]:
        """
        Find all rules that match an expense's description.

        Returns list of matching TagRule objects (one per unique tag_id,
        highest priority wins if multiple rules match the same tag).
        """
        desc_upper = expense.description.upper()
        matched_tag_ids = set()
        matches = []
        has_user_rule = False

        for rule in self.rules:
            if rule.tag_id in matched_tag_ids:
                continue  # already matched a higher-priority rule for this tag

            if self._rule_matches(rule, desc_upper):
                matches.append(rule)
                matched_tag_ids.add(rule.tag_id)
                if not rule.is_default:
                    has_user_rule = True

        # If there's any user-added rule, it overrides default rules
        if has_user_rule:
            matches = [m for m in matches if not m.is_default]

        return matches

    def get_all_matching_rules(self, expense: Expense) -> List[TagRule]:
        """
        Find all rules that match an expense's description, without applying
        the user-rule override logic. This is useful for displaying why a tag
        was originally applied (e.g. for suppressed tags) even if it's currently
        shadowed by a user rule.
        """
        desc_upper = expense.description.upper()
        matched_tag_ids = set()
        matches = []

        for rule in self.rules:
            if rule.tag_id in matched_tag_ids:
                continue

            if self._rule_matches(rule, desc_upper):
                matches.append(rule)
                matched_tag_ids.add(rule.tag_id)

        return matches

    def _rule_matches(self, rule: TagRule, desc_upper: str) -> bool:
        """Check if a single rule matches a description (already uppercased)."""
        pattern = rule.pattern.upper()

        if rule.match_type == 'contains':
            return pattern in desc_upper
        elif rule.match_type == 'starts_with':
            return desc_upper.startswith(pattern)
        elif rule.match_type == 'regex':
            try:
                return bool(re.search(rule.pattern, desc_upper, re.IGNORECASE))
            except re.error:
                logger.warning(f"Invalid regex in rule {rule.id}: {rule.pattern}")
                return False
        else:
            logger.warning(f"Unknown match_type '{rule.match_type}' in rule {rule.id}")
            return False

    def apply_rules(self, expenses: List[Expense]) -> int:
        """
        Apply tag rules to a list of expenses.

        Only adds auto-tags. Skips expenses that already have a manual tag
        for a given tag_id. Returns count of new tags applied.
        """
        if not expenses:
            return 0

        applied = 0

        for expense in expenses:
            # Get existing tags for this expense
            existing = {
                et.tag_id: et.source
                for et in self.db.query(ExpenseTag).filter_by(expense_id=expense.id).all()
            }

            matches = self.match_expense(expense)
            for rule in matches:
                if rule.tag_id in existing:
                    continue  # already tagged (auto or manual)

                et = ExpenseTag(
                    expense_id=expense.id,
                    tag_id=rule.tag_id,
                    source='auto',
                )
                self.db.add(et)
                applied += 1

        self.db.commit()
        return applied

    def retag_all(self) -> int:
        """
        Clear this user's auto-tags and re-apply rules to their expenses.

        Manual tags are preserved. Returns count of new tags applied.
        """
        # This user's expense ids.
        expense_ids = [
            e.id for e in self.db.query(Expense.id).filter_by(user_id=self.user_id).all()
        ]

        # Delete auto-assigned tags on this user's expenses only.
        deleted = 0
        if expense_ids:
            deleted = (
                self.db.query(ExpenseTag)
                .filter(ExpenseTag.source == 'auto')
                .filter(ExpenseTag.expense_id.in_(expense_ids))
                .delete(synchronize_session='fetch')
            )
        self.db.commit()
        logger.info(f"Cleared {deleted} auto-tags")

        # Re-apply rules to this user's expenses.
        self.invalidate_cache()
        expenses = self.db.query(Expense).filter_by(user_id=self.user_id).all()
        applied = self.apply_rules(expenses)
        logger.info(f"Applied {applied} auto-tags to {len(expenses)} expenses")
        return applied

    def get_stats(self) -> dict:
        """Return tagging statistics for this user."""
        from sqlalchemy import func

        expense_ids = [
            e.id for e in self.db.query(Expense.id).filter_by(user_id=self.user_id).all()
        ]
        total_expenses = len(expense_ids)
        tagged = 0
        tag_counts = []
        if expense_ids:
            tagged = (
                self.db.query(ExpenseTag.expense_id)
                .filter(ExpenseTag.expense_id.in_(expense_ids))
                .filter(ExpenseTag.source != 'suppressed')
                .distinct()
                .count()
            )
            tag_counts = (
                self.db.query(Tag.name, func.count(ExpenseTag.id))
                .join(ExpenseTag, Tag.id == ExpenseTag.tag_id)
                .filter(ExpenseTag.expense_id.in_(expense_ids))
                .filter(ExpenseTag.source != 'suppressed')
                .group_by(Tag.name)
                .order_by(func.count(ExpenseTag.id).desc())
                .all()
            )
        untagged = total_expenses - tagged

        return {
            'total_expenses': total_expenses,
            'tagged': tagged,
            'untagged': untagged,
            'coverage_pct': round(tagged / total_expenses * 100, 1) if total_expenses else 0,
            'by_tag': {name: count for name, count in tag_counts},
        }

