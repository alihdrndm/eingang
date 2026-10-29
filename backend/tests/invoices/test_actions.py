import pytest

from accounts.models import Organization, User
from invoices.actions import ACTIONS_BY_STATUS, AllowedAction, allowed_actions
from invoices.models import Document, Event
from tests.conftest import MakeUser
from tests.factories import add_check, make_document

pytestmark = pytest.mark.django_db
Status = Document.Status


def actions_for(document: Document, user: User) -> dict[str, AllowedAction]:
    return {entry.action: entry for entry in allowed_actions(document, user)}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (Status.NEEDS_REVIEW, ["edit_fields", "resolve_check", "mark_reviewed", "delete"]),
        (Status.AWAITING_APPROVAL, ["approve", "reject", "send_back", "delete"]),
        (Status.REJECTED, ["reopen", "delete"]),
        (Status.APPROVED, ["delete"]),
        (Status.FAILED, ["retry", "delete"]),
        (Status.RECEIVED, []),
        (Status.PROCESSING, []),
        (Status.EXPORTED, []),
    ],
)
def test_every_action_of_the_status_is_listed(
    status: str, expected: list[str], organization: Organization, make_user: MakeUser
) -> None:
    document = make_document(organization, status=status)
    names = [entry.action for entry in allowed_actions(document, make_user("viewer"))]
    assert names == expected
    assert list(ACTIONS_BY_STATUS.get(status, ())) == expected


@pytest.mark.parametrize(
    ("role", "enabled"),
    [
        ("admin", {"edit_fields", "resolve_check", "mark_reviewed", "delete"}),
        ("accountant", {"edit_fields", "resolve_check", "mark_reviewed"}),
        ("approver", set()),
        ("viewer", set()),
    ],
)
def test_FORBIDDEN_ROLE_in_needs_review(
    role: str, enabled: set[str], organization: Organization, make_user: MakeUser
) -> None:
    user = make_user(role)
    entries = actions_for(make_document(organization), user)
    assert {name for name, entry in entries.items() if entry.enabled} == enabled
    for entry in entries.values():
        if not entry.enabled:
            assert entry.reason_code == "FORBIDDEN_ROLE"
            assert entry.reason == f"Your role ({role}) can't do this."


@pytest.mark.parametrize(
    ("role", "enabled"),
    [
        ("admin", {"approve", "reject", "send_back", "delete"}),
        ("approver", {"approve", "reject"}),
        ("accountant", {"send_back"}),
        ("viewer", set()),
    ],
)
def test_roles_in_awaiting_approval(
    role: str, enabled: set[str], organization: Organization, make_user: MakeUser
) -> None:
    document = make_document(organization, status=Status.AWAITING_APPROVAL)
    entries = actions_for(document, make_user(role))
    assert {name for name, entry in entries.items() if entry.enabled} == enabled


def test_FOUR_EYES_reviewer_cannot_decide(organization: Organization, make_user: MakeUser) -> None:
    admin = make_user("admin")
    document = make_document(organization, status=Status.AWAITING_APPROVAL, reviewed_by=admin)
    entries = actions_for(document, admin)
    for action in ("approve", "reject"):
        assert entries[action].enabled is False
        assert entries[action].reason_code == "FOUR_EYES"
        assert entries[action].reason == "You prepared this invoice. Someone else must approve it."
    assert entries["send_back"].enabled is True


@pytest.mark.parametrize(
    "event_type",
    [
        Event.Type.DOCUMENT_RECEIVED,
        Event.Type.INVOICE_FIELDS_EDITED,
        Event.Type.CHECK_RESOLVED,
        Event.Type.REVIEW_COMPLETED,
    ],
)
def test_FOUR_EYES_whoever_prepared_the_invoice_cannot_decide(
    event_type: str, organization: Organization, make_user: MakeUser
) -> None:
    # A straight-through invoice has no reviewer: its uploader must not approve it alone, and
    # neither may someone who changed its fields or cleared one of its checks.
    preparer = make_user("admin")
    other = make_user("approver")
    document = make_document(organization, status=Status.AWAITING_APPROVAL)
    Event.objects.create(
        organization=organization, document=document, actor=preparer, type=event_type, data={}
    )
    for action in ("approve", "reject"):
        assert actions_for(document, preparer)[action].reason_code == "FOUR_EYES"
        assert actions_for(document, other)[action].enabled is True


def test_FOUR_EYES_other_events_do_not_count_as_preparing(
    organization: Organization, make_user: MakeUser
) -> None:
    admin = make_user("admin")
    document = make_document(organization, status=Status.AWAITING_APPROVAL)
    Event.objects.create(
        organization=organization,
        document=document,
        actor=admin,
        type=Event.Type.REVIEW_SENT_BACK,
        data={},
    )
    assert actions_for(document, admin)["approve"].enabled is True


def test_FOUR_EYES_off_lets_the_reviewer_decide(
    organization: Organization, make_user: MakeUser
) -> None:
    organization.four_eyes = False
    organization.save()
    admin = make_user("admin")
    document = make_document(organization, status=Status.AWAITING_APPROVAL, reviewed_by=admin)
    assert actions_for(document, admin)["approve"].enabled is True


def test_BLOCKING_CHECKS_count_and_plural(organization: Organization, make_user: MakeUser) -> None:
    accountant = make_user("accountant")
    document = make_document(organization)
    add_check(document)
    entry = actions_for(document, accountant)["mark_reviewed"]
    assert (entry.enabled, entry.reason_code) == (False, "BLOCKING_CHECKS")
    assert entry.reason == "Resolve 1 blocking check first."
    add_check(document, "C06")
    add_check(document, "C07", severity="warn")  # warnings do not block
    assert actions_for(document, accountant)["mark_reviewed"].reason == (
        "Resolve 2 blocking checks first."
    )


def test_role_reason_comes_before_blocking_checks(
    organization: Organization, make_user: MakeUser
) -> None:
    document = make_document(organization)
    add_check(document)
    assert actions_for(document, make_user("viewer"))["mark_reviewed"].reason_code == (
        "FORBIDDEN_ROLE"
    )
