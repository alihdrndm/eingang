"""Documents, invoices and everything recorded about them (HANDOFF "Database")."""

from collections.abc import Iterable
from typing import ClassVar, NoReturn

from django.db import models
from django.db.models import Q
from django.db.models.base import ModelBase

from accounts.models import Organization, User
from eingang.db import BaseModel


class AppendOnlyError(Exception):
    """Raised when code tries to change or delete a row of an append-only table."""


# One live (not deleted) document per file and organisation; uploads rely on its name.
LIVE_SHA256_CONSTRAINT = "documents_org_sha256_live_uniq"


class Document(BaseModel):
    class Kind(models.TextChoices):
        XML = "xml"
        HYBRID_PDF = "hybrid_pdf"
        LEGACY_ZUGFERD1 = "legacy_zugferd1"
        HYBRID_PDF_UNSUPPORTED = "hybrid_pdf_unsupported"
        PDF_TEXT = "pdf_text"
        PDF_NO_TEXT = "pdf_no_text"

    class Source(models.TextChoices):
        UPLOAD = "upload"
        EMAIL = "email"
        SAMPLE = "sample"

    class Status(models.TextChoices):
        RECEIVED = "received"
        PROCESSING = "processing"
        NEEDS_REVIEW = "needs_review"
        AWAITING_APPROVAL = "awaiting_approval"
        APPROVED = "approved"
        REJECTED = "rejected"
        EXPORTED = "exported"
        FAILED = "failed"

    class ProcessingStep(models.TextChoices):
        DETECT = "detect"
        VALIDATE = "validate"
        EXTRACT = "extract"
        CHECK = "check"
        DONE = "done"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="documents"
    )
    source = models.CharField(max_length=16, choices=Source.choices)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    storage_key = models.CharField(max_length=500)
    text_storage_key = models.CharField(max_length=500, null=True, blank=True)
    received_at = models.DateTimeField()
    sender_email = models.EmailField(null=True, blank=True)
    # Unknown until the worker has detected the file (the API process cannot detect).
    kind = models.CharField(max_length=32, choices=Kind.choices, null=True, blank=True)
    format_label = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.RECEIVED)
    processing_step = models.CharField(
        max_length=16, choices=ProcessingStep.choices, null=True, blank=True
    )
    failure_reason = models.TextField(null=True, blank=True)
    # Empty for seeded documents, which are processed without a workflow.
    workflow_id = models.CharField(max_length=200, blank=True, default="")
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "documents"
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["organization", "sha256"],
                condition=Q(deleted_at__isnull=True),
                name=LIVE_SHA256_CONSTRAINT,
            )
        ]
        indexes: ClassVar = [
            models.Index(fields=["organization", "status"], name="documents_org_status_idx")
        ]

    def __str__(self) -> str:
        return str(self.id)


class Invoice(BaseModel):
    class ExtractionMethod(models.TextChoices):
        XML = "xml"
        LLM = "llm"
        MANUAL = "manual"

    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name="invoice")
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="invoices"
    )
    syntax = models.CharField(max_length=8, null=True, blank=True)
    profile = models.CharField(max_length=32, null=True, blank=True)
    spec_id = models.CharField(max_length=255, null=True, blank=True)
    is_einvoice = models.BooleanField(default=False)
    extraction_method = models.CharField(max_length=16, choices=ExtractionMethod.choices)

    # CanonicalInvoice fields. All nullable: an invoice can start empty (scans).
    invoice_number = models.CharField(max_length=200, null=True, blank=True)
    type_code = models.IntegerField(null=True, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, null=True, blank=True)
    buyer_reference = models.CharField(max_length=200, null=True, blank=True)
    order_reference = models.CharField(max_length=200, null=True, blank=True)
    seller_name = models.CharField(max_length=500, null=True, blank=True)
    seller_vat_id = models.CharField(max_length=32, null=True, blank=True)
    seller_tax_number = models.CharField(max_length=64, null=True, blank=True)
    seller_street = models.CharField(max_length=500, null=True, blank=True)
    seller_postcode = models.CharField(max_length=32, null=True, blank=True)
    seller_city = models.CharField(max_length=200, null=True, blank=True)
    seller_country_code = models.CharField(max_length=2, null=True, blank=True)
    seller_email = models.CharField(max_length=254, null=True, blank=True)
    buyer_name = models.CharField(max_length=500, null=True, blank=True)
    buyer_vat_id = models.CharField(max_length=32, null=True, blank=True)
    buyer_tax_number = models.CharField(max_length=64, null=True, blank=True)
    buyer_street = models.CharField(max_length=500, null=True, blank=True)
    buyer_postcode = models.CharField(max_length=32, null=True, blank=True)
    buyer_city = models.CharField(max_length=200, null=True, blank=True)
    buyer_country_code = models.CharField(max_length=2, null=True, blank=True)
    buyer_email = models.CharField(max_length=254, null=True, blank=True)
    payee_iban = models.CharField(max_length=34, null=True, blank=True)
    payee_bic = models.CharField(max_length=11, null=True, blank=True)
    payment_terms = models.TextField(null=True, blank=True)
    line_total = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    allowance_total = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    charge_total = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    net_total = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    tax_total = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    gross_total = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    prepaid_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    payable_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    notes = models.JSONField(null=True, blank=True)

    tax_breakdown = models.JSONField(default=list, blank=True)
    field_confidence = models.JSONField(default=dict, blank=True)
    field_evidence = models.JSONField(default=dict, blank=True)
    text_truncated = models.BooleanField(default=False)
    # The prompt that produced an LLM extraction (`extract_invoice.v1`); null otherwise.
    prompt_version = models.CharField(max_length=64, null=True, blank=True)
    supplier = models.ForeignKey(
        "suppliers.Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    normalised_number = models.CharField(max_length=200, null=True, blank=True)

    class Meta:
        db_table = "invoices"
        indexes: ClassVar = [
            models.Index(
                fields=["organization", "supplier", "normalised_number"],
                name="invoices_org_supplier_num_idx",
            )
        ]

    def __str__(self) -> str:
        return str(self.id)


class InvoiceLine(BaseModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveIntegerField()
    line_id = models.CharField(max_length=100, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    unit_code = models.CharField(max_length=16, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    net_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    tax_category = models.CharField(max_length=8, null=True, blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "invoice_lines"
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["invoice", "position"], name="invoice_lines_invoice_pos_uniq"
            )
        ]

    def __str__(self) -> str:
        return str(self.id)


class ValidationReport(BaseModel):
    class Status(models.TextChoices):
        VALID = "valid"
        WARNINGS = "warnings"
        INVALID = "invalid"
        NOT_APPLICABLE = "not_applicable"

    document = models.OneToOneField(
        Document, on_delete=models.CASCADE, related_name="validation_report"
    )
    status = models.CharField(max_length=16, choices=Status.choices)
    engine = models.CharField(max_length=200)
    xsd_ok = models.BooleanField(null=True)  # None when nothing was validated
    issues = models.JSONField(default=list, blank=True)
    fatal_count = models.PositiveIntegerField(default=0)
    warning_count = models.PositiveIntegerField(default=0)
    ran_at = models.DateTimeField()

    class Meta:
        db_table = "validation_reports"

    def __str__(self) -> str:
        return str(self.id)


class RuleExplanation(models.Model):
    class Source(models.TextChoices):
        CURATED = "curated"
        LLM = "llm"

    rule_id = models.CharField(max_length=128, primary_key=True)
    plain_text = models.CharField(max_length=300)
    fix_hint = models.CharField(max_length=200)
    source = models.CharField(max_length=16, choices=Source.choices)
    prompt_version = models.CharField(max_length=32, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rule_explanations"

    def __str__(self) -> str:
        return self.rule_id


class Check(BaseModel):
    class Severity(models.TextChoices):
        BLOCK = "block"
        WARN = "warn"
        INFO = "info"

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="checks")
    check_id = models.CharField(max_length=3)
    code = models.CharField(max_length=40)
    severity = models.CharField(max_length=8, choices=Severity.choices)
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "checks"
        indexes: ClassVar = [
            models.Index(fields=["document", "resolved_at"], name="checks_doc_resolved_idx")
        ]

    def __str__(self) -> str:
        return str(self.id)


class Approval(BaseModel):
    class Decision(models.TextChoices):
        APPROVED = "approved"
        REJECTED = "rejected"

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="approvals")
    decision = models.CharField(max_length=16, choices=Decision.choices)
    # RESTRICT: a user with decisions cannot be deleted alone, but an organisation can.
    decided_by = models.ForeignKey(User, on_delete=models.RESTRICT)
    comment = models.TextField(blank=True, default="")
    decided_at = models.DateTimeField()

    class Meta:
        db_table = "approvals"

    def __str__(self) -> str:
        return str(self.id)


class Event(BaseModel):
    """One entry of a document's or an organisation's audit trail.

    The audit trail is evidence of who did what and when, so it is append-only: a saved
    event can never be changed or deleted through the model.
    """

    class Type(models.TextChoices):
        DOCUMENT_RECEIVED = "document.received"
        DOCUMENT_DUPLICATE_UPLOAD = "document.duplicate_upload"
        PROCESSING_STARTED = "processing.started"
        PROCESSING_STEP = "processing.step"
        PROCESSING_FAILED = "processing.failed"
        PROCESSING_COMPLETED = "processing.completed"
        INVOICE_FIELDS_EDITED = "invoice.fields_edited"
        CHECK_CREATED = "check.created"
        CHECK_RESOLVED = "check.resolved"
        REVIEW_COMPLETED = "review.completed"
        APPROVAL_DECIDED = "approval.decided"
        REVIEW_SENT_BACK = "review.sent_back"
        DOCUMENT_REOPENED = "document.reopened"
        REMINDER_SENT = "reminder.sent"
        EXPORT_CREATED = "export.created"
        DOCUMENT_DELETED = "document.deleted"
        # Organisation events: `document` is NULL.
        ORGANIZATION_SETTINGS_CHANGED = "organization.settings_changed"
        MEMBER_CREATED = "member.created"
        MEMBER_ROLE_CHANGED = "member.role_changed"
        MEMBER_DETAILS_CHANGED = "member.details_changed"
        MEMBER_DEACTIVATED = "member.deactivated"
        MEMBER_REACTIVATED = "member.reactivated"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="events")
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, null=True, blank=True, related_name="events"
    )
    # NULL means the system did it.
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    type = models.CharField(max_length=40, choices=Type.choices)
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "events"
        indexes: ClassVar = [
            models.Index(fields=["document", "created_at"], name="events_doc_created_idx")
        ]

    def __str__(self) -> str:
        return str(self.id)

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise AppendOnlyError("Events are append-only and cannot be changed.")
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(self, using: str | None = None, keep_parents: bool = False) -> NoReturn:
        raise AppendOnlyError("Events are append-only and cannot be deleted.")
