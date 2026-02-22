import argparse
import logging
import time
import schedule
from src.database import init_db
from src.processor import ExpenseProcessor
from src.config import CHECK_INTERVAL_MINUTES
import sys

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)-35s - %(levelname)-7s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description='Expense Analysis Application')
    parser.add_argument('--manual', action='store_true',
                        help='Manually step through emails one at a time (no scheduling)')
    args = parser.parse_args()

    logger.info("Initializing Expense Analysis Application...")
    init_db()

    processor = ExpenseProcessor(manual=args.manual)

    if args.manual:
        logger.info("Running in MANUAL mode. You will be prompted for each email.")
        processor.process()
    else:
        processor.process()

        schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(processor.process)
        logger.info(f"Scheduler started. Running every {CHECK_INTERVAL_MINUTES} minutes.")

        while True:
            schedule.run_pending()
            time.sleep(1)

if __name__ == "__main__":
    main()
