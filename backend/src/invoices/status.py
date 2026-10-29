"""The document status machine (HANDOFF section 9): the only allowed transitions.

Every change of `Document.status` goes through `transition`, which checks the trigger
(a role, or the system), the condition, writes the event and saves. Anything else raises
`InvalidTransition` (409 INVALID_TRANSITION).
"""

from dataclasses import dataclass

from django.db import transaction

from accounts.models import User
from eingang import clock
from eingang.problem import ProblemError
from invoices.four_eyes import FOUR_EYES_REASON, prepared_by
from invoices.models import Approval, Check, Document, Event, Invoice

Status = Document.Status
Role = User.Role
SYSTEM = None  # actor of transitions the workflow or an export performs

ACCOUNTING = frozenset({Role.ADMIN, Role.ACCOUNTANT})
DECIDING = frozenset({Role.ADMIN, Role.APPROVER})
COMMENT_MIN, COMMENT_MAX = 5, 500


@dataclass(frozen=True)
class Rule:
    roles: frozenset[str] | None  # None: only the system may trigger it
    event: str


TRANSITIONS: dict[tuple[str, str], Rule] = {
    (Status.RECEIVED, Status.PROCESSING): Rule(None, Event.Type.PROCESSING_STARTED),
    (Status.PROCESSING, Status.NEEDS_REVIEW): Rule(None, Event.Type.PROCESSING_COMPLETED),
    (Status.PROCESSING, Status.AWAITING_APPROVAL): Rule(None, Event.Type.PROCESSING_COMPLETED),
    (Status.PROCESSING, Status.FAILED): Rule(None, Event.Type.PROCESSING_FAILED),
    (Status.FAILED, Status.PROCESSING): Rule(ACCOUNTING, Event.Type.PROCESSING_STARTED),
    (Status.NEEDS_REVIEW, Status.AWAITING_APPROVAL): Rule(ACCOUNTING, Event.Type.REVIEW_COMPLETED),
    (Status.AWAITING_APPROVAL, Status.APPROVED): Rule(DECIDING, Event.Type.APPROVAL_DECIDED),
    (Status.AWAITING_APPROVAL, Status.REJECTED): Rule(DECIDING, Event.Type.APPROVAL_DECIDED),
    (Status.AWAITING_APPROVAL, Status.NEEDS_REVIEW): Rule(ACCOUNTING, Event.Type.REVIEW_SENT_BACK),
    (Status.REJECTED, Status.NEEDS_REVIEW): Rule(ACCOUNTING, Event.Type.DOCUMENT_REOPENED),
    (Status.APPROVED, Status.EXPORTED): Rule(None, Event.Type.EXPORT_CREATED),
}


class InvalidTransition(ProblemError):  # noqa: N818 - the spec names it InvalidTransition
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            409,
            "INVALID_TRANSITION",
            "Not possible now",
            f"An invoice that is {current.replace('_', ' ')} cannot become "
            f"{target.replace('_', ' ')}.",
        )


def forbidden_role(role: str) -> ProblemError:
    return ProblemError(403, "FORBIDDEN_ROLE", "Not allowed", f"Your role ({role}) can't do this.")


def blocking_checks(document: Document) -> list[Check]:
    return list(
        Check.objects.filter(
            document=document, severity=Check.Severity.BLOCK, resolved_at__isnull=True
        ).order_by("check_id", "created_at")
    )


def processing_outcome(document: Document) -> str:
    """Where a document goes when processing ends (the two `processing →` rows)."""
    open_checks = Check.objects.filter(
        document=document,
        severity__in=[Check.Severity.BLOCK, Check.Severity.WARN],
        resolved_at__isnull=True,
    ).exists()
    invoice = Invoice.objects.filter(document=document).first()
    straight_through = (
        invoice is not None
        and invoice.extraction_method == Invoice.ExtractionMethod.XML
        and invoice.is_einvoice
        and not open_checks
    )
    return Status.AWAITING_APPROVAL if straight_through else Status.NEEDS_REVIEW


def _require_comment(comment: str | None, *, required: bool) -> str:
    text = (comment or "").strip()
    if not required and not text:
        return ""
    if not COMMENT_MIN <= len(text) <= COMMENT_MAX:
        raise ProblemError(
            422,
            "VALIDATION_FAILED",
            "Validation failed",
            "One or more fields are invalid.",
            errors=[
                {
                    "path": "comment",
                    "code": "length",
                    "message": f"Write {COMMENT_MIN} to {COMMENT_MAX} characters.",
                }
            ],
        )
    return text


def _check_condition(
    document: Document, target: str, actor: User | None, comment: str | None
) -> str:
    """Raise if the transition's condition fails; return the cleaned comment."""
    current = document.status
    if (current, target) == (Status.NEEDS_REVIEW, Status.AWAITING_APPROVAL):
        blocking = blocking_checks(document)
        if blocking:
            noun = "check" if len(blocking) == 1 else "checks"
            raise ProblemError(
                409,
                "BLOCKING_CHECKS",
                "Blocking checks remain",
                f"Resolve {len(blocking)} blocking {noun} first.",
                extra={"checks": [str(check.id) for check in blocking]},
            )
    if current == Status.AWAITING_APPROVAL and target in (Status.APPROVED, Status.REJECTED):
        organization = document.organization
        if actor is not None and organization.four_eyes and prepared_by(document, actor):
            raise ProblemError(403, "FOUR_EYES", "Someone else must decide", FOUR_EYES_REASON)
        return _require_comment(comment, required=target == Status.REJECTED)
    if (current, target) == (Status.AWAITING_APPROVAL, Status.NEEDS_REVIEW):
        return _require_comment(comment, required=True)
    return (comment or "").strip()


@transaction.atomic
def transition(
    document: Document,
    target: str,
    actor: User | None,
    *,
    comment: str | None = None,
    failure_reason: str | None = None,
    event_data: dict[str, object] | None = None,
) -> Document:
    """Move `document` to `target` if the status machine allows it; write the event."""
    document = Document.objects.select_for_update().get(pk=document.pk)
    rule = TRANSITIONS.get((document.status, target))
    if rule is None:
        raise InvalidTransition(document.status, target)
    if rule.roles is None:
        if actor is not None:
            raise forbidden_role(actor.role)
    elif actor is None or actor.role not in rule.roles:
        raise forbidden_role(actor.role if actor is not None else "system")
    text = _check_condition(document, target, actor, comment)
    previous = document.status
    document.status = target
    fields = ["status", "updated_at"]
    if target == Status.FAILED:
        document.failure_reason = failure_reason or "Processing failed."
        fields.append("failure_reason")
    if previous == Status.FAILED and target == Status.PROCESSING:
        document.failure_reason = None
        fields.append("failure_reason")
    if (previous, target) == (Status.NEEDS_REVIEW, Status.AWAITING_APPROVAL):
        document.reviewed_by = actor
        fields.append("reviewed_by")
    document.save(update_fields=fields)
    if target in (Status.APPROVED, Status.REJECTED) and actor is not None:
        Approval.objects.create(
            document=document, decision=target, decided_by=actor, comment=text,
            decided_at=clock.now(),
        )  # fmt: skip
    data: dict[str, object] = {"from": previous, "to": target, **(event_data or {})}
    if text:
        data["comment"] = text
    Event.objects.create(
        organization=document.organization,
        document=document,
        actor=actor,
        type=rule.event,
        data=data,
    )
    return document
