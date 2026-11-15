import re
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

MAX_PDF_BYTES = 5 * 1024 * 1024
MONEY_PATTERN = r"\$((?:\d{1,3}(?:,\d{3})+|\d{1,9})\.\d{2})"
DATE_PATTERN = r"([A-Z][a-z]{2} \d{1,2}, \d{4})"


@dataclass(frozen=True)
class ExtractedInvoice:
    vendor: str
    number: str
    po: str
    issued: date
    due: date
    terms: int
    amount: int
    line_total: int


def require_single(pattern: str, text: str) -> str:
    matches = re.findall(pattern, text, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("Expected one value for each invoice field")
    return matches[0]


def cents(value: str) -> int:
    return int(value.replace(",", "").replace(".", ""))


def read_invoice(pdf: bytes) -> ExtractedInvoice | None:
    """Read the one-page, USD text layout used by the demo invoices."""
    try:
        reader = PdfReader(BytesIO(pdf))
        if reader.is_encrypted or len(reader.pages) != 1:
            return None
        text = reader.pages[0].extract_text(extraction_mode="layout")
        vendor = require_single(r"\A([^\n]{1,160})\n", text).strip()
        number = require_single(r"^Invoice ([A-Za-z0-9][A-Za-z0-9 /._-]{0,63})$", text)
        po = require_single(r"\bPurchase order +([A-Z0-9-]{1,32})$", text)
        issued = datetime.strptime(require_single(r"\bIssued +" + DATE_PATTERN + "$", text), "%b %d, %Y").date()
        due = datetime.strptime(require_single(r"^ +Due +" + DATE_PATTERN + "$", text), "%b %d, %Y").date()
        terms = int(require_single(r"^ +Terms +Net (\d{1,3})$", text))
        amount = cents(require_single(r"^ +Total due +" + MONEY_PATTERN + "$", text))
        item_block = require_single(r"^ *Description +Amount \(USD\)\n([\s\S]*?)^ +Total due ", text)
        items = [
            cents(require_single(r"^\s+\S.* +" + MONEY_PATTERN + "$", line))
            for line in item_block.splitlines()
            if line.strip()
        ]
        if not vendor or not items or not 1 <= terms <= 365 or not 1 <= amount <= 99999999999:
            return None
        return ExtractedInvoice(vendor, number.strip(), po, issued, due, terms, amount, sum(items))
    except (PyPdfError, ValueError, TypeError, KeyError, OverflowError):
        return None
