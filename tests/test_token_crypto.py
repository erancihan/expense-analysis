import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from src.token_crypto import (
    TokenEncryptionError,
    decrypt,
    encrypt,
    encryption_available,
)

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()
TOKEN = '{"refresh_token": "r", "token": "t"}'


class TokenCryptoTest(unittest.TestCase):
    @patch('src.config.TOKEN_ENCRYPTION_KEY', KEY_A)
    def test_roundtrip(self):
        self.assertEqual(decrypt(encrypt(TOKEN)), TOKEN)

    @patch('src.config.TOKEN_ENCRYPTION_KEY', KEY_A)
    def test_ciphertext_hides_the_plaintext(self):
        ciphertext = encrypt(TOKEN)
        self.assertNotEqual(ciphertext, TOKEN)
        self.assertNotIn('refresh_token', ciphertext)

    @patch('src.config.TOKEN_ENCRYPTION_KEY', KEY_A)
    def test_encryption_is_non_deterministic(self):
        self.assertNotEqual(encrypt(TOKEN), encrypt(TOKEN))

    @patch('src.config.TOKEN_ENCRYPTION_KEY', KEY_A)
    def test_available_with_a_good_key(self):
        self.assertTrue(encryption_available())

    def test_wrong_key_raises(self):
        with patch('src.config.TOKEN_ENCRYPTION_KEY', KEY_A):
            ciphertext = encrypt(TOKEN)
        with patch('src.config.TOKEN_ENCRYPTION_KEY', KEY_B):
            with self.assertRaises(TokenEncryptionError):
                decrypt(ciphertext)

    @patch('src.config.TOKEN_ENCRYPTION_KEY', KEY_A)
    def test_plaintext_input_is_rejected(self):
        with self.assertRaises(TokenEncryptionError):
            decrypt(TOKEN)

    @patch('src.config.TOKEN_ENCRYPTION_KEY', '')
    def test_no_key_means_unavailable(self):
        self.assertFalse(encryption_available())

    @patch('src.config.TOKEN_ENCRYPTION_KEY', '')
    def test_no_key_encrypt_raises(self):
        with self.assertRaises(TokenEncryptionError):
            encrypt(TOKEN)

    @patch('src.config.TOKEN_ENCRYPTION_KEY', 'not-a-valid-fernet-key')
    def test_malformed_key_is_unavailable(self):
        self.assertFalse(encryption_available())
