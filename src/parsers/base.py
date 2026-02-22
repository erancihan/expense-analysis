from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

class BankParser(ABC):
    """
    Abstract base class for bank statement parsers.
    """
    # If set, the parser will skip PDFs whose statement date is older than this.
    # Set to None to parse all files regardless of date.
    parse_after: Optional[datetime] = None

    # If set, the parser will skip PDFs whose statement date is newer than this.
    # Set to None to parse all files regardless of date.
    parse_before: Optional[datetime] = None

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    def can_parse(self, statement_date: Optional[datetime] = None) -> bool:
        """
        Returns True if this parser should process the given statement.
        If parse_before is set and a statement_date is provided,
        returns True when the statement date is before or equal to parse_before.
        """
        if self.parse_before is None or self.parse_after is None or statement_date is None:
            return True

        if self.parse_after <= statement_date and statement_date <= self.parse_before:
            return True

        return False

    @abstractmethod
    def extract_transactions(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        Parses the PDF file and returns a list of dictionaries representing transactions.
        Each dictionary should have keys corresponding to the Expense model fields:
        - date (datetime object)
        - description (str)
        - amount (float)
        - currency (str)
        - category (str, optional)
        - raw_text (str, optional)
        """
        pass
