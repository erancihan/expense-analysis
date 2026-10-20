"""Synthetic İşbank-shaped statement PDFs for parser tests.

Real statements carry a name and home address, so none is checked in. These
PDFs reproduce only the layout properties IsbankParser depends on:

  * a text line reading exactly 'TUTAR', whose right edge anchors the amount
    column (src/parsers/isbank.py:280-281 returns [] without it),
  * description lines at x0 < 200,
  * amount lines whose right edge is within COLUMN_X1_TOLERANCE (5.0pt) of the
    TUTAR header's,
  * amounts on the same baseline as their description, within
    POSITION_TOLERANCE (3.0pt).

Keep all text within Latin-1. reportlab's base-14 Helvetica uses
WinAnsiEncoding, which has no 'İ', 'ş', 'ğ' or 'ı'; 'Ö' and 'ü' are fine.
"""

from dataclasses import dataclass
from typing import List, Optional

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

DESC_X = 60.0            # description column — parser requires x0 < 200
LABEL_X = 300.0          # header label column, right of the description column
LABEL_VALUE_X = 460.0    # header value column, same baseline as its label.
                         # 'Bir Sonraki Hesap Kesim Tarihi:' ends at ~427.5pt
                         # in 9pt Helvetica; a gap under ~10pt would make
                         # pdfminer merge label and value into one line.
INSTALLMENT_X = 445.0    # TAKSİT BİLGİSİ column. Starts 25pt right of the
                         # amount column's edge so pdfminer keeps it a
                         # separate line from the amount.
AMOUNT_RIGHT_X = 420.0   # amount column right edge; the TUTAR header shares it
ROW_PITCH = 20.0         # far outside the parser's 3.0pt row tolerance
TOP_MARGIN = 60.0
FONT_NAME = 'Helvetica'
FONT_SIZE = 9


@dataclass
class Row:
    """One statement line.

    text:   description-column text, e.g. '01/07/2026 MIGROS ISTANBUL'.
            A line with no leading date becomes a continuation of the previous
            transaction's description.
    amount: amount-column text, e.g. '1.234,56'. None draws no amount, which
            makes the parser report amount=None for that row.
    installment: TAKSİT BİLGİSİ text, e.g. '2/4 taksidi (11.323,24)'. None
            draws nothing.
    """

    text: str
    amount: Optional[str] = None
    installment: Optional[str] = None


def build_statement(
    path: str,
    rows: List[Row],
    *,
    card_number: str = '4543********6152',
    card_type: str = 'VISA Klasik',
    cutoff_date: str = '15.07.2026',
    next_cutoff_date: Optional[str] = None,
    include_tutar_header: bool = True,
) -> str:
    """Write a synthetic statement to `path` and return `path`.

    The closing date is drawn the way real statements print it: the
    'Hesap Kesim Tarihi:' label and its DD.MM.YYYY value on one baseline.
    `next_cutoff_date` adds a 'Bir Sonraki Hesap Kesim Tarihi:' row *above*
    it, so the next closing date is the first date in text order — the layout
    that made the parser file 2021 statements a month late.
    """
    pdf = canvas.Canvas(path, pagesize=A4)
    pdf.setFont(FONT_NAME, FONT_SIZE)
    _, page_height = A4
    y = page_height - TOP_MARGIN

    for header_line in (card_number, f'{card_type} Hesap Özetiniz'):
        pdf.drawString(DESC_X, y, header_line)
        y -= ROW_PITCH

    if next_cutoff_date is not None:
        pdf.drawString(LABEL_X, y, 'Bir Sonraki Hesap Kesim Tarihi:')
        pdf.drawString(LABEL_VALUE_X, y, next_cutoff_date)
        y -= ROW_PITCH

    pdf.drawString(LABEL_X, y, 'Hesap Kesim Tarihi:')
    pdf.drawString(LABEL_VALUE_X, y, cutoff_date)
    y -= ROW_PITCH

    if include_tutar_header:
        pdf.drawRightString(AMOUNT_RIGHT_X, y, 'TUTAR')
    y -= ROW_PITCH

    for row in rows:
        pdf.drawString(DESC_X, y, row.text)
        if row.amount is not None:
            pdf.drawRightString(AMOUNT_RIGHT_X, y, row.amount)
        if row.installment is not None:
            pdf.drawString(INSTALLMENT_X, y, row.installment)
        y -= ROW_PITCH

    pdf.showPage()
    pdf.save()
    return path
