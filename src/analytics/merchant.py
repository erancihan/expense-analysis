"""Merchant names from İşbank descriptions, for grouping spend by merchant.

Descriptions carry noise that splits one merchant into many keys: reward-point
suffixes, cancellation markers, payment-processor and installment prefixes,
store numbers, a trailing "<CITY> <CC>", and — on some rows — reversed,
letter-spaced footer text that the parser appended as a continuation line.
Stored descriptions are left alone (they feed dedup fingerprints and tag
rules); this cleans a copy for analysis only.
"""

import re
from typing import Optional

_PREFIXES = (
    'TAKSİTLENDİRME/', 'TAKSITLENDIRME/', 'ARTI TAKSIT TAKSITLENDIRILMESI /',
    'PARATIKA/', 'SİPAY/', 'SIPAY/', 'IYZICO/', 'MOKAUNİTED/', 'MOKAUNITED/',
    'İADE/', 'IADE/',
)
# Six or more single characters separated by spaces, running to the end.
_SPACED_JUNK = re.compile(r'(?:\s\S){6,}\s*$')
_REWARD_SUFFIX = re.compile(r'\s*KAZANILAN\s+MAX[Iİ]?(?:PUAN|M[İI]L)\s*:.*$')
_CANCEL_SUFFIX = re.compile(r'\s+(?:[İI]ŞLEM\s+)?[İI]PTAL[Iİ]?\b.*$')
# A whole token made of digits and */#/- (store numbers, masked references).
_NUMBER_TOKEN = re.compile(r'(?<!\S)[\d*#-]*\d[\d*#-]*(?!\S)')
_LOCATION_SUFFIX = re.compile(r'\s+\S+\s+[A-Z]{2}$')
# str.upper() maps 'i' to 'I'; Turkish needs 'i' → 'İ' and 'ı' → 'I'.
_TURKISH_UPPER = str.maketrans({'i': 'İ', 'ı': 'I'})


def normalize(description: Optional[str]) -> str:
    """A grouping key for the merchant in a transaction description."""
    text = (description or '').strip()
    text = _SPACED_JUNK.sub('', text)
    text = _REWARD_SUFFIX.sub('', text)
    text = _CANCEL_SUFFIX.sub('', text)
    text = text.translate(_TURKISH_UPPER).upper()

    stripped = True
    while stripped:
        stripped = False
        for prefix in _PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):].lstrip()
                stripped = True

    text = _NUMBER_TOKEN.sub(' ', text)
    text = ' '.join(text.split())
    text = _LOCATION_SUFFIX.sub('', text)
    return text.strip(' -*/.,')
