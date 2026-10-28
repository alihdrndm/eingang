"""What a received file is: rules D1-D5, the profile from BT-24, and the format label."""

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum

from lxml import etree

from einvoice import namespaces as ns
from einvoice import pdf
from einvoice.errors import UnsafeXmlError, UnsupportedFileError
from einvoice.model import CREDIT_NOTE_TYPE_CODE
from einvoice.xmlsafe import parse_xml, strip_leading

# D3: a PDF needs this many non-whitespace characters in its first pages to count as text.
MIN_TEXT_CHARS = 200


class Kind(StrEnum):
    XML = "xml"
    HYBRID_PDF = "hybrid_pdf"
    LEGACY_ZUGFERD1 = "legacy_zugferd1"
    HYBRID_PDF_UNSUPPORTED = "hybrid_pdf_unsupported"
    PDF_TEXT = "pdf_text"
    PDF_NO_TEXT = "pdf_no_text"


class Syntax(StrEnum):
    UBL = "ubl"
    CII = "cii"


class Profile(StrEnum):
    MINIMUM = "MINIMUM"
    BASIC_WL = "BASIC WL"
    BASIC = "BASIC"
    EN16931 = "EN 16931"
    EXTENDED = "EXTENDED"
    XRECHNUNG = "XRECHNUNG"
    UNKNOWN = "UNKNOWN"


EINVOICE_PROFILES = frozenset({Profile.BASIC, Profile.EN16931, Profile.EXTENDED, Profile.XRECHNUNG})
UNKNOWN_SPEC_NOTE = "Unknown specification identifier"


@dataclass(frozen=True)
class Detection:
    kind: Kind
    syntax: Syntax | None = None
    profile: Profile | None = None
    profile_version: str | None = None  # the XRechnung version, e.g. "3.0"
    spec_id: str | None = None  # BT-24
    xml: bytes | None = None
    has_text_layer: bool | None = None  # PDFs only
    page_count: int | None = None  # PDFs only
    ubl_credit_note: bool = False  # the root element is a UBL CreditNote
    notes: list[str] = field(default_factory=list)

    @property
    def is_einvoice(self) -> bool:
        """An EN 16931 invoice in a structured format (glossary: "E-invoice")."""
        return self.kind in (Kind.XML, Kind.HYBRID_PDF) and self.profile in EINVOICE_PROFILES


_XRECHNUNG_ID = re.compile(
    r"urn:(?:xeinkauf\.de:kosit:xrechnung|xoev-de:kosit:standard:xrechnung)(?:_(\d+(?:\.\d+)*))?"
)
_EN16931 = "urn:cen.eu:en16931:2017"
_EN16931_PEPPOL = f"{_EN16931}#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
# Any other CIUS ("#compliant#") or extension ("#conformant#") of EN 16931, such as Peppol
# self-billing, is still an EN 16931 invoice when no more specific profile matched.
_EN16931_DERIVED = (f"{_EN16931}#compliant#", f"{_EN16931}#conformant#")
# Order matters: "...:basic" is a prefix of "...:basicwl", so BASIC WL is tested first.
_FACTURX_PROFILES: list[tuple[Profile, tuple[str, ...]]] = [
    (Profile.MINIMUM, ("urn:factur-x.eu:1p0:minimum", "urn:zugferd.de:2p0:minimum")),
    (Profile.BASIC_WL, ("urn:factur-x.eu:1p0:basicwl", "urn:zugferd.de:2p0:basicwl")),
    (Profile.BASIC, ("urn:factur-x.eu:1p0:basic", "urn:zugferd.de:2p0:basic")),
    (Profile.EXTENDED, ("urn:factur-x.eu:1p0:extended", "urn:zugferd.de:2p0:extended")),
]


def profile_from_spec_id(spec_id: str | None) -> tuple[Profile, str | None]:
    """The profile and (for XRechnung) its version, from the BT-24 value."""
    if not spec_id:
        return Profile.UNKNOWN, None
    match = _XRECHNUNG_ID.search(spec_id)
    if match:
        return Profile.XRECHNUNG, match.group(1)
    for profile, identifiers in _FACTURX_PROFILES:
        if any(identifier in spec_id for identifier in identifiers):
            return profile, None
    if spec_id in (_EN16931, _EN16931_PEPPOL):
        return Profile.EN16931, None
    if spec_id.startswith(_EN16931_DERIVED) and spec_id not in _EN16931_DERIVED:
        return Profile.EN16931, None
    return Profile.UNKNOWN, None


def spec_id_of(root: etree._Element) -> str | None:
    """The BT-24 specification identifier of a UBL or CII root element."""
    if root.tag == ns.CII_ROOT:
        path = "rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID"
        value = root.findtext(path, namespaces=ns.CII_NS)
    else:
        value = root.findtext("cbc:CustomizationID", namespaces=ns.UBL_NS)
    return value.strip() if value and value.strip() else None


def _structured(kind: Kind, syntax: Syntax, root: etree._Element, xml: bytes) -> Detection:
    spec_id = spec_id_of(root)
    profile, version = profile_from_spec_id(spec_id)
    notes = [UNKNOWN_SPEC_NOTE] if profile is Profile.UNKNOWN else []
    return Detection(
        kind=kind,
        syntax=syntax,
        profile=profile,
        profile_version=version,
        spec_id=spec_id,
        xml=xml,
        ubl_credit_note=root.tag == ns.UBL_CREDIT_NOTE_ROOT,
        notes=notes,
    )


def _detect_pdf(data: bytes) -> Detection:
    text = pdf.extract_text(data)
    facts = Detection(
        kind=Kind.PDF_NO_TEXT,
        has_text_layer=text.non_whitespace_chars >= MIN_TEXT_CHARS,
        page_count=text.page_count,
    )
    embedded = pdf.embedded_invoice_xml(data)  # D1
    if embedded is None:  # D3
        return replace(facts, kind=Kind.PDF_TEXT if facts.has_text_layer else Kind.PDF_NO_TEXT)
    try:  # D2
        root = parse_xml(embedded)
    except UnsafeXmlError:
        raise
    except UnsupportedFileError:
        notes = ["The embedded XML is not well-formed"]
        return replace(facts, kind=Kind.HYBRID_PDF_UNSUPPORTED, notes=notes)
    if root.tag == ns.CII_ROOT:
        structured = _structured(Kind.HYBRID_PDF, Syntax.CII, root, embedded)
        return replace(structured, has_text_layer=facts.has_text_layer, page_count=facts.page_count)
    if etree.QName(root).namespace == ns.ZUGFERD1:
        return replace(facts, kind=Kind.LEGACY_ZUGFERD1, xml=embedded)
    notes = ["The embedded XML is not a CII invoice"]
    return replace(facts, kind=Kind.HYBRID_PDF_UNSUPPORTED, xml=embedded, notes=notes)


def detect(data: bytes, filename: str) -> Detection:
    """Decide what a received file is. The file name is never used to decide (HANDOFF D1-D5)."""
    if pdf.is_pdf(data):  # D1
        return _detect_pdf(data)
    if strip_leading(data).startswith(b"<"):  # D4
        root = parse_xml(data)
        if root.tag in (ns.UBL_INVOICE_ROOT, ns.UBL_CREDIT_NOTE_ROOT):
            return _structured(Kind.XML, Syntax.UBL, root, data)
        if root.tag == ns.CII_ROOT:
            return _structured(Kind.XML, Syntax.CII, root, data)
        raise UnsupportedFileError("XML whose root is not a UBL or CII invoice")
    raise UnsupportedFileError("neither a PDF nor XML")  # D5


_STRUCTURED_LABEL_PREFIX = {Profile.XRECHNUNG: "XRechnung", Profile.EN16931: "EN 16931"}
_FIXED_LABELS = {
    Kind.LEGACY_ZUGFERD1: "ZUGFeRD 1",
    Kind.HYBRID_PDF_UNSUPPORTED: "Hybrid PDF (unsupported)",
    Kind.PDF_TEXT: "Plain PDF",
    Kind.PDF_NO_TEXT: "Scanned PDF",
}


def format_label(detection: Detection, type_code: int | None) -> str:
    """The label shown in the UI, written to the CSV and used by the ?format= filter."""
    if detection.kind is Kind.XML:
        prefix = _STRUCTURED_LABEL_PREFIX.get(detection.profile or Profile.UNKNOWN, "XML")
        syntax = (detection.syntax or Syntax.UBL).value.upper()
        label = f"{prefix} · {syntax}"
    elif detection.kind is Kind.HYBRID_PDF:
        profile = detection.profile or Profile.UNKNOWN
        shown = "unknown profile" if profile is Profile.UNKNOWN else profile.value
        label = f"ZUGFeRD · {shown}"
    else:
        label = _FIXED_LABELS[detection.kind]
    if type_code == CREDIT_NOTE_TYPE_CODE or detection.ubl_credit_note:
        label += " · credit note"
    return label
