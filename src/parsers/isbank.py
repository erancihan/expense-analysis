"""
Parser for İşbank credit card statements (PDF → HTML → BeautifulSoup).

All İşbank credit card statements share the same PDF layout regardless of card
type (VISA Klasik, TROY KLASİK, MAXIMUM VİSA, Maximiles, etc.).

The PDF is converted to HTML via pdfminer. The resulting HTML uses absolutely-
positioned <div> elements. The key columns are:

    left ~37-100px   →  İŞLEM TARİHİ / AÇIKLAMA  (date + description)
    left ~370-430px  →  TUTAR  (amount)
    left ~430-450px  →  TAKSİT BİLGİSİ  (installment info)
    left ~549px      →  MAXIPUAN  (reward points)

Card metadata (card number, card type, statement currency) is extracted from
the header section of each PDF.

Usage:
    python -m src.parsers.isbank <pdf_path> [pdf_path ...]
    python -m src.parsers.isbank --dump-html <pdf_path>
"""

from .base import BankParser
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from io import BytesIO
import re
import logging

from pdfminer.high_level import extract_text_to_fp
from pdfminer.layout import LAParams
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Column boundaries (horizontal position in px) ────────────────────────────
# These vary slightly across format eras (2019-2026) so we use wide ranges.
DESC_LEFT_MIN = 30.0
DESC_LEFT_MAX = 140.0
AMOUNT_LEFT_MIN = 330.0
AMOUNT_LEFT_MAX = 430.0

# ── Patterns ─────────────────────────────────────────────────────────────────
# Date at start of line. Older format concatenates date+desc without space.
DATE_PATTERN = re.compile(r'^(\d{2}/\d{2}/\d{4})\s*(.*)')

# Amount: optional sign (with optional space after), digits with thousand-
# separator dots, comma for decimal.  e.g. "1.234,56", "- 65.195,00"
# Also handles dot-decimal used in some USD statements: "0.00", "579.09"
AMOUNT_PATTERN = re.compile(r'^[+-]?\s*[\d.]+,\d{2}$|^[+-]?\s*\d+\.\d{2}$')

# Card number in header: 4543********6152
CARD_NUMBER_PATTERN = re.compile(r'(\d{4}\*{4,8}\d{4})')

# Card type from "<TYPE> Hesap Özetiniz"
CARD_TYPE_PATTERN = re.compile(r'^(.+?)\s+Hesap Özetiniz$')

# ── Section markers ──────────────────────────────────────────────────────────
PREV_BALANCE_MARKER = 'ÖNCEKİ HESAP ÖZETİ BAKİYENİZ'
PAYMENT_THANKS_MARKER = 'ÖDEMELERINIZ IÇIN TESEKKÜR EDERIZ'
TOTAL_MARKER = 'TOPLAM'
PAYMENT_MARKER = 'HESAPTAN AKTARIM'

# Lines to skip entirely (substring match)
SKIP_SUBSTRINGS = [
    'Müşteri Limiti', 'Kullanıla', 'Kredi Kartınız',
    'isbank.com', 'maximum.com', 'Aylık', 'Yıllık',
    'MESAJINIZ', 'Bu bildirim', 'TAKSİTLİ BORÇ',
    'Sayfa', 'Hesap Özetiniz', 'Toplam Kart',
    'Asgari Ödeme', 'Nakit Çekme Limiti',
    'Alışveriş Faiz', 'Nakit Çekme Faiz', 'Nakit Gecikme',
    'Alışveriş Gecikme', 'Zarf ', 'İNDİRİM NEDENİYLE İPTAL',
    'MAXİPUAN İLAVE', 'MAXİPUAN KULLANIMI', 'MAXİMİL KULLANIMI', 'MAXİMİL İLAVE'
]

# Keywords that should NOT be appended as continuation text
CONTINUATION_STOP_KEYWORDS = [
    'Sözleşme', 'itibari', 'kartlar', 'MaxiPuan',
    'kampanya', 'www.', 'İşCep', 'uygulaması',
    'ÜRÜN', 'ASIL KART', 'KREDİ KARTI SÖZLEŞMESİ',
    'Belirtilen ücretler', 'Ba skı',
]


def _parse_style(style: str) -> Tuple[float, float]:
    """Extract (left, top) position from an inline CSS style attribute."""
    left_m = re.search(r'left:([\d.]+)px', style)
    top_m = re.search(r'top:([\d.]+)px', style)
    left = float(left_m.group(1)) if left_m else 0.0
    top = float(top_m.group(1)) if top_m else 0.0
    return left, top


def _normalise_amount(s: str) -> Optional[float]:
    """
    Normalise a Turkish-formatted number string to a float.
    Handles both comma-decimal (1.234,56) and dot-decimal (0.00) formats.
    """
    s = s.strip()
    negative = s.startswith('-')
    s = s.lstrip('+-').strip()

    if ',' in s:
        # Turkish format: dots are thousand separators, comma is decimal
        s = s.replace('.', '').replace(',', '.')
    # else: standard dot-decimal format, no transformation needed

    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None


def _extract_amount_values(text: str) -> List[float]:
    """Extract all amount values from a text block, skipping non-amount lines.
    
    Lines with a trailing currency label (e.g. '22.926,40 TL') are summary/
    balance amounts and are intentionally skipped — only raw numeric amounts
    are transaction values.
    """
    values = []
    for line in text.split('\n'):
        line = line.strip()
        if not line or line == 'TUTAR':
            continue
        # Skip lines that end with a currency label — these are summary
        # amounts (Hesap Özeti Borcu, etc.), not transaction amounts
        if re.search(r'\s+(TL|USD|EUR|GBP)\s*$', line):
            continue
        if AMOUNT_PATTERN.match(line):
            val = _normalise_amount(line)
            if val is not None:
                values.append(val)
    return values


def _extract_card_metadata(all_text: str) -> Dict[str, str]:
    """
    Extract card number and card type from the full text of a statement.

    Returns dict with keys: card_number, card_type, statement_currency,
    statement_period
    """
    meta: Dict[str, str] = {
        'card_number': '',
        'card_type': '',
        'statement_currency': 'TRY',
        'statement_period': '',
    }
    prev_line = ""
    for line in all_text.split('\n'):
        line = line.strip()
        if not line:
            continue

        # Card number: "4543********6152"
        if not meta['card_number']:
            m = CARD_NUMBER_PATTERN.search(line)
            if m:
                meta['card_number'] = m.group(1)

        # Card type: "VISA Klasik Hesap Özetiniz"
        if not meta['card_type']:
            m = CARD_TYPE_PATTERN.match(line)
            if m:
                meta['card_type'] = m.group(1).strip()

        # Statement period from "Hesap Kesim Tarihi:" label.
        # With extract_pages, the label and its date value (DD.MM.YYYY)
        # are separate text lines at the same y-position.  They may appear
        # in any order, so we search all lines for a matching date.
        if 'Hesap Kesim Tarihi' in line and 'Sonraki' not in line and not meta['statement_period']:
            for date_line in all_text.split('\n'):
                date_match = re.match(r'^(\d{2})\.(\d{2})\.(\d{4})$', date_line.strip())
                if date_match:
                    day, month, year = date_match.groups()
                    meta['statement_period'] = f"{year}-{month}"
                    break

        # Detect statement currency from amount+currency patterns near
        # "Hesap Özeti Borcu".  With extract_pages, the label and value are
        # separate text lines at the same y-position, so prev_line won't
        # reliably contain the value.  Instead, search the full text for
        # amount patterns followed by a currency code.
        if 'Hesap Özeti Borcu' in line:
            # Search full text for standalone "AMOUNT CURRENCY" lines
            # (e.g. "709,85 USD").  Use end-of-line anchor to avoid matching
            # transaction descriptions like "28 GBP SATIŞ".
            for currency_line in all_text.split('\n'):
                currency_match = re.match(
                    r'^[\d.,]+\s+(USD|EUR|GBP)$', currency_line.strip()
                )
                if currency_match:
                    meta['statement_currency'] = currency_match.group(1)
                    break

        prev_line = line

    return meta


class IsbankParser(BankParser):
    """
    Parser for İşbank credit card statements.

    Handles all İşbank card types (VISA Klasik, TROY, MAXIMUM, Maximiles, etc.)
    using the same parsing logic. Card identity is extracted from the PDF header
    and included in each transaction record.

    Amount matching uses pdfminer's layout analysis to get exact per-line vertical
    positions. Each transaction's date line is matched to the TUTAR (amount) column
    by y-position (within ±3px tolerance). Lines with no matching amount get
    amount=None and are excluded from the database.
    """
    parse_after = None

    # Position matching tolerance in PDF points
    POSITION_TOLERANCE = 3.0

    # Tolerance for matching TUTAR column x1 to header x1
    COLUMN_X1_TOLERANCE = 5.0

    def extract_transactions(self, pdf_path: str) -> List[Dict[str, Any]]:
        transactions = []
        try:
            transactions = self._parse_with_layout(pdf_path)
        except Exception as e:
            self.logger.error(f"Error parsing PDF {pdf_path}: {e}", exc_info=True)

        self.logger.info(f"Parsed {len(transactions)} transactions from {pdf_path}")
        return transactions

    def _pdf_to_html(self, pdf_path: str) -> str:
        """Convert PDF to HTML using pdfminer (kept for --dump-html)."""
        output = BytesIO()
        laparams = LAParams()
        with open(pdf_path, 'rb') as f:
            extract_text_to_fp(f, output, output_type='html', laparams=laparams)
        return output.getvalue().decode('utf-8')

    def _parse_with_layout(self, pdf_path: str) -> List[Dict[str, Any]]:
        """Parse PDF using pdfminer's layout analysis for position-based matching."""
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextLine

        def _collect_text_lines(element):
            """Recursively collect all LTTextLine objects from any container."""
            if isinstance(element, LTTextLine):
                yield element
            elif hasattr(element, '__iter__'):
                for child in element:
                    yield from _collect_text_lines(child)

        # ── Step 1: Extract all text lines with exact positions ───────────────
        # First pass: collect all lines and find the TUTAR header to determine
        # the column position dynamically (x1 varies across card layouts).
        raw_lines: List[Tuple[float, float, float, str]] = []  # (x0, x1, top, text)
        tutar_x1: Optional[float] = None

        page_num = 0
        for page_layout in extract_pages(pdf_path, laparams=LAParams()):
            page_height = page_layout.height
            page_offset = page_num * 10000  # ensure unique tops across pages
            for line in _collect_text_lines(page_layout):
                x0, y0, x1, y1 = line.bbox
                top = page_offset + (page_height - y1)  # top-down + page offset
                text = line.get_text().strip()
                if not text:
                    continue
                raw_lines.append((x0, x1, top, text))

                # Detect TUTAR column header
                if text == 'TUTAR' and tutar_x1 is None:
                    tutar_x1 = x1

            page_num += 1

        if tutar_x1 is None:
            self.logger.warning(f"No TUTAR header found in {pdf_path}")
            return []

        # ── Step 2: Classify lines by column ──────────────────────────────────
        desc_lines: List[Dict[str, Any]] = []      # {top, text}
        amount_map: Dict[float, float] = {}         # top → amount value
        all_text_parts: List[str] = []

        for x0, x1, top, text in raw_lines:
            all_text_parts.append(text)

            if x0 < 200:
                # Description column
                desc_lines.append({'top': top, 'text': text})
            elif abs(x1 - tutar_x1) <= self.COLUMN_X1_TOLERANCE:
                # TUTAR (amount) column — x1 matches the header's right edge
                if AMOUNT_PATTERN.match(text):
                    val = _normalise_amount(text)
                    if val is not None:
                        amount_map[top] = val

        # Sort desc lines by vertical position (document order)
        desc_lines.sort(key=lambda d: d['top'])

        # ── Step 2: Extract card metadata from full text ──────────────────────
        full_text = '\n'.join(all_text_parts)
        card_meta = _extract_card_metadata(full_text)
        default_currency = card_meta.get('statement_currency', 'TRY')

        # ── Step 3: Walk desc lines and match amounts by position ─────────────
        transactions: List[Dict[str, Any]] = []
        stop_parsing = False
        in_transaction_area = False  # Stop markers only apply after we've
                                     # entered the transaction section

        for dline in desc_lines:
            if stop_parsing:
                break

            line = dline['text']
            line_top = dline['top']

            # Stop markers — only apply once we've entered the transaction area.
            # These markers also appear in the page header/summary section
            # which is positioned above the transaction table.
            if in_transaction_area:
                if PAYMENT_THANKS_MARKER in line:
                    stop_parsing = True
                    break
                if line.strip() == TOTAL_MARKER:
                    stop_parsing = True
                    break

            # Skip previous balance line (not a transaction)
            if PREV_BALANCE_MARKER in line:
                in_transaction_area = True
                continue
            if line.startswith('***') or line.startswith('****'):
                continue

            # Skip known non-transaction content
            if line.strip() in ('İŞLEM', 'TARİHİ', 'İŞLEMTARİHİ'):
                continue
            if 'İŞLEM' in line and 'TARİH' in line:
                continue
            if line.startswith('TUTAR') or line.startswith('AÇIKLAMA'):
                continue
            if '(cid:' in line:
                continue

            date_match = DATE_PATTERN.match(line)
            if date_match:
                in_transaction_area = True
                desc = date_match.group(2).strip()
                date_str = date_match.group(1)

                try:
                    date_obj = datetime.strptime(date_str, '%d/%m/%Y')
                except ValueError:
                    self.logger.warning(f"Could not parse date: {date_str}")
                    continue

                if not self.can_parse(date_obj):
                    continue

                # Look up amount by position (±tolerance)
                amount = self._find_amount_at_position(line_top, amount_map)

                is_payment = PAYMENT_MARKER in desc

                transactions.append({
                    'date': date_obj,
                    'description': desc,
                    'amount': amount,  # None if no TUTAR value at this row
                    'currency': default_currency,
                    'category': 'Payment' if is_payment else 'Uncategorized',
                    'card_number': card_meta.get('card_number', ''),
                    'card_type': card_meta.get('card_type', ''),
                    'statement_period': card_meta.get('statement_period', ''),
                    'raw_text': f"{date_str} {desc} {amount}",
                })
            else:
                # Non-date line — possible continuation of previous description
                if any(kw in line for kw in SKIP_SUBSTRINGS):
                    continue

                if transactions and not any(
                    kw in line for kw in CONTINUATION_STOP_KEYWORDS
                ):
                    transactions[-1]['description'] += ' ' + line

        return transactions

    def _find_amount_at_position(
        self, target_top: float, amount_map: Dict[float, float]
    ) -> Optional[float]:
        """Find an amount value at the same vertical position (within tolerance).

        Returns the amount value if found, or None if no TUTAR value exists at
        this row position.
        """
        best_val = None
        best_dist = self.POSITION_TOLERANCE + 1  # start beyond tolerance

        for amt_top, amt_val in amount_map.items():
            dist = abs(amt_top - target_top)
            if dist < best_dist:
                best_dist = dist
                best_val = amt_val

        if best_dist <= self.POSITION_TOLERANCE:
            return best_val
        return None


