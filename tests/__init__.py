"""Tests package.

Sets a fixed Fernet key for the whole suite. User.gmail_token is an
EncryptedText column, so any test that writes a token needs a usable key;
without one the write raises StatementError. unittest has no conftest.py, and
this package __init__ is imported before any test module, so this is the hook.

Individual tests that care about key handling patch
src.config.TOKEN_ENCRYPTION_KEY themselves — token_crypto reads it at call
time, so patching works.
"""

from cryptography.fernet import Fernet

from src import config

config.TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()
