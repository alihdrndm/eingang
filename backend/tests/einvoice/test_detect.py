from pathlib import Path

import pytest

from einvoice.detect import (
    Detection,
    Kind,
    Profile,
    Syntax,
    detect,
    format_label,
    profile_from_spec_id,
)
from einvoice.errors import UnsafeXmlError, UnsupportedFileError
from tests.fixtures import xml
from tests.fixtures.pdfs import LONG_TEXT, blank_pdf, text_pdf, with_attachment

# --- Rules D1-D5 ---------------------------------------------------------------


def test_D1_pdf_magic_bytes_decide_pdf_whatever_the_file_name() -> None:
    detection = detect(text_pdf([LONG_TEXT]), "invoice.xml")
    assert detection.kind is Kind.PDF_TEXT
    assert detection.page_count == 1


def test_D1_embedded_xml_found_through_the_fallback_name() -> None:
    pdf = with_attachment(text_pdf([LONG_TEXT]), "xrechnung.xml", xml.cii())
    assert detect(pdf, "a.pdf").kind is Kind.HYBRID_PDF


def test_D2_cii_root_is_a_hybrid_pdf() -> None:
    pdf = with_attachment(text_pdf([LONG_TEXT]), "factur-x.xml", xml.cii())
    detection = detect(pdf, "a.pdf")
    assert detection.kind is Kind.HYBRID_PDF
    assert detection.syntax is Syntax.CII
    assert detection.profile is Profile.XRECHNUNG
    assert detection.xml == xml.cii()
    assert detection.has_text_layer is True


def test_D2_zugferd1_root_is_legacy() -> None:
    pdf = with_attachment(text_pdf([LONG_TEXT]), "ZUGFeRD-invoice.xml", xml.zugferd1())
    detection = detect(pdf, "a.pdf")
    assert detection.kind is Kind.LEGACY_ZUGFERD1
    assert detection.syntax is None


def test_D2_any_other_root_is_an_unsupported_hybrid_pdf() -> None:
    pdf = with_attachment(text_pdf([LONG_TEXT]), "factur-x.xml", xml.ubl())
    detection = detect(pdf, "a.pdf")
    assert detection.kind is Kind.HYBRID_PDF_UNSUPPORTED
    assert detection.notes == ["The embedded XML is not a CII invoice"]


def test_D2_malformed_embedded_xml_is_an_unsupported_hybrid_pdf() -> None:
    pdf = with_attachment(text_pdf([LONG_TEXT]), "xrechnung.xml", b"<rsm:Cross")
    detection = detect(pdf, "a.pdf")
    assert detection.kind is Kind.HYBRID_PDF_UNSUPPORTED
    assert detection.notes == ["The embedded XML is not well-formed"]


def test_xxe_in_hybrid_pdf_detection_is_refused(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    payload = (
        f'<!DOCTYPE r [<!ENTITY x SYSTEM "{secret.as_uri()}">]>'
        f'<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:'
        f'CrossIndustryInvoice:100">&x;</rsm:CrossIndustryInvoice>'
    ).encode()
    pdf = with_attachment(text_pdf([LONG_TEXT]), "factur-x.xml", payload)
    with pytest.raises(UnsafeXmlError):
        detect(pdf, "a.pdf")


def test_D3_enough_text_is_a_text_pdf() -> None:
    detection = detect(text_pdf([LONG_TEXT]), "a.pdf")
    assert detection.kind is Kind.PDF_TEXT
    assert detection.has_text_layer is True
    assert detection.xml is None
    assert detection.spec_id is None


def test_D3_fewer_than_200_characters_is_a_scan() -> None:
    detection = detect(text_pdf([["x" * 199]]), "a.pdf")
    assert detection.kind is Kind.PDF_NO_TEXT
    assert detection.has_text_layer is False
    assert detect(text_pdf([["x" * 200]]), "a.pdf").kind is Kind.PDF_TEXT


def test_D3_only_the_first_six_pages_count() -> None:
    pages: list[list[str]] = [[] for _ in range(6)] + [["x" * 300]]
    assert detect(text_pdf(pages), "a.pdf").kind is Kind.PDF_NO_TEXT


def test_D3_scan_without_any_text_layer() -> None:
    detection = detect(blank_pdf(3), "scan.pdf")
    assert detection.kind is Kind.PDF_NO_TEXT
    assert detection.page_count == 3


def test_D4_ubl_invoice() -> None:
    detection = detect(xml.ubl(), "a.pdf")
    assert (detection.kind, detection.syntax) == (Kind.XML, Syntax.UBL)
    assert detection.page_count is None
    assert detection.has_text_layer is None


def test_D4_ubl_credit_note() -> None:
    detection = detect(xml.ubl(credit_note=True), "a.xml")
    assert detection.syntax is Syntax.UBL
    assert detection.ubl_credit_note is True


def test_D4_cii_with_bom_and_leading_whitespace() -> None:
    detection = detect(b"\xef\xbb\xbf\r\n  " + xml.cii(), "a.xml")
    assert (detection.kind, detection.syntax) == (Kind.XML, Syntax.CII)


def test_D4_other_root_is_unsupported() -> None:
    with pytest.raises(UnsupportedFileError, match="root"):
        detect(xml.zugferd1(), "a.xml")


def test_D4_xxe_upload_is_refused() -> None:
    with pytest.raises(UnsafeXmlError):
        detect(b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>', "a.xml")


@pytest.mark.parametrize("data", [b"", b"PK\x03\x04zip", b"hello", b"\x89PNG\r\n"])
def test_D5_anything_else_is_unsupported(data: bytes) -> None:
    with pytest.raises(UnsupportedFileError):
        detect(data, "invoice.pdf")


# --- Profile from BT-24 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("spec_id", "version"),
    [
        ("urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0", "3.0"),
        ("urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0.2", "3.0.2"),
    ],
)
def test_profile_xrechnung(spec_id: str, version: str) -> None:
    assert profile_from_spec_id(spec_id) == (Profile.XRECHNUNG, version)


def test_profile_xrechnung_1_and_2() -> None:
    spec_id = "urn:cen.eu:en16931:2017#compliant#urn:xoev-de:kosit:standard:xrechnung_2.3"
    assert profile_from_spec_id(spec_id) == (Profile.XRECHNUNG, "2.3")


@pytest.mark.parametrize("spec_id", ["urn:factur-x.eu:1p0:minimum", "urn:zugferd.de:2p0:minimum"])
def test_profile_minimum(spec_id: str) -> None:
    assert profile_from_spec_id(spec_id) == (Profile.MINIMUM, None)


@pytest.mark.parametrize("spec_id", ["urn:factur-x.eu:1p0:basicwl", "urn:zugferd.de:2p0:basicwl"])
def test_profile_basic_wl(spec_id: str) -> None:
    assert profile_from_spec_id(spec_id) == (Profile.BASIC_WL, None)


@pytest.mark.parametrize(
    "spec_id",
    [
        "urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic",
        "urn:cen.eu:en16931:2017#compliant#urn:zugferd.de:2p0:basic",
    ],
)
def test_profile_basic(spec_id: str) -> None:
    assert profile_from_spec_id(spec_id) == (Profile.BASIC, None)


@pytest.mark.parametrize(
    "spec_id",
    [
        "urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended",
        "urn:cen.eu:en16931:2017#conformant#urn:zugferd.de:2p0:extended",
    ],
)
def test_profile_extended(spec_id: str) -> None:
    assert profile_from_spec_id(spec_id) == (Profile.EXTENDED, None)


@pytest.mark.parametrize(
    "spec_id",
    [
        "urn:cen.eu:en16931:2017",
        "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0",
    ],
)
def test_profile_en16931(spec_id: str) -> None:
    assert profile_from_spec_id(spec_id) == (Profile.EN16931, None)


@pytest.mark.parametrize(
    "spec_id",
    [
        "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:selfbilling:3.0",
        "urn:cen.eu:en16931:2017#compliant#urn:x",
        "urn:cen.eu:en16931:2017#conformant#urn:example:extension",
    ],
)
def test_profile_en16931_any_other_cius_or_extension(spec_id: str) -> None:
    assert profile_from_spec_id(spec_id) == (Profile.EN16931, None)


def test_profile_en16931_peppol_self_billing_is_an_einvoice() -> None:
    spec_id = "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:selfbilling:3.0"
    detection = detect(xml.ubl(spec_id), "a.xml")
    assert detection.profile is Profile.EN16931
    assert detection.notes == []
    assert detection.is_einvoice


@pytest.mark.parametrize(
    "spec_id",
    [
        None,
        "",
        "urn:example:something",
        "urn:cen.eu:en16931:2017#compliant#",
        "urn:cen.eu:en16931:2017:compliant:factur-x.eu:1p0:basic",
    ],
)
def test_profile_unknown(spec_id: str | None) -> None:
    assert profile_from_spec_id(spec_id) == (Profile.UNKNOWN, None)


def test_profile_order_basic_wl_is_not_mistaken_for_basic() -> None:
    assert profile_from_spec_id("urn:factur-x.eu:1p0:basicwl")[0] is Profile.BASIC_WL


def test_profile_unknown_adds_a_note_and_is_not_an_einvoice() -> None:
    detection = detect(xml.ubl("urn:example:other"), "a.xml")
    assert detection.profile is Profile.UNKNOWN
    assert detection.notes == ["Unknown specification identifier"]
    assert not detection.is_einvoice


def test_profile_missing_spec_id_is_unknown() -> None:
    assert detect(xml.cii(None), "a.xml").profile is Profile.UNKNOWN


@pytest.mark.parametrize(
    ("profile", "counts"),
    [
        (Profile.XRECHNUNG, True),
        (Profile.EN16931, True),
        (Profile.BASIC, True),
        (Profile.EXTENDED, True),
        (Profile.MINIMUM, False),
        (Profile.BASIC_WL, False),
        (Profile.UNKNOWN, False),
    ],
)
def test_profile_counts_as_einvoice(profile: Profile, counts: bool) -> None:
    assert Detection(kind=Kind.XML, profile=profile).is_einvoice is counts
    assert Detection(kind=Kind.HYBRID_PDF, profile=profile).is_einvoice is counts


@pytest.mark.parametrize(
    "kind", [Kind.PDF_TEXT, Kind.PDF_NO_TEXT, Kind.LEGACY_ZUGFERD1, Kind.HYBRID_PDF_UNSUPPORTED]
)
def test_pdfs_without_cii_are_not_einvoices(kind: Kind) -> None:
    assert not Detection(kind=kind).is_einvoice


# --- Format labels --------------------------------------------------------------


def label(kind: Kind, profile: Profile | None = None, syntax: Syntax | None = None) -> str:
    return format_label(Detection(kind=kind, profile=profile, syntax=syntax), None)


def test_format_label_xml_xrechnung() -> None:
    assert label(Kind.XML, Profile.XRECHNUNG, Syntax.UBL) == "XRechnung · UBL"
    assert label(Kind.XML, Profile.XRECHNUNG, Syntax.CII) == "XRechnung · CII"


def test_format_label_xml_en16931() -> None:
    assert label(Kind.XML, Profile.EN16931, Syntax.UBL) == "EN 16931 · UBL"
    assert label(Kind.XML, Profile.EN16931, Syntax.CII) == "EN 16931 · CII"


@pytest.mark.parametrize("profile", [Profile.BASIC, Profile.EXTENDED, Profile.UNKNOWN])
def test_format_label_xml_other_profile(profile: Profile) -> None:
    assert label(Kind.XML, profile, Syntax.UBL) == "XML · UBL"
    assert label(Kind.XML, profile, Syntax.CII) == "XML · CII"


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (Profile.EN16931, "ZUGFeRD · EN 16931"),
        (Profile.BASIC_WL, "ZUGFeRD · BASIC WL"),
        (Profile.MINIMUM, "ZUGFeRD · MINIMUM"),
        (Profile.XRECHNUNG, "ZUGFeRD · XRECHNUNG"),
        (Profile.UNKNOWN, "ZUGFeRD · unknown profile"),
    ],
)
def test_format_label_hybrid_pdf(profile: Profile, expected: str) -> None:
    assert label(Kind.HYBRID_PDF, profile, Syntax.CII) == expected


def test_format_label_legacy_zugferd1() -> None:
    assert label(Kind.LEGACY_ZUGFERD1) == "ZUGFeRD 1"


def test_format_label_hybrid_pdf_unsupported() -> None:
    assert label(Kind.HYBRID_PDF_UNSUPPORTED) == "Hybrid PDF (unsupported)"


def test_format_label_pdf_text() -> None:
    assert label(Kind.PDF_TEXT) == "Plain PDF"


def test_format_label_pdf_no_text() -> None:
    assert label(Kind.PDF_NO_TEXT) == "Scanned PDF"


def test_format_label_credit_note_by_type_code_381() -> None:
    detection = Detection(kind=Kind.XML, profile=Profile.XRECHNUNG, syntax=Syntax.CII)
    assert format_label(detection, 381) == "XRechnung · CII · credit note"
    assert format_label(detection, 380) == "XRechnung · CII"


@pytest.mark.parametrize("code", [261, 262, 396, 532])
def test_format_label_credit_note_by_any_credit_note_type_code(code: int) -> None:
    detection = Detection(kind=Kind.XML, profile=Profile.EN16931, syntax=Syntax.UBL)
    assert format_label(detection, code) == "EN 16931 · UBL · credit note"


def test_format_label_credit_note_by_ubl_root() -> None:
    detection = detect(xml.ubl(credit_note=True), "a.xml")
    assert format_label(detection, None) == "XRechnung · UBL · credit note"
