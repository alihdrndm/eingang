"""Response shapes of the supplier endpoints (HANDOFF "HTTP API", section 10)."""

from rest_framework import serializers

from eingang.serializers import Serializer
from invoices.models import Document


class SupplierSerializer(Serializer):
    """One row of `GET /suppliers`."""

    id = serializers.UUIDField()
    name = serializers.CharField()
    vat_id = serializers.CharField(required=False)
    invoice_count = serializers.IntegerField()
    first_seen_at = serializers.DateTimeField()
    last_seen_at = serializers.DateTimeField()


class SupplierPageSerializer(Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = SupplierSerializer(many=True)


class SupplierIbanSerializer(Serializer):
    """One IBAN of the supplier's history; `status` and `trusted` come from suppliers.trust."""

    iban = serializers.CharField()
    first_seen_invoice_id = serializers.UUIDField()
    first_seen_document_id = serializers.UUIDField()
    first_seen_invoice_number = serializers.CharField(required=False)
    first_seen_at = serializers.DateTimeField()
    last_seen_at = serializers.DateTimeField()
    trusted = serializers.BooleanField()
    status = serializers.ChoiceField(choices=["known", "confirmed", "new"])
    confirmed_by_name = serializers.CharField(required=False)
    confirmed_at = serializers.DateTimeField(required=False)
    confirmation_note = serializers.CharField(required=False)


class SupplierInvoiceSerializer(Serializer):
    """One of the supplier's invoices; `status` is the document's status."""

    document_id = serializers.UUIDField()
    invoice_number = serializers.CharField(required=False)
    # A credit-note code (einvoice.model.CREDIT_NOTE_TYPE_CODES): the amount is shown negative.
    type_code = serializers.IntegerField(required=False)
    issue_date = serializers.DateField(required=False)
    gross_total = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    currency = serializers.CharField(required=False)
    status = serializers.ChoiceField(choices=Document.Status.choices, source="document.status")
    received_at = serializers.DateTimeField(source="document.received_at")


class SupplierDetailSerializer(SupplierSerializer):
    ibans = SupplierIbanSerializer(many=True)
    invoices = SupplierInvoiceSerializer(many=True)
