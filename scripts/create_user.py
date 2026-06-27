#!/usr/bin/env python3
"""
Create a user account (or reset an existing user's password).

There is no public sign-up; this is how an administrator provisions accounts.

Usage:
    ./scripts/create_user.py you@example.com --admin            # prompts for password
    ./scripts/create_user.py you@example.com --password 's3cret'
    make create-user EMAIL=you@example.com ADMIN=1
"""

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash

from src.database import SessionLocal
from src.models import User
from src.seeding import seed_default_tags


def main():
    parser = argparse.ArgumentParser(description="Create or update a user account.")
    parser.add_argument('email')
    parser.add_argument('--password', help='Password (prompted securely if omitted)')
    parser.add_argument('--admin', action='store_true', help='Grant admin rights')
    args = parser.parse_args()

    email = args.email.strip().lower()
    password = args.password or getpass.getpass('Password: ')
    if not password:
        print('Error: password cannot be empty', file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(email=email).first()
        if user:
            user.password_hash = generate_password_hash(password)
            if args.admin:
                user.is_admin = True
            db.commit()
            print(f"Updated user {email}" + (' (admin)' if args.admin else ''))
            return

        user = User(
            email=email,
            password_hash=generate_password_hash(password),
            is_admin=args.admin,
            is_active=True,
        )
        db.add(user)
        db.commit()
        # Give the new account its own copy of the default tags + rules.
        counts = seed_default_tags(db, user.id)
        print(f"Created user {email}" + (' (admin)' if args.admin else '')
              + f"; seeded {counts['tags']} tags, {counts['rules']} rules")
    finally:
        db.close()


if __name__ == '__main__':
    main()
