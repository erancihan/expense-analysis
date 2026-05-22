#!/usr/bin/env python3
"""
Download all PDF attachments from İşbank emails to data/pdfs/.

Usage:
    ./scripts/download_pdfs.py
    make download-pdfs
"""

import os
import sys
import logging
from email.utils import parsedate_to_datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.gmail_client import GmailClient
from src.config import BANK_CONFIG

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)-35s - %(levelname)-7s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

SAVE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'pdfs')


def main():
    os.makedirs(SAVE_DIR, exist_ok=True)

    client = GmailClient()

    for bank_id, config in BANK_CONFIG.items():
        senders = config.get('senders', [])
        subjects = config.get('subject_keywords', [])

        sender_query = " OR ".join([f"from:{s}" for s in senders])
        subject_query = " OR ".join([f"subject:{s}" for s in subjects])
        query = f"({sender_query}) ({subject_query}) has:attachment"

        logger.info(f"Querying Gmail for {bank_id}: {query}")
        messages = client.fetch_emails(query)
        logger.info(f"Found {len(messages)} messages for {bank_id}")

        for i, msg in enumerate(messages):
            message_id = msg['id']
            full_msg = client.get_message_detail(message_id)
            if not full_msg:
                continue

            headers = {h['name']: h['value'] for h in full_msg['payload'].get('headers', [])}
            subject = headers.get('Subject', 'no-subject')
            date_header = headers.get('Date', '')
            try:
                dt = parsedate_to_datetime(date_header)
                date_str = dt.strftime('%Y-%m-%d')
            except (TypeError, ValueError):
                date_str = '0000-00-00'

            logger.info(f"[{i+1}/{len(messages)}] {date_str} — {subject}")

            parts = full_msg['payload'].get('parts', [])
            for part in parts:
                filename = part.get('filename', '')
                if filename and filename.lower().endswith('.pdf'):
                    att_id = part['body'].get('attachmentId')
                    if att_id:
                        safe_filename = f"{bank_id}_{date_str}_{i+1:03d}_{filename}"
                        dest = os.path.join(SAVE_DIR, safe_filename)
                        if os.path.exists(dest):
                            logger.info(f"  Already downloaded: {safe_filename}")
                            continue
                        path = client.save_attachment(message_id, att_id, safe_filename, save_dir=SAVE_DIR)
                        if path:
                            logger.info(f"  Saved: {path}")
                        else:
                            logger.warning(f"  Failed to download attachment from message {message_id}")


if __name__ == '__main__':
    main()
