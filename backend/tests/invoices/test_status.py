"""Table-driven test of the status machine: every (from, to, actor) combination."""

from itertools import product

import pytest

from accounts.models import Organization, User
from eingang.problem import ProblemError
from invoices.models import Approval, Document, Event, Invoice
from invoices.status import TRANSITIONS, processing_outcome, transition
from tests.conftest import MakeUser
from tests.factories import add_check, make_document, make_invoice

pytestmark = pytest.mark.django_db
Status = Document.Status

STATUSES = [choice for choice, _ in Status.choices]
ACTORS = ["system", "admin", "accountant", "approver", "viewer"]

# The spec's table: (from, to) -> who may trigger it.
ALLOWED: dict[tuple[str, str], set[str]] = {
    (Status.RECEIVED, Status.PROCESSING): {"system"},
    (Status.PROCESSING, Status.NEEDS_REVIEW): {"system"},
    (Status.PROCESSING, Status.AWAITING_APPROVAL): {"system"},
    (Status.PROCESSING, Status.FAILED): {"system"},
    (Status.FAILED, Status.PROCESSING): {"admin", "accountant"},
    (Status.NEEDS_REVIEW, Status.AWAITING_APPROVAL): {"admin", "accountant"},
    (Status.AWAITING_APPROVAL, Status.APPROVED): {"admin", "approver"},
    (Status.AWAITING_APPROVAL, Status.REJECTED): {"admin", "approver"},
    (Status.AWAITING_APPROVAL, Status.NEEDS_REVIEW): {"admin", "accountant"},
    (Status.REJECTED, Status.NEEDS_REVIEW): {"admin", "accountant"},
    (Status.APPROVED, Status.EXPORTED): {"system"},
}

COMBINATIONS = [
    (source, target, actor)
    for source, target, actor in product(STATUSES, STATUSES, ACTORS)
    if source != target
]


def test_the_transition_table_matches_the_spec() -> None:
    assert set(TRANSITIONS) == set(ALLOWED)


@pytest.mark.parametrize(("source", "target", "actor"), COMBINATIONS)
def test_every_transition_and_trigger(
    source: str, target: str, actor: str, organization: Organization, make_user: MakeUser
) -> None:
    document = make_document(organization, status=source)
    user: User | None = None if actor == "system" else make_user(actor)
    allowed = actor in ALLOWED.get((source, target), set())
    if not allowed:
        with pytest.raises(ProblemError) as error:
            transition(document, target, user, comment="A long enough comment")
        expected = "INVALID_TRANSITION" if (source, target) not in ALLOWED else "FORBIDDEN_ROLE"
        assert error.value.code == expected
        document.refresh_from_db()
        assert document.status == source
        return
    moved = transition(document, target, user, comment="A long enough comment")
    assert moved.status == target
    event = Event.objects.get(document=document)
    assert event.data["from"] == source
    assert event.data["to"] == target
    assert event.actor == user


def test_mark_reviewed_records_the_reviewer(
    organization: Organization, make_user: MakeUser
) -> None:
    accountant = make_user("accountant")
    document = make_document(organization, status=Status.NEEDS_REVIEW)
    assert transition(document, Status.AWAITING_APPROVAL, accountant).reviewed_by == accountant


def test_BLOCKING_CHECKS_stop_mark_reviewed(
    organization: Organization, make_user: MakeUser
) -> None:
    document = make_document(organization, status=Status.NEEDS_REVIEW)
    check = add_check(document)
    add_check(document, "C07", severity="warn")  # warnings do not block
    with pytest.raises(ProblemError) as error:
        transition(document, Status.AWAITING_APPROVAL, make_user("admin"))
    assert (error.value.status, error.value.code) == (409, "BLOCKING_CHECKS")
    assert error.value.extra["checks"] == [str(check.id)]


def test_FOUR_EYES_reviewer_cannot_decide(organization: Organization, make_user: MakeUser) -> None:
    admin = make_user("admin")
    document = make_document(organization, status=Status.AWAITING_APPROVAL, reviewed_by=admin)
    with pytest.raises(ProblemError) as error:
        transition(document, Status.APPROVED, admin)
    assert (error.value.status, error.value.code) == (403, "FOUR_EYES")
    organization.four_eyes = False
    organization.save()
    assert transition(document, Status.APPROVED, admin).status == Status.APPROVED


def test_FOUR_EYES_uploader_of_a_straight_through_invoice_cannot_decide(
    organization: Organization, make_user: MakeUser
) -> None:
    uploader = make_user("admin")
    document = make_document(organization, status=Status.AWAITING_APPROVAL)
    Event.objects.create(
        organization=organization,
        document=document,
        actor=uploader,
        type=Event.Type.DOCUMENT_RECEIVED,
        data={},
    )
    with pytest.raises(ProblemError) as error:
        transition(document, Status.APPROVED, uploader)
    assert (error.value.status, error.value.code) == (403, "FOUR_EYES")
    assert transition(document, Status.APPROVED, make_user("approver")).status == Status.APPROVED


def test_decision_creates_an_approval(organization: Organization, make_user: MakeUser) -> None:
    approver = make_user("approver")
    document = make_document(organization, status=Status.AWAITING_APPROVAL)
    transition(document, Status.REJECTED, approver, comment="Wrong cost centre")
    approval = Approval.objects.get(document=document)
    assert (approval.decision, approval.decided_by, approval.comment) == (
        "rejected",
        approver,
        "Wrong cost centre",
    )


@pytest.mark.parametrize("comment", [None, "", "abcd", "x" * 501])
def test_VALIDATION_FAILED_rejection_needs_a_comment_of_5_to_500_characters(
    comment: str | None, organization: Organization, make_user: MakeUser
) -> None:
    document = make_document(organization, status=Status.AWAITING_APPROVAL)
    with pytest.raises(ProblemError) as error:
        transition(document, Status.REJECTED, make_user("approver"), comment=comment)
    assert error.value.code == "VALIDATION_FAILED"


def test_send_back_needs_a_comment(organization: Organization, make_user: MakeUser) -> None:
    document = make_document(organization, status=Status.AWAITING_APPROVAL)
    with pytest.raises(ProblemError):
        transition(document, Status.NEEDS_REVIEW, make_user("accountant"))


def test_approval_comment_is_optional(organization: Organization, make_user: MakeUser) -> None:
    document = make_document(organization, status=Status.AWAITING_APPROVAL)
    assert transition(document, Status.APPROVED, make_user("approver")).status == Status.APPROVED


def test_failure_stores_the_reason_and_retry_clears_it(
    organization: Organization, make_user: MakeUser
) -> None:
    document = make_document(organization, status=Status.PROCESSING)
    assert transition(
        document, Status.FAILED, None, failure_reason="corrupt PDF"
    ).failure_reason == ("corrupt PDF")
    assert transition(document, Status.PROCESSING, make_user("accountant")).failure_reason is None


def test_processing_outcome(organization: Organization) -> None:
    straight = make_document(organization, status=Status.PROCESSING)
    make_invoice(straight)  # XML, e-invoice, no checks
    assert processing_outcome(straight) == Status.AWAITING_APPROVAL
    warned = make_document(organization, status=Status.PROCESSING)
    make_invoice(warned)
    add_check(warned, "C07", severity="warn")
    assert processing_outcome(warned) == Status.NEEDS_REVIEW
    llm = make_document(organization, status=Status.PROCESSING)
    Invoice.objects.filter(pk=make_invoice(llm).pk).update(extraction_method="llm")
    assert processing_outcome(llm) == Status.NEEDS_REVIEW
    not_einvoice = make_document(organization, status=Status.PROCESSING)
    Invoice.objects.filter(pk=make_invoice(not_einvoice).pk).update(is_einvoice=False)
    assert processing_outcome(not_einvoice) == Status.NEEDS_REVIEW
    info_only = make_document(organization, status=Status.PROCESSING)
    make_invoice(info_only)
    add_check(info_only, "C04", severity="info")
    assert processing_outcome(info_only) == Status.AWAITING_APPROVAL
    no_invoice = make_document(organization, status=Status.PROCESSING)
    assert processing_outcome(no_invoice) == Status.NEEDS_REVIEW
