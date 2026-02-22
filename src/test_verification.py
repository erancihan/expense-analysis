import unittest
from unittest.mock import MagicMock, patch
from src.models import Expense, Base
from src.parsers.sample import SampleBankParser
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime

class TestExpenseApp(unittest.TestCase):
    def setUp(self):
        # Setup in-memory SQLite db
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()

    def test_database_insert(self):
        expense = Expense(
            date=datetime.now(),
            description="Test Expense",
            amount=100.50,
            currency="USD",
            bank_source="test_bank"
        )
        self.db.add(expense)
        self.db.commit()

        retrieved = self.db.query(Expense).first()
        self.assertEqual(retrieved.description, "Test Expense")
        self.assertEqual(retrieved.amount, 100.50)

    @patch('src.parsers.sample.pypdf.PdfReader')
    def test_parser(self, MockPdfReader):
        # Mock PDF content
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "2023-10-27超市购物 150.00\n2023-10-28 PAYPAL *NETFLIX 199.99"
        
        # Adjusting the mock to match the regex in SampleBankParser
        # Regex: (\d{4}-\d{2}-\d{2})\s+(.+)\s+(\d+\.\d{2})
        mock_page.extract_text.return_value = "2023-10-27 Supermarket 150.00\nIrrelevant line\n2023-10-28 Netflix 199.99"

        mock_pdf = MockPdfReader.return_value
        mock_pdf.pages = [mock_page]

        parser = SampleBankParser()
        transactions = parser.extract_transactions("dummy.pdf")

        self.assertEqual(len(transactions), 2)
        self.assertEqual(transactions[0]['description'], "Supermarket")
        self.assertEqual(transactions[0]['amount'], 150.00)
        self.assertEqual(transactions[1]['description'], "Netflix")
        self.assertEqual(transactions[1]['amount'], 199.99)

if __name__ == '__main__':
    unittest.main()
