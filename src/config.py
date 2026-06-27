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

# Web / auth
_DEV_SECRET = 'dev-insecure-secret-change-me'
SECRET_KEY = os.getenv('SECRET_KEY', _DEV_SECRET)
# Set true behind HTTPS so the session cookie is only sent over TLS.
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'false').lower() in ('1', 'true', 'yes', 'on')
# No public sign-up by default — users are created by an admin (scripts/create_user.py).
ALLOW_REGISTRATION = os.getenv('ALLOW_REGISTRATION', 'false').lower() in ('1', 'true', 'yes', 'on')
# Number of trusted reverse proxies in front of the app (enables ProxyFix so the
# real client IP / scheme are read from X-Forwarded-* headers). 0 = direct.
TRUSTED_PROXIES = int(os.getenv('TRUSTED_PROXIES', 0))
# Rate limit applied to the login endpoint (brute-force defense).
LOGIN_RATELIMIT = os.getenv('LOGIN_RATELIMIT', '10 per minute')

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
    global SECRET_KEY, SESSION_COOKIE_SECURE, ALLOW_REGISTRATION
    global TRUSTED_PROXIES, LOGIN_RATELIMIT

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
    if 'secret_key' in local:
        SECRET_KEY = local['secret_key']
    if 'session_cookie_secure' in local:
        SESSION_COOKIE_SECURE = bool(local['session_cookie_secure'])
    if 'allow_registration' in local:
        ALLOW_REGISTRATION = bool(local['allow_registration'])
    if 'trusted_proxies' in local:
        TRUSTED_PROXIES = int(local['trusted_proxies'])
    if 'login_ratelimit' in local:
        LOGIN_RATELIMIT = local['login_ratelimit']

_load_local_config()

if SECRET_KEY == _DEV_SECRET:
    logger.warning(
        "SECRET_KEY is the insecure development default. Set SECRET_KEY (env or "
        "config.local.yaml) before deploying — sessions are forgeable otherwise."
    )