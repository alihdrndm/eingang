"""Post-processing of LLM-read invoice values (HANDOFF sections 5 and 6).

Pure and deterministic: no LLM call, no database, no clock other than `eingang.clock`.

- `prepare_text` builds the text the model reads: the first 6 pages, each introduced by
  `--- page N ---` (the same format as `einvoice.pdf.PdfText.as_text`), cut at 12,000
  characters.
- `post_process` turns an `ExtractedInvoice` into Invoice column values, plus the
  `field_confidence` and `field_evidence` maps (section 5, "Post-processing and confidence").
- `compare` lists the differences between an invoice's XML and the values read from its
  visible PDF text (section 6, check C09).

German and English number and date formats are read by `parse_amount` and `parse_date`;
the rules for ambiguous forms are written on those functions.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from stdnum import bic as stdnum_bic
from stdnum import iban as stdnum_iban
from stdnum.eu import vat as stdnum_vat

from eingang import clock
from einvoice.model import CanonicalInvoice, Line, TaxBreakdown
from invoices.models import Invoice
from llm.schemas import ComparedValues, ExtractedInvoice, ExtractedLine, ExtractedTaxRow

PAGE_LIMIT = 6
TEXT_LIMIT = 12_000
EARLIEST_DATE = date(2000, 1, 1)
LATEST_DATE_DAYS_AHEAD = 400

HIGH = "high"
MEDIUM = "medium"
LOW = "low"

CENT = Decimal("0.01")

# --- the text the model reads ---------------------------------------------------------------


def prepare_text(pages: list[str]) -> tuple[str, bool]:
    """The first 6 pages, each prefixed `--- page N ---`, cut at 12,000 characters.

    Returns the text and whether it was cut (`Invoice.text_truncated`). Pages beyond the
    sixth are always left out and do not set the flag: the flag only means "the 12,000
    character limit was reached".
    """
    text = "\n".join(
        f"--- page {number} ---\n{page}" for number, page in enumerate(pages[:PAGE_LIMIT], start=1)
    )
    if len(text) <= TEXT_LIMIT:
        return text, False
    return text[:TEXT_LIMIT], True


def collapse(text: str) -> str:
    """Every run of whitespace becomes one space; leading and trailing whitespace goes."""
    return " ".join(text.split())


def _alnum_upper(text: str) -> str:
    """Letters and digits only, upper-case: how IBANs, BICs and VAT IDs are compared."""
    return re.sub(r"[^0-9A-Za-z]", "", text).upper()


# --- amounts --------------------------------------------------------------------------------

_MINUS_SIGNS = "-\u2212\u2013"  # hyphen-minus, minus sign, en dash
_SPACES = " \u00a0\u202f\u2009"  # space, no-break space, narrow no-break space, thin space
_GROUP_MARKS = _SPACES + "'\u2019"  # spaces and apostrophes only ever group thousands
_CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY"}
_PLAIN_DECIMAL = re.compile(r"-?\d+(?:\.\d+)?")
_NUMBER_BODY = re.compile(r"\d+(?:[.,'\u2019 \u00a0\u202f\u2009]\d+)*")
_CURRENCY_AFFIX = re.compile(r"^(?:[A-Z]{3}\b|[€$£¥])\s*|\s*(?:\b[A-Z]{3}|[€$£¥])$")


def parse_amount(text: str | None) -> Decimal | None:
    """Read an amount written in German or English style; None if it is not one.

    A plain dot-decimal (`1234.56`, `-12.500`) is read as written: that is the format the
    model is asked to return. Otherwise a currency symbol or ISO code before or after the
    number is ignored, a leading or trailing minus (`-12,50`, `12,50-`) or parentheses
    make it negative, and the separators decide the rest:

    - both `.` and `,` present: the last one is the decimal mark (`1.234,56`, `1,234.56`);
    - spaces and apostrophes group thousands (`1 234,56`, `1'234.56`);
    - one kind of mark used more than once groups thousands (`1.234.567`);
    - one mark used once followed by exactly three digits, with a first group of 1-3
      digits not starting with 0, is read as thousands (`1,234` and `1.234,-` are 1234);
      this is the ambiguous case, and invoices rarely print three decimals. A bare
      `1.234` is a plain dot-decimal, so it is 1.234 (and then fails the two-decimals
      rule); `amounts_in` keeps both readings when re-reading a snippet;
    - any other single mark is the decimal mark (`1234,56`, `0,125`, `1234.5`).
    """
    if text is not None and _PLAIN_DECIMAL.fullmatch(collapse(text)):
        return Decimal(collapse(text))
    readings = _amount_readings(text)
    return readings[0] if readings else None


def _amount_readings(text: str | None) -> list[Decimal]:
    """Every plausible reading of one amount, the preferred one first."""
    if text is None:
        return []
    value = _CURRENCY_AFFIX.sub("", collapse(text)).strip()
    negative = False
    if value.startswith("(") and value.endswith(")"):
        negative, value = True, value[1:-1].strip()
    if value and value[0] in _MINUS_SIGNS:
        negative, value = True, value[1:].strip()
    elif value and value[-1] in _MINUS_SIGNS:
        negative, value = True, value[:-1].strip()
    if not _NUMBER_BODY.fullmatch(value):
        return []
    readings = _unsigned_readings(value)
    return [-reading if negative else reading for reading in readings]


def _unsigned_readings(body: str) -> list[Decimal]:
    marks = [char for char in body if not char.isdigit()]
    if not marks:
        return [Decimal(body)]
    readings: list[Decimal] = []
    grouped = _read_grouped(body, set(marks))
    if grouped is not None:
        readings.append(grouped)
    last = marks[-1]
    if last in ".,":
        whole, fraction = body[: body.rindex(last)], body[body.rindex(last) + 1 :]
        whole_marks = set(whole) - set("0123456789")
        if last not in whole_marks:
            integer = _read_grouped(whole, whole_marks) if whole_marks else Decimal(whole)
            if integer is not None:
                readings.append(Decimal(f"{integer}.{fraction}"))
    return readings


def _read_grouped(body: str, marks: set[str]) -> Decimal | None:
    """`body` as digit groups of three split by one kind of mark, or None if it is not."""
    if len(marks) > 1 and not marks <= set(_GROUP_MARKS):
        return None
    groups = re.split(r"[.,'\u2019 \u00a0\u202f\u2009]", body)
    first, rest = groups[0], groups[1:]
    if not rest or not 1 <= len(first) <= 3 or first.startswith("0"):
        return None
    if any(len(group) != 3 for group in rest):
        return None
    return Decimal("".join(groups))


_AMOUNT_TOKEN = re.compile(
    r"(?<![\d.,])([-\u2212\u2013]\s?)?(\d(?:[\d.,'\u2019 \u00a0\u202f\u2009]*\d)?)(-(?!\d))?"
)


def amounts_in(snippet: str) -> set[Decimal]:
    """Every amount that can be read from a snippet of text, in any plausible reading."""
    found: set[Decimal] = set()
    for match in _AMOUNT_TOKEN.finditer(snippet):
        leading, token, trailing = match.group(1), match.group(2), match.group(3)
        pieces = re.split(r"[ \u00a0\u202f\u2009]+", token)
        for start in range(len(pieces)):
            for end in range(start + 1, len(pieces) + 1):
                candidate = " ".join(pieces[start:end])
                for reading in _amount_readings(candidate):
                    found.add(reading)
                    if (leading and start == 0) or (trailing and end == len(pieces)):
                        found.add(-reading)
    return found


def has_cent_precision(amount: Decimal) -> bool:
    """True when the amount has at most two decimal places (`12.500` counts as 12.50)."""
    return amount == amount.quantize(CENT)


# --- dates ----------------------------------------------------------------------------------

_MONTHS = {
    "january": 1, "jan": 1, "januar": 1, "jänner": 1, "jaenner": 1,
    "february": 2, "feb": 2, "februar": 2,
    "march": 3, "mar": 3, "märz": 3, "maerz": 3, "mär": 3, "mrz": 3,
    "april": 4, "apr": 4,
    "may": 5, "mai": 5,
    "june": 6, "jun": 6, "juni": 6,
    "july": 7, "jul": 7, "juli": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "oktober": 10, "okt": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12, "dezember": 12, "dez": 12,
}  # fmt: skip
_MONTH = "(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b"
_YEAR_FIRST = re.compile(r"(?<!\d)(\d{4})([-/.])(\d{1,2})\2(\d{1,2})(?!\d)")
_DAY_FIRST = re.compile(r"(?<!\d)(\d{1,2})([./-])(\d{1,2})\2(\d{4}|\d{2})(?![\d])")
_DAY_MONTH_NAME = re.compile(r"(?<!\d)(\d{1,2})\.?\s*" + _MONTH + r"\.?,?\s*(\d{4})(?!\d)", re.I)
_MONTH_NAME_DAY = re.compile(
    r"(?<!\w)" + _MONTH + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\.?,?\s+(\d{4})(?!\d)", re.I
)


def parse_date(text: str | None) -> date | None:
    """Read a date written in German or English style; None if it is not one.

    Accepted: `2026-10-31` (also with `/` or `.`), `31.10.2026`, `31/10/2026`,
    `31-10-2026`, `31. Oktober 2026`, `31 Okt. 2026`, `October 31, 2026`, `Oct 31st 2026`.
    Ambiguous forms are read this way:

    - numeric dates with the year last are day first (`03/04/2026` is 3 April); a slash or
      dash date is read month first only when day first is impossible (`10/31/2026`);
      dotted dates are always day first;
    - a two-digit year is in 2000-2099 (`31.10.26` is 2026-10-31).
    """
    if text is None:
        return None
    readings = _date_readings(collapse(text), whole=True)
    return readings[0] if readings else None


def dates_in(snippet: str) -> set[date]:
    """Every date that can be read from a snippet of text, in any plausible reading."""
    return set(_date_readings(snippet, whole=False))


def _date_readings(text: str, *, whole: bool) -> list[date]:
    readings: list[date] = []

    def matches(pattern: re.Pattern[str]) -> list[re.Match[str]]:
        if whole:
            match = pattern.fullmatch(text)
            return [match] if match else []
        return list(pattern.finditer(text))

    for match in matches(_YEAR_FIRST):
        _add_date(readings, int(match[1]), int(match[3]), int(match[4]))
    for match in matches(_DAY_FIRST):
        first, second, year = int(match[1]), int(match[3]), _full_year(match[4])
        day_first = _make_date(year, second, first)
        if day_first is not None:
            readings.append(day_first)
        if match[2] != "." and (day_first is None or not whole):
            _add_date(readings, year, first, second)
    for match in matches(_DAY_MONTH_NAME):
        _add_date(readings, int(match[3]), _MONTHS[match[2].lower()], int(match[1]))
    for match in matches(_MONTH_NAME_DAY):
        _add_date(readings, int(match[3]), _MONTHS[match[1].lower()], int(match[2]))
    return readings


def _full_year(digits: str) -> int:
    return 2000 + int(digits) if len(digits) == 2 else int(digits)


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _add_date(readings: list[date], year: int, month: int, day: int) -> None:
    made = _make_date(year, month, day)
    if made is not None:
        readings.append(made)


# --- field kinds: how a value is normalised, validated and re-read from its evidence -------

# ISO 4217 currency codes in use (funds and precious metals left out). BGN and ANG are
# kept for invoices issued before their replacement by EUR (2026) and XCG (2025).
_ISO_4217_CODES = """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD
    BTN BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB
    EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK
    JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA
    MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK
    PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN
    SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VED VES VND VUV
    WST XAF XCD XCG XOF XPF YER ZAR ZMW ZWG
"""
ISO_4217 = frozenset(_ISO_4217_CODES.split())

Value = str | date | Decimal


@dataclass(frozen=True)
class FieldKind:
    normalise: Callable[[str], Value | None]
    is_valid: Callable[[Value, date], bool]
    read_from: Callable[[Value, str], bool]


def _normalise_text(raw: str) -> str | None:
    return collapse(raw) or None


def _normalise_code(raw: str) -> str | None:
    return _alnum_upper(raw) or None


def _normalise_currency(raw: str) -> str | None:
    value = raw.strip()
    return _CURRENCY_SYMBOLS.get(value, value.upper()) or None


def _text_in(value: Value, evidence: str) -> bool:
    return collapse(str(value)).casefold() in collapse(evidence).casefold()


def _code_in(value: Value, evidence: str) -> bool:
    return str(value) in _alnum_upper(evidence)


def _currency_in(value: Value, evidence: str) -> bool:
    if re.search(rf"(?<![A-Za-z]){re.escape(str(value))}(?![A-Za-z])", evidence.upper()):
        return True
    symbols = [symbol for symbol, code in _CURRENCY_SYMBOLS.items() if code == value]
    return any(symbol in evidence for symbol in symbols) or (
        value == "EUR" and re.search(r"\beuro?\b", evidence, re.I) is not None
    )


def _amount_in(value: Value, evidence: str) -> bool:
    return value in amounts_in(evidence)


def _date_in(value: Value, evidence: str) -> bool:
    return value in dates_in(evidence)


def _date_in_range(value: Value, today: date) -> bool:
    latest = today + timedelta(days=LATEST_DATE_DAYS_AHEAD)
    return isinstance(value, date) and EARLIEST_DATE <= value <= latest


def _amount_ok(value: Value, today: date) -> bool:
    return isinstance(value, Decimal) and has_cent_precision(value)


def _always(value: Value, today: date) -> bool:
    return True


TEXT = FieldKind(_normalise_text, _always, _text_in)
DATE = FieldKind(parse_date, _date_in_range, _date_in)
AMOUNT = FieldKind(parse_amount, _amount_ok, _amount_in)
IBAN = FieldKind(_normalise_code, lambda value, _: stdnum_iban.is_valid(str(value)), _code_in)
VAT_ID = FieldKind(_normalise_code, lambda value, _: stdnum_vat.is_valid(str(value)), _code_in)
BIC = FieldKind(_normalise_code, lambda value, _: stdnum_bic.is_valid(str(value)), _code_in)
CURRENCY = FieldKind(_normalise_currency, lambda value, _: value in ISO_4217, _currency_in)
COUNTRY = FieldKind(
    lambda raw: raw.strip().upper() or None,
    lambda value, _: re.fullmatch(r"[A-Z]{2}", str(value)) is not None,
    _text_in,
)

HEADER_FIELDS: dict[str, FieldKind] = {
    "invoice_number": TEXT,
    "issue_date": DATE,
    "due_date": DATE,
    "currency": CURRENCY,
    "buyer_reference": TEXT,
    "order_reference": TEXT,
    "seller_name": TEXT,
    "seller_vat_id": VAT_ID,
    "seller_tax_number": TEXT,
    "seller_street": TEXT,
    "seller_postcode": TEXT,
    "seller_city": TEXT,
    "seller_country_code": COUNTRY,
    "seller_email": TEXT,
    "buyer_name": TEXT,
    "buyer_vat_id": VAT_ID,
    "payee_iban": IBAN,
    "payee_bic": BIC,
    "payment_terms": TEXT,
    "line_total": AMOUNT,
    "allowance_total": AMOUNT,
    "charge_total": AMOUNT,
    "net_total": AMOUNT,
    "tax_total": AMOUNT,
    "gross_total": AMOUNT,
    "prepaid_amount": AMOUNT,
    "payable_amount": AMOUNT,
}


def _column_length(name: str) -> int | None:
    max_length: object = getattr(Invoice._meta.get_field(name), "max_length", None)
    return max_length if isinstance(max_length, int) else None


def fits_column(name: str, value: Value) -> bool:
    """Whether the value can be stored in its Invoice column without changing it."""
    if isinstance(value, Decimal):
        return has_cent_precision(value)
    if isinstance(value, str):
        limit = _column_length(name)
        return limit is None or len(value) <= limit
    return True


# --- confidence -----------------------------------------------------------------------------


def evidence_found(evidence: str | None, collapsed_page_text: str) -> bool:
    """The evidence, whitespace collapsed, occurs in the (collapsed) page text."""
    if evidence is None:
        return False
    snippet = collapse(evidence)
    return bool(snippet) and snippet in collapsed_page_text


@dataclass(frozen=True)
class FieldResult:
    value: Value | None  # what is stored; None when the value cannot be stored as read
    confidence: str


def assess(
    name: str, raw: str, evidence: str | None, collapsed_page_text: str, today: date
) -> FieldResult:
    """Normalise one header value and grade it `high`, `medium` or `low` (section 5)."""
    kind = HEADER_FIELDS[name]
    value = kind.normalise(raw)
    valid = value is not None and kind.is_valid(value, today) and fits_column(name, value)
    found = evidence_found(evidence, collapsed_page_text)
    reread = (
        found and value is not None and evidence is not None and kind.read_from(value, evidence)
    )
    if found and valid and reread:
        confidence = HIGH
    elif found and valid:
        confidence = MEDIUM
    else:
        confidence = LOW
    stored = value if value is not None and fits_column(name, value) else None
    if isinstance(stored, Decimal):
        stored = _cents(stored)
    return FieldResult(value=stored, confidence=confidence)


# --- from the model's answer to Invoice columns ---------------------------------------------


@dataclass(frozen=True)
class Extraction:
    """Invoice column values plus the confidence and evidence maps for one extraction.

    `columns` has the same keys as `invoices.persist.invoice_columns`, so it can replace
    an invoice's data the same way. The LLM may leave out values the canonical invoice
    requires (number, date, currency, seller name, gross total), so this is not a
    `CanonicalInvoice`: every Invoice column is nullable.
    """

    columns: dict[str, object]
    lines: list[Line]
    confidence: dict[str, str]
    evidence: dict[str, str | None]


_UNREAD_COLUMNS = (
    "buyer_tax_number", "buyer_street", "buyer_postcode", "buyer_city",
    "buyer_country_code", "buyer_email",
)  # fmt: skip


def post_process(
    extracted: ExtractedInvoice, page_text: str, today: date | None = None
) -> Extraction:
    """Grade every value the model read and turn the answer into Invoice columns.

    `page_text` is the text the model was given (`prepare_text`). A null value gets no
    confidence or evidence entry (a value that is not printed is not doubtful). A value
    that cannot be stored as read (not a date, more than two decimals, longer than its
    column) is stored as None, but keeps its `low` confidence and its evidence so check
    C13 asks a person for it. A value that is stored but fails its validator (an IBAN with
    a bad checksum, a date in 1999) is kept as read, so checks C06 and C07 can name it.
    """
    today = today or clock.today()
    collapsed_page_text = collapse(page_text)
    columns: dict[str, object] = {"type_code": None, "notes": []}
    columns.update(dict.fromkeys(_UNREAD_COLUMNS))
    confidence: dict[str, str] = {}
    evidence: dict[str, str | None] = {}
    for name in HEADER_FIELDS:
        raw: str | None = getattr(extracted, name)
        snippet: str | None = getattr(extracted, f"{name}_evidence")
        if raw is None or raw.strip().lower() in _NO_VALUE:
            columns[name] = None
            continue
        result = assess(name, raw, snippet, collapsed_page_text, today)
        columns[name] = result.value
        confidence[name] = result.confidence
        evidence[name] = snippet.strip() if snippet is not None and snippet.strip() else None
    columns["tax_breakdown"] = [
        row.model_dump(mode="json") for row in _tax_rows(extracted.tax_breakdown)
    ]
    return Extraction(
        columns=columns,
        lines=_lines(extracted.lines),
        confidence=confidence,
        evidence=evidence,
    )


# The model sometimes writes "no value" as text instead of JSON null (the M6 run: "null").
_NO_VALUE = frozenset({"", "null", "none", "n/a"})


def parse_number(text: str | None, places: int) -> Decimal | None:
    """An amount, quantity or rate with at most `places` decimals; a `%` sign is ignored."""
    if text is None:
        return None
    value = parse_amount(text.replace("%", ""))
    if value is None:
        return None
    exponent = Decimal(1).scaleb(-places)
    if value != value.quantize(exponent, rounding=ROUND_HALF_UP):
        return None
    return value


def _optional_text(text: str | None) -> str | None:
    if text is None:
        return None
    return collapse(text) or None


def _tax_rows(rows: list[ExtractedTaxRow]) -> list[TaxBreakdown]:
    parsed = [
        TaxBreakdown(
            category=_optional_text(row.category),
            rate=parse_number(row.rate, 2),
            taxable_amount=parse_number(row.taxable_amount, 2),
            tax_amount=parse_number(row.tax_amount, 2),
        )
        for row in rows
    ]
    return [row for row in parsed if row != TaxBreakdown()]


def _lines(lines: list[ExtractedLine]) -> list[Line]:
    parsed = [
        Line(
            description=_optional_text(line.description),
            quantity=parse_number(line.quantity, 4),
            unit_price=parse_number(line.unit_price, 6),
            net_amount=parse_number(line.net_amount, 2),
            tax_rate=parse_number(line.tax_rate, 2),
        )
        for line in lines
    ]
    return [line for line in parsed if line != Line()]


# --- section 6: the visible PDF against the XML ---------------------------------------------

COMPARED_FIELDS: dict[str, FieldKind] = {
    "invoice_number": TEXT,
    "issue_date": DATE,
    "gross_total": AMOUNT,
    "payable_amount": AMOUNT,
    "payee_iban": IBAN,
}


def compare(
    xml_invoice: CanonicalInvoice, pdf: ComparedValues, page_text: str | None = None
) -> list[dict[str, str]]:
    """The C09 differences `{"field", "xml", "pdf"}` between the XML and the visible PDF.

    Amounts are compared to the cent, dates exactly, the invoice number ignoring case and
    spaces, the IBAN ignoring case and spaces. A value the model did not find
    is not a difference, and neither is one that cannot be read as its type, one the XML
    does not have, or (when `page_text` is given) one whose evidence is not in that text.
    """
    collapsed_page_text = collapse(page_text) if page_text is not None else None
    differences: list[dict[str, str]] = []
    for name, kind in COMPARED_FIELDS.items():
        raw: str | None = getattr(pdf, name)
        xml_value: Value | None = getattr(xml_invoice, name)
        if raw is None or xml_value is None:
            continue
        snippet: str | None = getattr(pdf, f"{name}_evidence")
        if collapsed_page_text is not None and not evidence_found(snippet, collapsed_page_text):
            continue
        pdf_value = kind.normalise(raw)
        if pdf_value is None or _same(xml_value, pdf_value):
            continue
        differences.append({"field": name, "xml": _shown(xml_value), "pdf": _shown(pdf_value)})
    return differences


def _same(xml_value: Value, pdf_value: Value) -> bool:
    if isinstance(xml_value, Decimal) and isinstance(pdf_value, Decimal):
        return _cents(xml_value) == _cents(pdf_value)
    if isinstance(xml_value, str) and isinstance(pdf_value, str):
        return _loose(xml_value) == _loose(pdf_value)
    return xml_value == pdf_value


def _loose(text: str) -> str:
    return "".join(text.split()).casefold()


def _cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def _shown(value: Value) -> str:
    if isinstance(value, Decimal):
        return str(_cents(value))
    if isinstance(value, date):
        return value.isoformat()
    return value
