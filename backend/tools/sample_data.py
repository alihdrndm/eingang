"""The twelve fictional sandbox invoices (HANDOFF section 12, "The sample set").

Everything here is invented: companies, people, VAT IDs and IBANs. VAT IDs and IBANs
are generated with valid check digits, except the two well-known test IBANs the spec
names. `build_samples.py` turns this data into files.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from stdnum import iban, luhn
from stdnum.iso7064 import mod_11_10

from einvoice.model import CanonicalInvoice, Line, Party, TaxBreakdown
from einvoice.write import XRECHNUNG_3_ID, ElectronicAddress, WriteOptions

EN16931_ID = "urn:cen.eu:en16931:2017"
BASIC_WL_ID = "urn:factur-x.eu:1p0:basicwl"
TERMS = "Zahlbar innerhalb 30 Tagen netto"
NINETEEN = Decimal("19")
CENT = Decimal("0.01")


def german_vat_id(digits: str) -> str:
    """DE + 8 digits + the ISO 7064 MOD 11,10 check digit."""
    return f"DE{digits}{mod_11_10.calc_check_digit(digits)}"


def french_vat_id(siren_digits: str) -> str:
    """FR + key + SIREN, where the SIREN carries a Luhn check digit (stdnum.fr.tva)."""
    siren = siren_digits + luhn.calc_check_digit(siren_digits)
    key = (12 + 3 * (int(siren) % 97)) % 97
    return f"FR{key:02d}{siren}"


def account(country: str, bban: str) -> str:
    """An IBAN with valid check digits for a fictional account."""
    return country + iban.calc_check_digits(f"{country}00{bban}") + bban


KNOWN_IBAN = "DE89370400440532013000"  # well-known test IBAN (spec: S02, S11)
NEW_IBAN = "DE02120300000000202051"  # well-known test IBAN (spec: S07)

BUYER = Party(
    name="Holzwerk Brandt GmbH",
    vat_id=german_vat_id("29174605"),
    street="Sägewerkstraße 12",
    postcode="04129",
    city="Leipzig",
    country_code="DE",
)
BUYER_REFERENCE = "HB-EINKAUF-2026"
BUYER_ADDRESS = ElectronicAddress("rechnungseingang@holzwerk-brandt.example")


def seller(name: str, vat_id: str, street: str, postcode: str, city: str, email: str) -> Party:
    return Party(
        name=name,
        vat_id=vat_id,
        street=street,
        postcode=postcode,
        city=city,
        country_code="DE",
        email=email,
    )


KESSLER = seller(
    "Elektro Kessler GmbH",
    german_vat_id("31852074"),
    "Gießerstraße 7",
    "04229",
    "Leipzig",
    "buchhaltung@elektro-kessler.example",
)
NORD = seller(
    "Bürobedarf Nord KG",
    german_vat_id("27740193"),
    "Am Kanal 3",
    "21073",
    "Hamburg",
    "rechnung@buerobedarf-nord.example",
)
ALBERS = seller(
    "Spedition Albers GmbH",
    german_vat_id("14920376"),
    "Hafenweg 21",
    "01139",
    "Dresden",
    "faktura@spedition-albers.example",
)
SOMMER = seller(
    "Druckerei Sommer GmbH",
    german_vat_id("36058812"),
    "Druckereiweg 5",
    "04317",
    "Leipzig",
    "info@druckerei-sommer.example",
)
POHL = seller(
    "Hausmeisterservice Pohl",
    german_vat_id("40117295"),
    "Lindenallee 44",
    "04155",
    "Leipzig",
    "pohl@hausmeister-pohl.example",
)
STADTWERKE = seller(
    "Stadtwerke Leipzig GmbH",
    german_vat_id("18832640"),
    "Augustusplatz 1",
    "04109",
    "Leipzig",
    "abrechnung@stadtwerke-leipzig.example",
)
MOREAU = Party(
    name="Atelier Moreau SARL",
    vat_id=french_vat_id("73282932"),
    street="12 rue des Tanneurs",
    postcode="67000",
    city="Strasbourg",
    country_code="FR",
)

IBANS = {
    "kessler": account("DE", "860555921034567890"),
    "albers": account("DE", "850503000221004455"),
    "sommer": account("DE", "100100100987654321"),
    "pohl": account("DE", "860100900001234567"),
    "stadtwerke": account("DE", "860555921100223344"),
    "moreau": account("FR", "30004000031234567890143"),
}


def options(contact: str, phone: str, email: str) -> WriteOptions:
    return WriteOptions(
        customization_id=XRECHNUNG_3_ID,
        seller_contact_name=contact,
        seller_contact_phone=phone,
        seller_electronic_address=ElectronicAddress(email),
        buyer_electronic_address=BUYER_ADDRESS,
    )


def line(number: int, text: str, quantity: str, unit: str, price: str) -> Line:
    net = (Decimal(quantity) * Decimal(price)).quantize(CENT, rounding=ROUND_HALF_UP)
    return Line(
        line_id=str(number),
        description=text,
        quantity=Decimal(quantity),
        unit_code=unit,
        unit_price=Decimal(price),
        net_amount=net,
        tax_category="S",
        tax_rate=NINETEEN,
    )


def standard_invoice(
    number: str,
    issued: date,
    seller_party: Party,
    payee_iban: str,
    lines: list[Line],
    *,
    type_code: int = 380,
    buyer_reference: str | None = BUYER_REFERENCE,
    due: date | None = None,
    notes: list[str] | None = None,
) -> CanonicalInvoice:
    """An invoice at 19 % VAT whose totals follow from its lines exactly."""
    net = sum((item.net_amount or Decimal(0) for item in lines), Decimal(0))
    tax = (net * NINETEEN / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return CanonicalInvoice(
        invoice_number=number,
        type_code=type_code,
        issue_date=issued,
        due_date=due,
        currency="EUR",
        buyer_reference=buyer_reference,
        seller=seller_party,
        buyer=BUYER,
        payee_iban=payee_iban,
        payment_terms=TERMS,
        line_total=net,
        allowance_total=Decimal(0),
        charge_total=Decimal(0),
        net_total=net,
        tax_total=tax,
        gross_total=net + tax,
        prepaid_amount=Decimal(0),
        payable_amount=net + tax,
        tax_breakdown=[
            TaxBreakdown(category="S", rate=NINETEEN, taxable_amount=net, tax_amount=tax)
        ],
        lines=lines,
        notes=notes or [],
    )


Form = Literal["ubl", "cii", "hybrid", "plain_pdf", "scan"]


@dataclass(frozen=True)
class Decision:
    decision: Literal["approved", "rejected"]
    by: str
    comment: str


@dataclass(frozen=True)
class Sample:
    id: str
    form: Form
    received_offset_minutes: int  # minutes before the sandbox was created
    invoice: CanonicalInvoice
    format_label: str
    expected_checks: list[str]
    expected_status: str
    write_options: WriteOptions = field(default_factory=WriteOptions)
    guideline_id: str = XRECHNUNG_3_ID  # BT-24 of CII and hybrid samples
    facturx_level: str | None = None  # for hybrid PDFs
    printed_gross: Decimal | None = None  # what the visible PDF prints, if it differs
    expected_fatal_rules: list[str] = field(default_factory=list)
    decision: Decision | None = None

    @property
    def filename(self) -> str:
        safe_number = self.invoice.invoice_number.replace("/", "-")
        extension = "xml" if self.form in ("ubl", "cii") else "pdf"
        return f"{self.id}-{safe_number}.{extension}"


KESSLER_OPTIONS = options("Petra Kessler", "+49 341 4402210", "buchhaltung@elektro-kessler.example")
NORD_OPTIONS = options("Lars Nordmann", "+49 40 7781200", "rechnung@buerobedarf-nord.example")
ALBERS_OPTIONS = options("Ute Albers", "+49 351 8830400", "faktura@spedition-albers.example")
STADTWERKE_OPTIONS = options(
    "Kundenservice", "+49 341 1210", "abrechnung@stadtwerke-leipzig.example"
)
JONAS = "Jonas Brandt (sample)"

S01_INVOICE = standard_invoice(
    "RE-2026-0412",
    date(2026, 3, 2),
    KESSLER,
    IBANS["kessler"],
    [
        line(1, "Elektroinstallation Werkhalle", "8", "HUR", "75.00"),
        line(2, "Kabel NYM-J 3x1,5 mm², Rolle 50 m", "4", "H87", "100.00"),
    ],
)

S04_INVOICE = CanonicalInvoice(
    invoice_number="F-2026-118",
    type_code=380,
    issue_date=date(2026, 2, 26),
    currency="EUR",
    buyer_reference=BUYER_REFERENCE,
    seller=MOREAU,
    buyer=BUYER,
    payee_iban=IBANS["moreau"],
    payment_terms=TERMS,
    line_total=Decimal("640"),
    allowance_total=Decimal(0),
    charge_total=Decimal(0),
    net_total=Decimal("640"),
    tax_total=Decimal(0),
    gross_total=Decimal("640"),
    prepaid_amount=Decimal(0),
    payable_amount=Decimal("640"),
    tax_breakdown=[
        TaxBreakdown(
            category="AE", rate=Decimal(0), taxable_amount=Decimal("640"), tax_amount=Decimal(0)
        )
    ],
    notes=["Autoliquidation - Steuerschuldnerschaft des Leistungsempfängers"],
)

SAMPLES = [
    Sample(
        id="S01",
        form="ubl",
        received_offset_minutes=2550,
        invoice=S01_INVOICE,
        write_options=KESSLER_OPTIONS,
        format_label="XRechnung · UBL",
        expected_checks=["C04"],
        expected_status="awaiting_approval",
    ),
    Sample(
        id="S02",
        form="cii",
        received_offset_minutes=62,
        invoice=standard_invoice(
            "BN-88213",
            date(2026, 3, 5),
            NORD,
            KNOWN_IBAN,
            [
                line(1, "Kopierpapier A4, 80 g, Karton à 2.500 Blatt", "15", "XCT", "18.90"),
                line(2, "Ordner A4, breit, blau", "21", "H87", "3.00"),
            ],
        ),
        write_options=NORD_OPTIONS,
        format_label="XRechnung · CII",
        expected_checks=[],
        expected_status="awaiting_approval",
    ),
    Sample(
        id="S03",
        form="hybrid",
        received_offset_minutes=980,
        invoice=standard_invoice(
            "SA/26/1187",
            date(2026, 3, 3),
            ALBERS,
            IBANS["albers"],
            [
                line(1, "Transport Leipzig - Hamburg, Komplettladung", "1", "C62", "2000.00"),
                line(2, "Palettentausch Europalette", "20", "H87", "20.00"),
            ],
        ),
        write_options=ALBERS_OPTIONS,
        guideline_id=EN16931_ID,
        facturx_level="en16931",
        format_label="ZUGFeRD · EN 16931",
        expected_checks=[],
        expected_status="awaiting_approval",
    ),
    Sample(
        id="S04",
        form="hybrid",
        received_offset_minutes=1318,
        invoice=S04_INVOICE,
        write_options=WriteOptions(tax_exemption_reasons={"AE": "Autoliquidation"}),
        guideline_id=BASIC_WL_ID,
        facturx_level="basicwl",
        format_label="ZUGFeRD · BASIC WL",
        expected_checks=["C04", "C11"],
        expected_status="needs_review",
    ),
    Sample(
        id="S05",
        form="ubl",
        received_offset_minutes=1425,
        invoice=standard_invoice(
            "RE-2026-0413",
            date(2026, 3, 4),
            KESSLER,
            IBANS["kessler"],
            [line(1, "Wartung Notbeleuchtung, Jahrespauschale", "1", "C62", "200.00")],
            buyer_reference=None,
        ),
        write_options=KESSLER_OPTIONS,
        format_label="XRechnung · UBL",
        expected_checks=["C15"],
        expected_status="needs_review",
        expected_fatal_rules=["BR-DE-15"],
    ),
    Sample(
        id="S06",
        form="ubl",
        received_offset_minutes=48,
        invoice=S01_INVOICE.model_copy(update={"notes": ["Zweitschrift"]}),
        write_options=KESSLER_OPTIONS,
        format_label="XRechnung · UBL",
        expected_checks=["C02"],
        expected_status="needs_review",
    ),
    Sample(
        id="S07",
        form="cii",
        received_offset_minutes=2815,
        invoice=standard_invoice(
            "BN-88290",
            date(2026, 2, 27),
            NORD,
            NEW_IBAN,
            [line(1, "Tonerkartusche schwarz, Modell TK-4120", "2", "H87", "41.00")],
        ),
        write_options=NORD_OPTIONS,
        format_label="XRechnung · CII",
        expected_checks=["C05"],
        expected_status="needs_review",
    ),
    Sample(
        id="S08",
        form="plain_pdf",
        received_offset_minutes=3796,
        invoice=standard_invoice(
            "2026-1043",
            date(2026, 2, 20),
            SOMMER,
            IBANS["sommer"],
            [
                line(1, "Flyer A5, 4/4-farbig, 5.000 Stück", "1", "C62", "900.00"),
                line(2, "Visitenkarten 85 x 55 mm, je 500 Stück", "4", "C62", "100.00"),
            ],
        ),
        format_label="Plain PDF",
        # The precomputed extraction graded every field high, so no C13 (spec section 12).
        expected_checks=["C04", "C11"],
        expected_status="needs_review",
    ),
    Sample(
        id="S09",
        form="scan",
        received_offset_minutes=4290,
        invoice=standard_invoice(
            "HP-2026-021",
            date(2026, 2, 18),
            POHL,
            IBANS["pohl"],
            [line(1, "Hausmeisterleistungen Februar 2026", "1", "C62", "380.00")],
        ),
        format_label="Scanned PDF",
        expected_checks=["C10", "C11"],
        expected_status="needs_review",
    ),
    Sample(
        id="S10",
        form="hybrid",
        received_offset_minutes=5508,
        invoice=standard_invoice(
            "SA/26/1160",
            date(2026, 2, 24),
            ALBERS,
            IBANS["albers"],
            [line(1, "Transport Leipzig - Dresden, Teilladung", "1", "C62", "1008.40")],
        ),
        write_options=ALBERS_OPTIONS,
        guideline_id=EN16931_ID,
        facturx_level="en16931",
        printed_gross=Decimal("1190.00"),
        format_label="ZUGFeRD · EN 16931",
        # The precomputed comparison finds the printed 1190.00 against 1200.00 (C09).
        expected_checks=["C04", "C09"],
        expected_status="needs_review",
    ),
    Sample(
        id="S11",
        form="ubl",
        received_offset_minutes=10085,
        invoice=standard_invoice(
            "BN-88102-G",
            date(2026, 2, 10),
            NORD,
            KNOWN_IBAN,
            [line(1, "Gutschrift: Ordner A4 zurückgegeben", "7", "H87", "7.00")],
            type_code=381,
        ),
        write_options=NORD_OPTIONS,
        format_label="XRechnung · UBL · credit note",
        expected_checks=["C04"],
        expected_status="approved",
        decision=Decision("approved", JONAS, ""),
    ),
    Sample(
        id="S12",
        form="cii",
        received_offset_minutes=34480,
        invoice=standard_invoice(
            "SW-2026-77812",
            date(2026, 1, 12),
            STADTWERKE,
            IBANS["stadtwerke"],
            [line(1, "Strom, Abschlag Januar 2026", "1", "C62", "327.23")],
            due=date(2026, 1, 26),
        ),
        write_options=STADTWERKE_OPTIONS,
        format_label="XRechnung · CII",
        expected_checks=["C04", "C08"],
        expected_status="rejected",
        decision=Decision(
            "rejected", JONAS, "Wrong cost centre, please ask for a corrected invoice."
        ),
    ),
]
