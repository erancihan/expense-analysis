import logging
import importlib
import os
from sqlalchemy.orm import Session
from src.gmail_client import GmailClient
from src.database import get_db
from src.models import ProcessedEmail
from src.config import BANK_CONFIG
from src.ingest import ingest_transactions, load_fingerprints
from src.tag_engine import TagEngine
from src.users import resolve_owner
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class ExpenseProcessor:
    def __init__(self, manual=False, owner_email=None):
        self.manual = manual
        self.owner_email = owner_email
        self.gmail_client = GmailClient()
        self.parsers = {}
        self._load_parsers()

    def _load_parsers(self):
        for bank_id, config in BANK_CONFIG.items():
            module_name, class_name = config['parser_class'].rsplit('.', 1)
            try:
                module = importlib.import_module(module_name)
                parser_class = getattr(module, class_name)
                self.parsers[bank_id] = parser_class()
            except Exception as e:
                logger.error(f"Failed to load parser for {bank_id}: {e}")

    def process(self):
        logger.info("Starting processing cycle...")
        db_gen = get_db()
        db = next(db_gen)
        try:
            # All ingested data belongs to one owner (the configured Gmail
            # account's user). Resolve once per cycle.
            owner = resolve_owner(db, self.owner_email)
            logger.info(f"Processing into account: {owner.email}")
            for bank_id, config in BANK_CONFIG.items():
                self._process_bank(bank_id, config, db, owner.id)
        finally:
            next(db_gen, None) # Close the generator
        logger.info("Processing cycle finished.")

    def _process_bank(self, bank_id, config, db: Session, user_id: int):
        senders = config.get('senders', [])
        subjects = config.get('subject_keywords', [])
        
        # Build query
        # from: (a OR b) AND subject: (c OR d) AND has:attachment
        sender_query = " OR ".join([f"from:{s}" for s in senders])
        subject_query = " OR ".join([f"subject:{s}" for s in subjects])
        query = f"({sender_query}) ({subject_query}) has:attachment"
        
        logger.info(f"Querying Gmail for {bank_id}: {query}")
        messages = self.gmail_client.fetch_emails(query)
        
        for i, msg in enumerate(messages):
            message_id = msg['id']
            
            # Check if already processed (message ids are globally unique)
            existing = db.query(ProcessedEmail).filter_by(message_id=message_id).first()
            if existing and existing.status == 'SUCCESS':
                continue

            logger.info(f"Processing message {message_id} ({i+1}/{len(messages)})")

            # In manual mode, show email info first and prompt
            if self.manual:
                self._preview_message(message_id)
                action = input("\n  [Enter] Process  |  [s] Skip  |  [q] Quit > ").strip().lower()
                if action == 'q':
                    logger.info("Quitting manual mode.")
                    return
                if action == 's':
                    logger.info("Skipping message.")
                    continue

            try:
                new_expenses = self._process_message(bank_id, message_id, db, user_id)
                # Mark as success
                if not existing:
                    processed = ProcessedEmail(message_id=message_id, status='SUCCESS', user_id=user_id)
                    db.add(processed)
                else:
                    existing.status = 'SUCCESS'
                    existing.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()

                # Auto-tag the newly-inserted expenses (now that they have ids).
                if new_expenses:
                    TagEngine(db, user_id).apply_rules(new_expenses)
            except Exception as e:
                logger.error(f"Failed to process message {message_id}: {e}")
                db.rollback()
                if not existing:
                    processed = ProcessedEmail(message_id=message_id, status='FAILED', user_id=user_id)
                    db.add(processed)
                    db.commit()

    def _get_message_headers(self, message_id):
        """Fetch and return the full message and parsed headers."""
        full_msg = self.gmail_client.get_message_detail(message_id)
        if not full_msg:
            return None, {}
        headers = {h['name']: h['value'] for h in full_msg['payload'].get('headers', [])}
        return full_msg, headers

    def _preview_message(self, message_id):
        """Show email metadata without processing it."""
        full_msg, headers = self._get_message_headers(message_id)
        if not full_msg:
            logger.warning(f"  Could not fetch message {message_id}")
            return
        logger.info(f"  From:    {headers.get('From', '(unknown)')}")
        logger.info(f"  Subject: {headers.get('Subject', '(no subject)')}")
        logger.info(f"  Date:    {headers.get('Date', '(unknown)')}")

    def _process_message(self, bank_id, message_id, db: Session, user_id: int):
        """Parse a message's PDF attachments and ingest their transactions.

        Returns the list of newly-inserted (uncommitted) Expense objects so the
        caller can auto-tag them after committing.
        """
        full_msg, headers = self._get_message_headers(message_id)
        if not full_msg:
            return []

        # Log email metadata (in non-manual mode, this is the first time we see it)
        if not self.manual:
            logger.info(f"  From:    {headers.get('From', '(unknown)')}")
            logger.info(f"  Subject: {headers.get('Subject', '(no subject)')}")
            logger.info(f"  Date:    {headers.get('Date', '(unknown)')}")

        parser = self.parsers.get(bank_id)
        if not parser:
            logger.error(f"No parser found for {bank_id}")
            return []

        # Dedup across this message's attachments (and against this user's
        # existing rows) via a shared fingerprint set.
        fingerprints = load_fingerprints(db, user_id)
        new_expenses = []

        for part in full_msg['payload'].get('parts', []):
            if part['filename'] and part['filename'].endswith('.pdf'):
                # Check mime type if needed, usually application/pdf
                att_id = part['body'].get('attachmentId')
                if att_id:
                    path = self.gmail_client.save_attachment(message_id, att_id, part['filename'])
                    if path:
                        logger.info(f"Parsing {path} with {bank_id} parser")
                        transactions = parser.extract_transactions(path)

                        # Shared ingestion: dedup + statement_period stamping,
                        # identical to the batch importer's behaviour.
                        new, _ = ingest_transactions(
                            db,
                            transactions,
                            user_id=user_id,
                            bank_source=bank_id,
                            fingerprints=fingerprints,
                        )
                        new_expenses.extend(new)

                        # Cleanup
                        os.remove(path)

        return new_expenses
