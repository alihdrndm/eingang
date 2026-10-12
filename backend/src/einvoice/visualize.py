"""XML invoice → HTML with the official KoSIT visualisation (HANDOFF section 4).

Two steps, as the KoSIT release does it: UBL or CII → the XRechnung intermediate format
(XR), then XR → HTML. Every script is removed from the result: the page is shown in a
sandboxed iframe under a CSP without scripts, and it must also be safe on its own.
"""

from lxml import html as lxml_html

from einvoice import resources, saxon
from einvoice.detect import Detection, Kind, Profile, Syntax
from einvoice.namespaces import UBL_CREDIT_NOTE_ROOT
from einvoice.xmlsafe import parse_xml, to_text

VISUALISED_PROFILES = frozenset(
    {Profile.EN16931, Profile.XRECHNUNG, Profile.BASIC, Profile.EXTENDED}
)
LANGUAGE = "en"  # the UI is English (HANDOFF "Out of scope": internationalisation)


def applies_to(detection: Detection) -> bool:
    return (
        detection.kind in (Kind.XML, Kind.HYBRID_PDF)
        and detection.syntax is not None
        and detection.profile in VISUALISED_PROFILES
        and detection.xml is not None
    )


def _static_page(page: str) -> str:
    """Remove every script and event handler, and show all sections at once.

    The KoSIT page switches between its sections (overview, details, ...) with a script.
    Scripts never run here, so the tab bar is removed and every section is shown. The
    <noscript> warning goes too: with every section shown, nothing is limited.
    """
    document = lxml_html.document_fromstring(page)
    removed = [
        element
        for element in document.iter()
        if element.tag in ("script", "noscript") or element.get("class") == "menue"
    ]
    for element in removed:
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    for element in document.iter():
        for attribute in [str(name) for name in element.attrib]:
            if attribute.lower().startswith("on"):
                del element.attrib[attribute]
        if element.get("class") == "divHide":
            element.set("class", "divShow")
    return str(lxml_html.tostring(document, encoding="unicode", doctype="<!DOCTYPE html>"))


def to_html(xml: bytes, syntax: Syntax) -> str:
    """Render one invoice XML as a standalone HTML page without scripts."""
    root = parse_xml(xml)
    if syntax is Syntax.CII:
        first = resources.CII_TO_XR_XSL
    elif root.tag == UBL_CREDIT_NOTE_ROOT:
        first = resources.UBL_CREDIT_NOTE_TO_XR_XSL
    else:
        first = resources.UBL_INVOICE_TO_XR_XSL
    intermediate = saxon.transform(first, to_text(root))
    # The XR document is produced by the official stylesheet; it is parsed safely all the same.
    xr = to_text(parse_xml(intermediate.encode("utf-8")))
    page = saxon.transform(resources.XR_TO_HTML_XSL, xr, {"lang": LANGUAGE})
    return _static_page(page)


def visualize(detection: Detection) -> str | None:
    """The HTML page for a detected document, or None when section 4 does not apply."""
    if not applies_to(detection) or detection.xml is None or detection.syntax is None:
        return None
    return to_html(detection.xml, detection.syntax)


def warm_up() -> None:
    saxon.compile_all(
        [
            resources.UBL_INVOICE_TO_XR_XSL,
            resources.UBL_CREDIT_NOTE_TO_XR_XSL,
            resources.CII_TO_XR_XSL,
            resources.XR_TO_HTML_XSL,
        ]
    )
