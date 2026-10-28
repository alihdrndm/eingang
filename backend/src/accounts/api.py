"""Sign-in, organisation and member endpoints (HANDOFF "HTTP API")."""

import secrets
from collections.abc import Mapping, Sequence
from uuid import UUID

from django.contrib.auth import authenticate, login, logout
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.exceptions import NotAuthenticated, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Organization, User
from accounts.permissions import (
    AdminOnly,
    AnyMember,
    NotInSandbox,
    sandbox_restricted,
    signed_in_user,
)
from accounts.scoping import organization_of, scoped
from accounts.serializers import (
    LoginRequestSerializer,
    MemberCreatedSerializer,
    MemberCreateSerializer,
    MemberPageSerializer,
    MemberSerializer,
    MemberUpdateSerializer,
    OrganizationSerializer,
    OrganizationUpdateSerializer,
    SessionSerializer,
    session_payload,
)
from eingang.problem import ProblemError
from eingang.throttles import GeneralThrottle, LoginThrottle
from invoices.models import Event

TAGS = ["auth"]
ORGANIZATION_TAGS = ["organization"]
MEMBER_TAGS = ["members"]


class CsrfView(APIView):
    authentication_classes = ()
    permission_classes = ()

    @extend_schema(
        responses={204: OpenApiResponse(description="Sets the csrftoken cookie.")}, tags=TAGS
    )
    @method_decorator(ensure_csrf_cookie)
    def get(self, request: Request) -> Response:
        return Response(status=204)


class LoginView(APIView):
    authentication_classes = ()
    permission_classes = ()
    throttle_classes = (GeneralThrottle, LoginThrottle)

    @extend_schema(request=LoginRequestSerializer, responses={200: SessionSerializer}, tags=TAGS)
    @method_decorator(csrf_protect)
    def post(self, request: Request) -> Response:
        payload = LoginRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = authenticate(
            request._request,
            username=payload.validated_data["email"].lower(),
            password=payload.validated_data["password"],
        )
        # Inactive users (the sandbox's sample people) are refused by Django's backend.
        if not isinstance(user, User):
            raise ProblemError(
                400,
                "INVALID_CREDENTIALS",
                "Sign-in failed",
                "The email address or password is not correct.",
            )
        login(request._request, user)
        return Response(session_payload(user))


class LogoutView(APIView):
    permission_classes = (AnyMember,)

    @extend_schema(request=None, responses={204: None}, tags=TAGS)
    def post(self, request: Request) -> Response:
        logout(request._request)
        return Response(status=204)


class MeView(APIView):
    permission_classes = (AnyMember,)

    @extend_schema(responses={200: SessionSerializer}, tags=TAGS)
    def get(self, request: Request) -> Response:
        user = signed_in_user(request)
        if user is None:
            raise NotAuthenticated
        return Response(session_payload(user))


# The one organisation setting a sandbox visitor may change.
SANDBOX_EDITABLE = frozenset({"name"})
# Settings whose values the audit trail records; for the others (a sole trader's name and
# VAT ID can identify a person) it records only that they changed.
LOGGED_SETTINGS = frozenset({"four_eyes", "duplicate_window_days", "reminder_after_days"})
# Member fields that are personal data: the audit trail names the field, never the value.
MEMBER_DETAILS = ("email", "name")


def _record(organization: Organization, actor: User, kind: str, data: dict[str, object]) -> None:
    """An organisation event (no document); members are named by their id only."""
    Event.objects.create(organization=organization, actor=actor, type=kind, data=data)


class OrganizationView(APIView):
    def get_permissions(self) -> Sequence[BasePermission]:
        if self.request.method == "PATCH":
            return (AdminOnly(),)
        return (AnyMember(),)

    @extend_schema(responses={200: OrganizationSerializer}, tags=ORGANIZATION_TAGS)
    def get(self, request: Request) -> Response:
        return Response(OrganizationSerializer(organization_of(request)).data)

    @extend_schema(
        request=OrganizationUpdateSerializer,
        responses={200: OrganizationSerializer},
        tags=ORGANIZATION_TAGS,
    )
    def patch(self, request: Request) -> Response:
        organization = organization_of(request)
        if (
            organization.is_sandbox
            and isinstance(request.data, Mapping)
            and set(request.data) - SANDBOX_EDITABLE
        ):
            raise sandbox_restricted()
        payload = OrganizationUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        changed: list[str] = []
        values: dict[str, dict[str, object]] = {}
        for field, value in payload.validated_data.items():
            old = getattr(organization, field)
            if old == value:
                continue
            changed.append(field)
            if field in LOGGED_SETTINGS:
                values[field] = {"old": old, "new": value}
            setattr(organization, field, value)
        with transaction.atomic():
            organization.save()
            if changed:
                _record(
                    organization,
                    _actor(request),
                    Event.Type.ORGANIZATION_SETTINGS_CHANGED,
                    {"fields": changed, "changes": values},
                )
        return Response(OrganizationSerializer(organization).data)


class MemberListView(APIView):
    # Role first, so a non-admin gets FORBIDDEN_ROLE and a sandbox admin SANDBOX_RESTRICTED.
    permission_classes = (AdminOnly, NotInSandbox)

    @extend_schema(
        operation_id="api_v1_members_list",
        parameters=[OpenApiParameter("page", int)],
        responses={200: MemberPageSerializer},
        tags=MEMBER_TAGS,
    )
    def get(self, request: Request) -> Response:
        members = scoped(User.objects.all(), request).order_by("name", "email")
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(members, request, view=self)
        return paginator.get_paginated_response(MemberSerializer(page, many=True).data)

    @extend_schema(
        request=MemberCreateSerializer,
        responses={201: MemberCreatedSerializer},
        tags=MEMBER_TAGS,
    )
    def post(self, request: Request) -> Response:
        payload = MemberCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        password = secrets.token_urlsafe(18)
        organization = organization_of(request)
        try:
            with transaction.atomic():
                member = User.objects.create_user(
                    payload.validated_data["email"],
                    password,
                    organization=organization,
                    role=payload.validated_data["role"],
                    name=payload.validated_data["name"],
                )
                _record(
                    organization,
                    _actor(request),
                    Event.Type.MEMBER_CREATED,
                    {"member_id": str(member.id), "role": member.role},
                )
        except IntegrityError as error:
            # Another request created the same address between the check and the insert.
            raise ValidationError(
                {"email": ["A user with this email address already exists."]}
            ) from error
        body = MemberCreatedSerializer(
            {
                "id": member.id,
                "email": member.email,
                "name": member.name,
                "role": member.role,
                "is_active": member.is_active,
                "one_time_password": password,
            }
        ).data
        return Response(body, status=201)


class MemberDetailView(APIView):
    permission_classes = (AdminOnly, NotInSandbox)

    @extend_schema(
        request=MemberUpdateSerializer,
        responses={200: MemberSerializer},
        tags=MEMBER_TAGS,
    )
    def patch(self, request: Request, member_id: UUID) -> Response:
        member = get_object_or_404(scoped(User.objects.all(), request), id=member_id)
        payload = MemberUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        changes = payload.validated_data
        if "email" in changes:
            changes["email"] = changes["email"].lower()
            taken = User.objects.filter(email__iexact=changes["email"]).exclude(id=member.id)
            if taken.exists():
                raise ValidationError({"email": ["A user with this email address already exists."]})
        if member.id == request.user.pk:
            errors: dict[str, list[str]] = {}
            if changes.get("is_active", True) is False:
                errors["is_active"] = ["You can't deactivate yourself."]
            if changes.get("role", member.role) != member.role:
                errors["role"] = ["You can't change your own role."]
            if errors:
                raise ValidationError(errors)
        events = _member_events(member, changes)
        for field, value in changes.items():
            setattr(member, field, value)
        try:
            with transaction.atomic():
                member.save()
                for kind, data in events:
                    _record(member.organization, _actor(request), kind, data)
        except IntegrityError as error:
            # Two requests raced for the same address; the database's unique index decided.
            raise ValidationError(
                {"email": ["A user with this email address already exists."]}
            ) from error
        return Response(MemberSerializer(member).data)


def _actor(request: Request) -> User:
    """The acting user of an admin-only endpoint (the permission has already checked it)."""
    user = signed_in_user(request)
    if user is None:
        raise NotAuthenticated
    return user


def _member_events(
    member: User, changes: Mapping[str, object]
) -> list[tuple[str, dict[str, object]]]:
    """The audit events of a member update, from the values before it is applied."""
    member_id = str(member.id)
    events: list[tuple[str, dict[str, object]]] = []
    details = [
        field
        for field in MEMBER_DETAILS
        if field in changes and changes[field] != getattr(member, field)
    ]
    if details:
        events.append(
            (Event.Type.MEMBER_DETAILS_CHANGED, {"member_id": member_id, "fields": details})
        )
    if "role" in changes and changes["role"] != member.role:
        events.append(
            (
                Event.Type.MEMBER_ROLE_CHANGED,
                {"member_id": member_id, "old": member.role, "new": changes["role"]},
            )
        )
    if "is_active" in changes and changes["is_active"] != member.is_active:
        kind = (
            Event.Type.MEMBER_REACTIVATED if changes["is_active"] else Event.Type.MEMBER_DEACTIVATED
        )
        events.append((kind, {"member_id": member_id}))
    return events
