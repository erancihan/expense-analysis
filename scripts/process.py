#!/usr/bin/env python3
"""
Run the Gmail expense processor (poll for new bank statement emails).

Usage:
    ./scripts/process.py                 # Run with scheduler
    ./scripts/process.py --manual        # Step through emails one by one
"""

import argparse
import logging
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import schedule as schedule_lib
from src.processor import ExpenseProcessor
from src.config import CHECK_INTERVAL_MINUTES

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)-35s - %(levelname)-7s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description='Expense Analysis Application')
    parser.add_argument('--manual', action='store_true',
                        help='Manually step through emails one at a time (no scheduling)')
    args = parser.parse_args()

    logger.info("Initializing Expense Analysis Application...")

    processor = ExpenseProcessor(manual=args.manual)

    if args.manual:
        logger.info("Running in MANUAL mode. You will be prompted for each email.")
        processor.process()
    else:
        processor.process()

        schedule_lib.every(CHECK_INTERVAL_MINUTES).minutes.do(processor.process)
        logger.info(f"Scheduler started. Running every {CHECK_INTERVAL_MINUTES} minutes.")

        while True:
            schedule_lib.run_pending()
            time.sleep(1)


if __name__ == '__main__':
    main()
