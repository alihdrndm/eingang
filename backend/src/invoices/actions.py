"""Which actions a document offers, and why one is disabled (HANDOFF "allowed_actions").

The list has one entry for every action that exists in the document's current status,
whether or not this user may take it, so the UI can show disabled actions with a reason
and never decides permissions itself.
"""

from dataclasses import dataclass
from typing import Literal

from accounts.models import User
from invoices.four_eyes import FOUR_EYES_REASON, prepared_by
from invoices.models import Check, Document

Action = Literal[
    "edit_fields",
    "resolve_check",
    "mark_reviewed",
    "delete",
    "approve",
    "reject",
    "send_back",
    "reopen",
    "retry",
]
ReasonCode = Literal["FORBIDDEN_ROLE", "SANDBOX_RESTRICTED", "FOUR_EYES", "BLOCKING_CHECKS"]

Status = Document.Status
Role = User.Role

ACTIONS_BY_STATUS: dict[str, tuple[Action, ...]] = {
    Status.NEEDS_REVIEW: ("edit_fields", "resolve_check", "mark_reviewed", "delete"),
    Status.AWAITING_APPROVAL: ("approve", "reject", "send_back", "delete"),
    Status.REJECTED: ("reopen", "delete"),
    Status.APPROVED: ("delete",),
    Status.FAILED: ("retry", "delete"),
}

ACCOUNTING = frozenset({Role.ADMIN, Role.ACCOUNTANT})
DECIDING = frozenset({Role.ADMIN, Role.APPROVER})
ROLES_BY_ACTION: dict[Action, frozenset[str]] = {
    "edit_fields": ACCOUNTING,
    "resolve_check": ACCOUNTING,
    "mark_reviewed": ACCOUNTING,
    "send_back": ACCOUNTING,
    "reopen": ACCOUNTING,
    "retry": ACCOUNTING,
    "approve": DECIDING,
    "reject": DECIDING,
    "delete": frozenset({Role.ADMIN}),
}
# No document action is closed to sandbox visitors; the code stays in the reason order
# because organisation-level actions use it (members, settings).
SANDBOX_RESTRICTED_ACTIONS: frozenset[Action] = frozenset()


@dataclass(frozen=True)
class AllowedAction:
    action: Action
    enabled: bool
    reason_code: ReasonCode | None = None
    reason: str | None = None


def open_blocking_checks(document: Document) -> int:
    return Check.objects.filter(
        document=document, severity=Check.Severity.BLOCK, resolved_at__isnull=True
    ).count()


def _blocking_checks_reason(count: int) -> str:
    noun = "check" if count == 1 else "checks"
    return f"Resolve {count} blocking {noun} first."


def _disabled_reason(
    action: Action, document: Document, user: User, blocking_checks: int
) -> tuple[ReasonCode, str] | None:
    """The first reason in the spec's order (role, sandbox, four-eyes, checks), if any."""
    if user.role not in ROLES_BY_ACTION[action]:
        return "FORBIDDEN_ROLE", f"Your role ({user.role}) can't do this."
    if action in SANDBOX_RESTRICTED_ACTIONS and user.organization.is_sandbox:
        return "SANDBOX_RESTRICTED", "Not available in the sandbox."
    if (
        action in ("approve", "reject")
        and document.organization.four_eyes
        and prepared_by(document, user)
    ):
        return "FOUR_EYES", FOUR_EYES_REASON
    if action == "mark_reviewed" and blocking_checks > 0:
        return "BLOCKING_CHECKS", _blocking_checks_reason(blocking_checks)
    return None


def allowed_actions(
    document: Document, user: User, blocking_checks: int | None = None
) -> list[AllowedAction]:
    """`blocking_checks` may be passed when already known (lists), saving a query per row."""
    actions = ACTIONS_BY_STATUS.get(document.status, ())
    if not actions:
        return []
    count = open_blocking_checks(document) if blocking_checks is None else blocking_checks
    result = []
    for action in actions:
        reason = _disabled_reason(action, document, user, count)
        if reason is None:
            result.append(AllowedAction(action=action, enabled=True))
        else:
            result.append(
                AllowedAction(action=action, enabled=False, reason_code=reason[0], reason=reason[1])
            )
    return result


ResolveReason = Literal["FORBIDDEN_ROLE", "CHECK_NOT_RESOLVABLE", "INVALID_TRANSITION"]
ADMIN_ONLY_CHECKS = frozenset({"C15"})  # "accept anyway" on a failed validation


@dataclass(frozen=True)
class Resolvable:
    enabled: bool
    reason_code: ResolveReason | None = None
    reason: str | None = None


def check_resolvable(check: Check, document: Document, user: User) -> Resolvable:
    """Whether this user may resolve this check now (HTTP API: `checks/{id}/resolve`)."""
    allowed_roles = ROLES_BY_ACTION["resolve_check"]
    if check.check_id in ADMIN_ONLY_CHECKS:
        allowed_roles = frozenset({Role.ADMIN})
    if user.role not in allowed_roles:
        return Resolvable(False, "FORBIDDEN_ROLE", f"Your role ({user.role}) can't do this.")
    if check.severity == Check.Severity.INFO:
        return Resolvable(False, "CHECK_NOT_RESOLVABLE", "Information only.")
    if check.resolved_at is not None:
        return Resolvable(False, "CHECK_NOT_RESOLVABLE", "Already resolved.")
    if document.status != Status.NEEDS_REVIEW:
        return Resolvable(
            False, "INVALID_TRANSITION", "Only invoices that need review can be changed."
        )
    return Resolvable(True)
