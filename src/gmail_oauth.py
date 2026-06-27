"""
Web OAuth flow for connecting a user's Gmail (read-only).

Requires a **Web application** OAuth client in Google Cloud whose secrets are at
`GMAIL_CREDENTIALS_PATH` (default `secrets/credentials.json`), with the app's
`/gmail/callback` URL registered as an authorized redirect URI. The desktop
("installed app") flow used by the CLI does NOT work for a hosted server.
"""

import logging
import os

from google_auth_oauthlib.flow import Flow

from src.config import GMAIL_CREDENTIALS_PATH
from src.gmail_client import SCOPES

logger = logging.getLogger(__name__)


def credentials_available() -> bool:
    """Whether an OAuth client secrets file is present (gate the UI on this)."""
    return os.path.exists(GMAIL_CREDENTIALS_PATH)


def build_flow(redirect_uri: str, state: str = None) -> Flow:
    """Build a web OAuth Flow from the configured client secrets."""
    return Flow.from_client_secrets_file(
        GMAIL_CREDENTIALS_PATH,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        state=state,
    )
