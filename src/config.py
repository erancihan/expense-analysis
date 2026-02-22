import os
import yaml
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Defaults (tracked in git)
# -------------------------------------------------------------------

# Database
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'expenses.db')
DATABASE_URL = os.getenv('DATABASE_URL', f'sqlite:///{DB_PATH}')

# Gmail
GMAIL_CREDENTIALS_PATH = os.getenv('GMAIL_CREDENTIALS_PATH', 'secrets/credentials.json')
GMAIL_TOKEN_PATH = os.getenv('GMAIL_TOKEN_PATH', 'secrets/token.json')

# Application
CHECK_INTERVAL_MINUTES = int(os.getenv('CHECK_INTERVAL_MINUTES', 60))

# Bank Configuration
BANK_CONFIG = {
    'isbank': {
        'senders': ['bilgilendirme@ileti.isbank.com.tr'],
        'subject_keywords': ['Statement', 'Ekstre', 'Kredi Kartı Hesap Özeti'],
        'parser_class': 'src.parsers.isbank.IsbankParser'
    }
}

# -------------------------------------------------------------------
# Override from local config file (not tracked)
# -------------------------------------------------------------------
LOCAL_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'config.local.yaml'
)

def _load_local_config():
    global DATABASE_URL, GMAIL_CREDENTIALS_PATH, GMAIL_TOKEN_PATH
    global CHECK_INTERVAL_MINUTES, BANK_CONFIG

    if not os.path.exists(LOCAL_CONFIG_PATH):
        return

    logger.info(f"Loading local config from {LOCAL_CONFIG_PATH}")
    with open(LOCAL_CONFIG_PATH, 'r') as f:
        local = yaml.safe_load(f) or {}

    if 'database_url' in local:
        DATABASE_URL = local['database_url']
    if 'gmail_credentials_path' in local:
        GMAIL_CREDENTIALS_PATH = local['gmail_credentials_path']
    if 'gmail_token_path' in local:
        GMAIL_TOKEN_PATH = local['gmail_token_path']
    if 'check_interval_minutes' in local:
        CHECK_INTERVAL_MINUTES = int(local['check_interval_minutes'])
    if 'bank_config' in local:
        BANK_CONFIG = local['bank_config']

_load_local_config()