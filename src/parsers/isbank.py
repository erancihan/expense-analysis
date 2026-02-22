from .base import BankParser
from typing import List, Dict, Any
from datetime import datetime
from io import BytesIO
import re
import xml.etree.ElementTree as ET

from pdfminer.high_level import extract_text_to_fp
from pdfminer.layout import LAParams


class IsbankParser(BankParser):
    """
    Parser for İşbank credit card statements.
    Extracts XML from the PDF and parses transaction lines from the structured output.
    """
    # Set to None to parse all statements, or set a datetime to skip older ones
    parse_after = None

    def extract_transactions(self, pdf_path: str) -> List[Dict[str, Any]]:
        transactions = []
        try:
            xml_content = self._pdf_to_xml(pdf_path)
            lines = self._extract_text_lines(xml_content)
            transactions = self._parse_lines(lines)
        except Exception as e:
            self.logger.error(f"Error parsing PDF {pdf_path}: {e}")

        self.logger.info(f"Parsed {len(transactions)} transactions from {pdf_path}")
        return transactions

    def _pdf_to_xml(self, pdf_path: str) -> str:
        """Convert PDF to XML using pdfminer."""
        output = BytesIO()
        laparams = LAParams()
        with open(pdf_path, 'rb') as f:
            extract_text_to_fp(f, output, output_type='xml', laparams=laparams)
        return output.getvalue().decode('utf-8')

    def _extract_text_lines(self, xml_content: str) -> List[str]:
        """
        Parse the pdfminer XML output and extract text content line by line.
        Groups <text> elements by their <textline> parents.
        """
        lines = []
        try:
            root = ET.fromstring(xml_content)
            for textline in root.iter('textline'):
                chars = []
                for text_el in textline.iter('text'):
                    if text_el.text:
                        chars.append(text_el.text)
                line = ''.join(chars).strip()
                if line:
                    lines.append(line)
        except ET.ParseError as e:
            self.logger.error(f"Failed to parse XML: {e}")
        return lines

    def _parse_lines(self, lines: List[str]) -> List[Dict[str, Any]]:
        """
        Parse extracted text lines into transactions.
        İşbank credit card statements typically have lines like:
            DD.MM.YYYY  DESCRIPTION  AMOUNT
        """
        transactions = []
        # Regex for DD.MM.YYYY or DD/MM/YYYY date at the start of a line
        date_pattern = re.compile(
            r'^(\d{2}[./]\d{2}[./]\d{4})\s+(.+?)\s+([\d.,]+)\s*(TL|USD|EUR|GBP)?$'
        )

        for line in lines:
            match = date_pattern.match(line)
            if match:
                date_str, description, amount_str, currency = match.groups()

                # Normalise date separators
                date_str = date_str.replace('/', '.')
                try:
                    date_obj = datetime.strptime(date_str, '%d.%m.%Y')
                except ValueError:
                    self.logger.warning(f"Could not parse date from line: {line}")
                    continue

                # Check parse_after guard
                if not self.can_parse(date_obj):
                    continue

                # Normalise amount: 1.234,56 -> 1234.56
                amount_str = amount_str.replace('.', '').replace(',', '.')
                try:
                    amount = float(amount_str)
                except ValueError:
                    self.logger.warning(f"Could not parse amount from line: {line}")
                    continue

                transactions.append({
                    'date': date_obj,
                    'description': description.strip(),
                    'amount': amount,
                    'currency': currency or 'TRY',
                    'category': 'Uncategorized',
                    'raw_text': line,
                })

        return transactions
