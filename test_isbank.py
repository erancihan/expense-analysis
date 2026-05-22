import sys
import logging
logging.basicConfig(level=logging.DEBUG)

from src.parsers.isbank import IsbankParser
class MyParser(IsbankParser):
    def can_parse(self, date_obj): return True

parser = MyParser()
txs = parser.extract_transactions(sys.argv[1])
for t in txs:
    print(t['date'].strftime('%Y-%m-%d'), t['amount'], t['currency'], t['description'])
