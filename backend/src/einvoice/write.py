"""CanonicalInvoice → UBL 2.1 or CII D16B, the reverse of the parsers.

Used to build the sandbox samples (M2) and in round-trip tests. Elements are written
in the order the XML schemas require; the structure follows the corpus reference files
XML-Rechnung/UBL/XRECHNUNG_Einfach.ubl.xml and XML-Rechnung/CII/XRECHNUNG_Einfach.cii.xml.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from lxml import etree

from einvoice import namespaces as ns
from einvoice.model import CanonicalInvoice, Line, Party

XRECHNUNG_3_ID = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
# BT-23, required by XRechnung (PEPPOL-EN16931-R001); the value the corpus reference files use.
PEPPOL_BILLING_PROCESS = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
DEFAULT_TYPE_CODE = 380
SEPA_CREDIT_TRANSFER = "58"


@dataclass(frozen=True)
class ElectronicAddress:
    value: str
    scheme: str = "EM"  # EAS code; EM = email address


@dataclass(frozen=True)
class WriteOptions:
    """What XRechnung requires but the canonical model does not hold."""

    customization_id: str = XRECHNUNG_3_ID  # UBL BT-24; CII takes it as `guideline_id`
    business_process: str | None = PEPPOL_BILLING_PROCESS  # BT-23
    seller_contact_name: str | None = None  # BG-6
    seller_contact_phone: str | None = None
    seller_electronic_address: ElectronicAddress | None = None  # BT-34
    buyer_electronic_address: ElectronicAddress | None = None  # BT-49
    payment_means_code: str = SEPA_CREDIT_TRANSFER  # BT-81
    # BT-120 per VAT category code, for categories such as AE or E that need a reason.
    tax_exemption_reasons: dict[str, str] = field(default_factory=dict)


def _number(value: Decimal) -> str:
    return format(value, "f")


def _child(
    parent: etree._Element, tag: str, value: str | None = None, **attributes: str
) -> etree._Element:
    element = etree.SubElement(parent, tag, attrib=attributes or None)
    if value is not None:
        element.text = value
    return element


def _optional(
    parent: etree._Element, tag: str, value: str | Decimal | None, **attributes: str
) -> None:
    if value is None:
        return
    _child(parent, tag, _number(value) if isinstance(value, Decimal) else value, **attributes)


# --- UBL -------------------------------------------------------------------------

CBC = f"{{{ns.CBC}}}"
CAC = f"{{{ns.CAC}}}"


def _ubl_amount(parent: etree._Element, tag: str, value: Decimal | None, currency: str) -> None:
    _optional(parent, f"{CBC}{tag}", value, currencyID=currency)


def _ubl_tax_scheme(parent: etree._Element) -> None:
    _child(_child(parent, f"{CAC}TaxScheme"), f"{CBC}ID", "VAT")


def _ubl_party(
    parent: etree._Element,
    party: Party,
    address: ElectronicAddress | None,
    *,
    is_seller: bool,
    contact_name: str | None = None,
    contact_phone: str | None = None,
) -> None:
    element = _child(parent, f"{CAC}Party")
    if address is not None:
        _child(element, f"{CBC}EndpointID", address.value, schemeID=address.scheme)
    postal = _child(element, f"{CAC}PostalAddress")
    _optional(postal, f"{CBC}StreetName", party.street)
    _optional(postal, f"{CBC}CityName", party.city)
    _optional(postal, f"{CBC}PostalZone", party.postcode)
    if party.country_code is not None:
        _child(_child(postal, f"{CAC}Country"), f"{CBC}IdentificationCode", party.country_code)
    # BT-32 (tax number) exists for the seller only; a buyer's is never written.
    if is_seller and party.tax_number is not None:
        scheme = _child(element, f"{CAC}PartyTaxScheme")
        _child(scheme, f"{CBC}CompanyID", party.tax_number)
        _child(_child(scheme, f"{CAC}TaxScheme"), f"{CBC}ID", "FC")
    if party.vat_id is not None:
        scheme = _child(element, f"{CAC}PartyTaxScheme")
        _child(scheme, f"{CBC}CompanyID", party.vat_id)
        _ubl_tax_scheme(scheme)
    legal = _child(element, f"{CAC}PartyLegalEntity")
    _optional(legal, f"{CBC}RegistrationName", party.name)
    if contact_name or contact_phone or party.email:
        contact = _child(element, f"{CAC}Contact")
        _optional(contact, f"{CBC}Name", contact_name)
        _optional(contact, f"{CBC}Telephone", contact_phone)
        _optional(contact, f"{CBC}ElectronicMail", party.email)


def _ubl_tax_category(
    parent: etree._Element,
    tag: str,
    category: str | None,
    rate: Decimal | None,
    options: WriteOptions,
) -> None:
    element = _child(parent, f"{CAC}{tag}")
    _optional(element, f"{CBC}ID", category)
    _optional(element, f"{CBC}Percent", rate)
    if category is not None and category in options.tax_exemption_reasons:
        _child(element, f"{CBC}TaxExemptionReason", options.tax_exemption_reasons[category])
    _ubl_tax_scheme(element)


def _ubl_line(
    parent: etree._Element, line: Line, credit_note: bool, currency: str, options: WriteOptions
) -> None:
    tag, quantity_tag = (
        ("CreditNoteLine", "CreditedQuantity")
        if credit_note
        else ("InvoiceLine", "InvoicedQuantity")
    )
    element = _child(parent, f"{CAC}{tag}")
    _optional(element, f"{CBC}ID", line.line_id)
    if line.quantity is not None or line.unit_code is not None:
        quantity = _child(
            element,
            f"{CBC}{quantity_tag}",
            _number(line.quantity) if line.quantity is not None else None,
        )
        if line.unit_code is not None:
            quantity.set("unitCode", line.unit_code)
    _ubl_amount(element, "LineExtensionAmount", line.net_amount, currency)
    item = _child(element, f"{CAC}Item")
    _optional(item, f"{CBC}Name", line.description)
    if line.tax_category is not None or line.tax_rate is not None:
        _ubl_tax_category(item, "ClassifiedTaxCategory", line.tax_category, line.tax_rate, options)
    if line.unit_price is not None:
        _ubl_amount(_child(element, f"{CAC}Price"), "PriceAmount", line.unit_price, currency)


def to_ubl(invoice: CanonicalInvoice, options: WriteOptions) -> bytes:
    """A UBL Invoice, or a CreditNote when type_code is a credit-note code."""
    credit_note = invoice.is_credit_note
    namespace, tag = (
        (ns.UBL_CREDIT_NOTE, "CreditNote") if credit_note else (ns.UBL_INVOICE, "Invoice")
    )
    # The None key makes the root's namespace the default one, like the reference files.
    nsmap = {None: namespace, "cac": ns.CAC, "cbc": ns.CBC}
    root = etree.Element(f"{{{namespace}}}{tag}", nsmap=nsmap)  # type: ignore[arg-type]  # lxml-stubs omits None keys
    currency = invoice.currency
    _child(root, f"{CBC}CustomizationID", options.customization_id)
    _optional(root, f"{CBC}ProfileID", options.business_process)
    _child(root, f"{CBC}ID", invoice.invoice_number)
    _child(root, f"{CBC}IssueDate", invoice.issue_date.isoformat())
    if not credit_note and invoice.due_date is not None:
        _child(root, f"{CBC}DueDate", invoice.due_date.isoformat())
    type_code = str(invoice.type_code or DEFAULT_TYPE_CODE)
    _child(root, f"{CBC}{'CreditNoteTypeCode' if credit_note else 'InvoiceTypeCode'}", type_code)
    for note in invoice.notes:
        _child(root, f"{CBC}Note", note)
    _child(root, f"{CBC}DocumentCurrencyCode", currency)
    _optional(root, f"{CBC}BuyerReference", invoice.buyer_reference)
    if invoice.order_reference is not None:
        _child(_child(root, f"{CAC}OrderReference"), f"{CBC}ID", invoice.order_reference)
    _ubl_party(
        _child(root, f"{CAC}AccountingSupplierParty"),
        invoice.seller,
        options.seller_electronic_address,
        is_seller=True,
        contact_name=options.seller_contact_name,
        contact_phone=options.seller_contact_phone,
    )
    _ubl_party(
        _child(root, f"{CAC}AccountingCustomerParty"),
        invoice.buyer,
        options.buyer_electronic_address,
        is_seller=False,
    )
    due_in_means = credit_note and invoice.due_date is not None
    if invoice.payee_iban or invoice.payee_bic or due_in_means:
        means = _child(root, f"{CAC}PaymentMeans")
        _child(means, f"{CBC}PaymentMeansCode", options.payment_means_code)
        if credit_note and invoice.due_date is not None:
            _child(means, f"{CBC}PaymentDueDate", invoice.due_date.isoformat())
        if invoice.payee_iban or invoice.payee_bic:
            account = _child(means, f"{CAC}PayeeFinancialAccount")
            _optional(account, f"{CBC}ID", invoice.payee_iban)
            if invoice.payee_bic is not None:
                branch = _child(account, f"{CAC}FinancialInstitutionBranch")
                _child(branch, f"{CBC}ID", invoice.payee_bic)
    if invoice.payment_terms is not None:
        _child(_child(root, f"{CAC}PaymentTerms"), f"{CBC}Note", invoice.payment_terms)
    if invoice.tax_total is not None or invoice.tax_breakdown:
        tax_total = _child(root, f"{CAC}TaxTotal")
        _ubl_amount(tax_total, "TaxAmount", invoice.tax_total, currency)
        for row in invoice.tax_breakdown:
            subtotal = _child(tax_total, f"{CAC}TaxSubtotal")
            _ubl_amount(subtotal, "TaxableAmount", row.taxable_amount, currency)
            _ubl_amount(subtotal, "TaxAmount", row.tax_amount, currency)
            _ubl_tax_category(subtotal, "TaxCategory", row.category, row.rate, options)
    totals = _child(root, f"{CAC}LegalMonetaryTotal")
    _ubl_amount(totals, "LineExtensionAmount", invoice.line_total, currency)
    _ubl_amount(totals, "TaxExclusiveAmount", invoice.net_total, currency)
    _ubl_amount(totals, "TaxInclusiveAmount", invoice.gross_total, currency)
    _ubl_amount(totals, "AllowanceTotalAmount", invoice.allowance_total, currency)
    _ubl_amount(totals, "ChargeTotalAmount", invoice.charge_total, currency)
    _ubl_amount(totals, "PrepaidAmount", invoice.prepaid_amount, currency)
    _ubl_amount(totals, "PayableAmount", invoice.payable_amount, currency)
    for line in invoice.lines:
        _ubl_line(root, line, credit_note, currency, options)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


# --- CII -------------------------------------------------------------------------

RSM = f"{{{ns.RSM}}}"
RAM = f"{{{ns.RAM}}}"
UDT = f"{{{ns.UDT}}}"


def _cii_date(parent: etree._Element, tag: str, value: date) -> None:
    holder = _child(parent, f"{RAM}{tag}")
    _child(holder, f"{UDT}DateTimeString", value.strftime("%Y%m%d"), format="102")


def _cii_party(
    parent: etree._Element,
    tag: str,
    party: Party,
    address: ElectronicAddress | None,
    *,
    is_seller: bool,
    contact_name: str | None = None,
    contact_phone: str | None = None,
) -> None:
    element = _child(parent, f"{RAM}{tag}")
    _optional(element, f"{RAM}Name", party.name)
    if contact_name or contact_phone or party.email:
        contact = _child(element, f"{RAM}DefinedTradeContact")
        _optional(contact, f"{RAM}PersonName", contact_name)
        if contact_phone is not None:
            phone = _child(contact, f"{RAM}TelephoneUniversalCommunication")
            _child(phone, f"{RAM}CompleteNumber", contact_phone)
        if party.email is not None:
            email = _child(contact, f"{RAM}EmailURIUniversalCommunication")
            _child(email, f"{RAM}URIID", party.email)
    if any((party.postcode, party.street, party.city, party.country_code)):
        postal = _child(element, f"{RAM}PostalTradeAddress")
        _optional(postal, f"{RAM}PostcodeCode", party.postcode)
        _optional(postal, f"{RAM}LineOne", party.street)
        _optional(postal, f"{RAM}CityName", party.city)
        _optional(postal, f"{RAM}CountryID", party.country_code)
    if address is not None:
        uri = _child(element, f"{RAM}URIUniversalCommunication")
        _child(uri, f"{RAM}URIID", address.value, schemeID=address.scheme)
    # BT-32 (tax number) exists for the seller only; a buyer's is never written.
    tax_number = party.tax_number if is_seller else None
    for scheme, value in (("FC", tax_number), ("VA", party.vat_id)):
        if value is not None:
            registration = _child(element, f"{RAM}SpecifiedTaxRegistration")
            _child(registration, f"{RAM}ID", value, schemeID=scheme)


def _cii_tax(
    parent: etree._Element, category: str | None, rate: Decimal | None, options: WriteOptions
) -> etree._Element:
    tax = _child(parent, f"{RAM}ApplicableTradeTax")
    _child(tax, f"{RAM}TypeCode", "VAT")
    if category is not None and category in options.tax_exemption_reasons:
        _child(tax, f"{RAM}ExemptionReason", options.tax_exemption_reasons[category])
    _optional(tax, f"{RAM}CategoryCode", category)
    _optional(tax, f"{RAM}RateApplicablePercent", rate)
    return tax


def _cii_line(parent: etree._Element, line: Line, options: WriteOptions) -> None:
    item = _child(parent, f"{RAM}IncludedSupplyChainTradeLineItem")
    document = _child(item, f"{RAM}AssociatedDocumentLineDocument")
    _optional(document, f"{RAM}LineID", line.line_id)
    product = _child(item, f"{RAM}SpecifiedTradeProduct")
    _optional(product, f"{RAM}Name", line.description)
    agreement = _child(item, f"{RAM}SpecifiedLineTradeAgreement")
    if line.unit_price is not None:
        price = _child(agreement, f"{RAM}NetPriceProductTradePrice")
        _child(price, f"{RAM}ChargeAmount", _number(line.unit_price))
    delivery = _child(item, f"{RAM}SpecifiedLineTradeDelivery")
    if line.quantity is not None or line.unit_code is not None:
        quantity = _child(
            delivery,
            f"{RAM}BilledQuantity",
            _number(line.quantity) if line.quantity is not None else None,
        )
        if line.unit_code is not None:
            quantity.set("unitCode", line.unit_code)
    settlement = _child(item, f"{RAM}SpecifiedLineTradeSettlement")
    if line.tax_category is not None or line.tax_rate is not None:
        _cii_tax(settlement, line.tax_category, line.tax_rate, options)
    summation = _child(settlement, f"{RAM}SpecifiedTradeSettlementLineMonetarySummation")
    _optional(summation, f"{RAM}LineTotalAmount", line.net_amount)


def to_cii(invoice: CanonicalInvoice, options: WriteOptions, guideline_id: str) -> bytes:
    """A CII CrossIndustryInvoice whose BT-24 is `guideline_id`."""
    root = etree.Element(
        f"{RSM}CrossIndustryInvoice",
        nsmap={"rsm": ns.RSM, "ram": ns.RAM, "udt": ns.UDT},
    )
    context = _child(root, f"{RSM}ExchangedDocumentContext")
    if options.business_process is not None:
        process = _child(context, f"{RAM}BusinessProcessSpecifiedDocumentContextParameter")
        _child(process, f"{RAM}ID", options.business_process)
    guideline = _child(context, f"{RAM}GuidelineSpecifiedDocumentContextParameter")
    _child(guideline, f"{RAM}ID", guideline_id)

    document = _child(root, f"{RSM}ExchangedDocument")
    _child(document, f"{RAM}ID", invoice.invoice_number)
    _child(document, f"{RAM}TypeCode", str(invoice.type_code or DEFAULT_TYPE_CODE))
    _cii_date(document, "IssueDateTime", invoice.issue_date)
    for note in invoice.notes:
        _child(_child(document, f"{RAM}IncludedNote"), f"{RAM}Content", note)

    transaction = _child(root, f"{RSM}SupplyChainTradeTransaction")
    for line in invoice.lines:
        _cii_line(transaction, line, options)

    agreement = _child(transaction, f"{RAM}ApplicableHeaderTradeAgreement")
    _optional(agreement, f"{RAM}BuyerReference", invoice.buyer_reference)
    _cii_party(
        agreement,
        "SellerTradeParty",
        invoice.seller,
        options.seller_electronic_address,
        is_seller=True,
        contact_name=options.seller_contact_name,
        contact_phone=options.seller_contact_phone,
    )
    _cii_party(
        agreement,
        "BuyerTradeParty",
        invoice.buyer,
        options.buyer_electronic_address,
        is_seller=False,
    )
    if invoice.order_reference is not None:
        order = _child(agreement, f"{RAM}BuyerOrderReferencedDocument")
        _child(order, f"{RAM}IssuerAssignedID", invoice.order_reference)

    _child(transaction, f"{RAM}ApplicableHeaderTradeDelivery")

    settlement = _child(transaction, f"{RAM}ApplicableHeaderTradeSettlement")
    _child(settlement, f"{RAM}InvoiceCurrencyCode", invoice.currency)
    if invoice.payee_iban or invoice.payee_bic:
        means = _child(settlement, f"{RAM}SpecifiedTradeSettlementPaymentMeans")
        _child(means, f"{RAM}TypeCode", options.payment_means_code)
        if invoice.payee_iban is not None:
            account = _child(means, f"{RAM}PayeePartyCreditorFinancialAccount")
            _child(account, f"{RAM}IBANID", invoice.payee_iban)
        if invoice.payee_bic is not None:
            institution = _child(means, f"{RAM}PayeeSpecifiedCreditorFinancialInstitution")
            _child(institution, f"{RAM}BICID", invoice.payee_bic)
    for row in invoice.tax_breakdown:
        tax = _child(settlement, f"{RAM}ApplicableTradeTax")
        _optional(tax, f"{RAM}CalculatedAmount", row.tax_amount)
        _child(tax, f"{RAM}TypeCode", "VAT")
        if row.category is not None and row.category in options.tax_exemption_reasons:
            _child(tax, f"{RAM}ExemptionReason", options.tax_exemption_reasons[row.category])
        _optional(tax, f"{RAM}BasisAmount", row.taxable_amount)
        _optional(tax, f"{RAM}CategoryCode", row.category)
        _optional(tax, f"{RAM}RateApplicablePercent", row.rate)
    if invoice.payment_terms is not None or invoice.due_date is not None:
        terms = _child(settlement, f"{RAM}SpecifiedTradePaymentTerms")
        _optional(terms, f"{RAM}Description", invoice.payment_terms)
        if invoice.due_date is not None:
            _cii_date(terms, "DueDateDateTime", invoice.due_date)
    summation = _child(settlement, f"{RAM}SpecifiedTradeSettlementHeaderMonetarySummation")
    _optional(summation, f"{RAM}LineTotalAmount", invoice.line_total)
    _optional(summation, f"{RAM}ChargeTotalAmount", invoice.charge_total)
    _optional(summation, f"{RAM}AllowanceTotalAmount", invoice.allowance_total)
    _optional(summation, f"{RAM}TaxBasisTotalAmount", invoice.net_total)
    _optional(summation, f"{RAM}TaxTotalAmount", invoice.tax_total, currencyID=invoice.currency)
    _child(summation, f"{RAM}GrandTotalAmount", _number(invoice.gross_total))
    _optional(summation, f"{RAM}TotalPrepaidAmount", invoice.prepaid_amount)
    _optional(summation, f"{RAM}DuePayableAmount", invoice.payable_amount)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
