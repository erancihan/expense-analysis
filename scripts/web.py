#!/usr/bin/env python3
"""
Start the Expense Analysis web server.

Usage:
    ./scripts/web.py                     # Start on 127.0.0.1:5000
    ./scripts/web.py --port 8080         # Custom port
"""

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.web import app

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)-35s - %(levelname)-7s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)


def main():
    port = 5000
    for i, arg in enumerate(sys.argv):
        if arg == '--port' and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    logger.info(f"Starting web server on http://127.0.0.1:{port}")
    app.run(host='127.0.0.1', port=port, debug=True)


if __name__ == '__main__':
    main()
