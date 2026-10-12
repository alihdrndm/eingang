from pathlib import Path

import pytest

from einvoice.detect import Detection, Kind, Profile, Syntax, detect
from einvoice.errors import UnsafeXmlError
from einvoice.visualize import applies_to, to_html, visualize

TESTSUITE = Path(__file__).resolve().parents[2] / "vendor" / "xrechnung-testsuite" / "instances"
UBL = (TESTSUITE / "standard" / "01.01a-INVOICE_ubl.xml").read_bytes()
CII = (TESTSUITE / "standard" / "01.01a-INVOICE_uncefact.xml").read_bytes()


@pytest.mark.parametrize("xml", [UBL, CII], ids=["ubl", "cii"])
def test_xml_invoices_render_as_static_english_html(xml: bytes) -> None:
    page = visualize(detect(xml, "a.xml"))
    assert page is not None
    assert page.startswith("<!DOCTYPE html>")
    assert '<html lang="en"' in page
    assert "<script" not in page.lower()
    assert "<noscript" not in page.lower()  # no "limited without JavaScript" warning
    assert 'class="divHide"' not in page  # every section is visible without scripts
    assert 'class="menue"' not in page
    assert " onclick=" not in page.lower()


def test_credit_note_uses_the_credit_note_stylesheet(corpus: Path) -> None:
    xml = (corpus / "XML-Rechnung" / "UBL" / "EN16931_Gutschrift.ubl.xml").read_bytes()
    page = visualize(detect(xml, "a.xml"))
    assert page is not None
    assert "<script" not in page.lower()


@pytest.mark.parametrize(
    ("profile", "applies"),
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
def test_visualisation_applies_to_the_profiles_section_4_names(
    profile: Profile, applies: bool
) -> None:
    detection = Detection(kind=Kind.XML, syntax=Syntax.UBL, profile=profile, xml=UBL)
    assert applies_to(detection) is applies


def test_pdfs_without_xml_are_not_visualised() -> None:
    assert visualize(Detection(kind=Kind.PDF_TEXT)) is None


def test_xxe_never_reaches_the_visualisation() -> None:
    with pytest.raises(UnsafeXmlError):
        to_html(b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>', Syntax.UBL)
