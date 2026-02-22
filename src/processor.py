import logging
import importlib
import os
from sqlalchemy.orm import Session
from src.gmail_client import GmailClient
from src.database import get_db
from src.models import Expense, ProcessedEmail
from src.config import BANK_CONFIG
from datetime import datetime

logger = logging.getLogger(__name__)

class ExpenseProcessor:
    def __init__(self, manual=False):
        self.manual = manual
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
            for bank_id, config in BANK_CONFIG.items():
                self._process_bank(bank_id, config, db)
        finally:
            next(db_gen, None) # Close the generator
        logger.info("Processing cycle finished.")

    def _process_bank(self, bank_id, config, db: Session):
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
            
            # Check if already processed
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
                self._process_message(bank_id, message_id, db)
                # Mark as success
                if not existing:
                    processed = ProcessedEmail(message_id=message_id, status='SUCCESS')
                    db.add(processed)
                else:
                    existing.status = 'SUCCESS'
                    existing.processed_at = datetime.utcnow()
                db.commit()
            except Exception as e:
                logger.error(f"Failed to process message {message_id}: {e}")
                db.rollback()
                if not existing:
                    processed = ProcessedEmail(message_id=message_id, status='FAILED')
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

    def _process_message(self, bank_id, message_id, db: Session):
        full_msg, headers = self._get_message_headers(message_id)
        if not full_msg:
            return

        # Log email metadata (in non-manual mode, this is the first time we see it)
        if not self.manual:
            logger.info(f"  From:    {headers.get('From', '(unknown)')}")
            logger.info(f"  Subject: {headers.get('Subject', '(no subject)')}")
            logger.info(f"  Date:    {headers.get('Date', '(unknown)')}")

        parser = self.parsers.get(bank_id)
        if not parser:
            logger.error(f"No parser found for {bank_id}")
            return

        for part in full_msg['payload'].get('parts', []):
            if part['filename'] and part['filename'].endswith('.pdf'):
                # Check mime type if needed, usually application/pdf
                att_id = part['body'].get('attachmentId')
                if att_id:
                    path = self.gmail_client.save_attachment(message_id, att_id, part['filename'])
                    if path:
                        logger.info(f"Parsing {path} with {bank_id} parser")
                        transactions = parser.extract_transactions(path)
                        
                        for tx in transactions:
                            expense = Expense(
                                date=tx['date'],
                                description=tx['description'],
                                amount=tx['amount'],
                                currency=tx.get('currency', 'TRY'),
                                category=tx.get('category'),
                                bank_source=bank_id,
                                raw_text=tx.get('raw_text')
                            )
                            db.add(expense)
                        
                        # Cleanup
                        os.remove(path)
