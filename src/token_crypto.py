"""Encryption for the per-user Gmail OAuth token stored in the database.

The stored token contains a refresh token, so it is encrypted at rest with
Fernet under a dedicated key, `token_encryption_key`. The key is deliberately
separate from `secret_key`: rotating the session secret must not destroy every
stored Gmail token.

The key is read at call time rather than bound at import, because
`src.config._load_local_config()` mutates module globals after import and tests
need to swap it.
"""

import logging

from cryptography.fernet import Fernet, InvalidToken

from src import config

logger = logging.getLogger(__name__)


class TokenEncryptionError(Exception):
    """Raised when a token cannot be encrypted or decrypted."""


def _fernet() -> Fernet:
    key = config.TOKEN_ENCRYPTION_KEY
    if not key:
        raise TokenEncryptionError(
            'token_encryption_key is not configured; Gmail tokens cannot be '
            'encrypted or decrypted'
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as e:
        raise TokenEncryptionError(
            f'token_encryption_key is not a valid Fernet key: {e}'
        ) from e


def encryption_available() -> bool:
    """True when a usable Fernet key is configured."""
    try:
        _fernet()
    except TokenEncryptionError:
        return False
    return True


def encrypt(plaintext: str) -> str:
    """Encrypt a token. Raises TokenEncryptionError when no usable key is set."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a token.

    Raises TokenEncryptionError on a missing key, the wrong key, or data that is
    not Fernet ciphertext. Plaintext is deliberately rejected rather than passed
    through: there is no legacy plaintext to tolerate, so accepting it would only
    mask corruption or tampering.
    """
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise TokenEncryptionError(
            'stored token could not be decrypted (wrong key or corrupt data)'
        ) from e
