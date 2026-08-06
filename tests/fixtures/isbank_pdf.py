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
    """

    text: str
    amount: Optional[str] = None


def build_statement(
    path: str,
    rows: List[Row],
    *,
    card_number: str = '4543********6152',
    card_type: str = 'VISA Klasik',
    cutoff_date: str = '15.07.2026',
    include_tutar_header: bool = True,
) -> str:
    """Write a synthetic statement to `path` and return `path`.

    `cutoff_date` must be the only bare DD.MM.YYYY string in the document:
    _extract_card_metadata takes the first one it finds as statement_period.
    """
    pdf = canvas.Canvas(path, pagesize=A4)
    pdf.setFont(FONT_NAME, FONT_SIZE)
    _, page_height = A4
    y = page_height - TOP_MARGIN

    for header_line in (
        card_number,
        f'{card_type} Hesap Özetiniz',
        'Hesap Kesim Tarihi',
        cutoff_date,
    ):
        pdf.drawString(DESC_X, y, header_line)
        y -= ROW_PITCH

    if include_tutar_header:
        pdf.drawRightString(AMOUNT_RIGHT_X, y, 'TUTAR')
    y -= ROW_PITCH

    for row in rows:
        pdf.drawString(DESC_X, y, row.text)
        if row.amount is not None:
            pdf.drawRightString(AMOUNT_RIGHT_X, y, row.amount)
        y -= ROW_PITCH

    pdf.showPage()
    pdf.save()
    return path
