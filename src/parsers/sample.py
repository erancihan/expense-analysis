from .base import BankParser
from typing import List, Dict, Any
import pypdf
from datetime import datetime
import re

class SampleBankParser(BankParser):
    def extract_transactions(self, pdf_path: str) -> List[Dict[str, Any]]:
        transactions = []
        try:
            reader = pypdf.PdfReader(pdf_path)
            full_text = ""
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"
            
            # This is a dummy implementation.
            # Real parsing logic would use regex or other methods to find transaction lines.
            # Example heuristic: look for lines starting with a date.
            
            # Regex for YYYY-MM-DD
            date_pattern = re.compile(r'(\d{4}-\d{2}-\d{2})\s+(.+)\s+(\d+\.\d{2})')
            
            for line in full_text.split('\n'):
                match = date_pattern.search(line)
                if match:
                    date_str, desc, amount_str = match.groups()
                    try:
                        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                        amount = float(amount_str)
                        transactions.append({
                            'date': date_obj,
                            'description': desc.strip(),
                            'amount': amount,
                            'currency': 'TRY', # Default or scraped
                            'category': 'Uncategorized',
                            'raw_text': line
                        })
                    except ValueError:
                        self.logger.warning(f"Failed to parse line: {line}")
                        continue
                        
        except Exception as e:
            self.logger.error(f"Error parsing PDF {pdf_path}: {e}")

        self.logger.info(f"Parsed {len(transactions)} transactions from {pdf_path}")    
        
        return transactions
