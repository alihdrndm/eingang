"""Document endpoints (HTTP API: `/documents`)."""

import logging
from collections.abc import Sequence
from uuid import UUID

from django.http import Http404, HttpResponse
from django.utils.http import content_disposition_header
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.exceptions import NotAuthenticated, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import BaseParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import AdminOnly, AdminOrAccountant, AnyMember, signed_in_user
from accounts.scoping import scoped
from eingang import storage, temporal_client
from eingang.problem import ProblemError
from eingang.throttles import GeneralThrottle, UploadThrottle
from invoices import review
from invoices.models import Document
from invoices.queries import DocumentListQuerySerializer, filter_documents
from invoices.serializers import (
    DocumentDetailSerializer,
    DocumentPageSerializer,
    DocumentSummarySerializer,
    document_detail,
    document_summary,
    with_summary_data,
)
from invoices.uploads import (
    MAX_FILES_PER_REQUEST,
    SizeLimitedUploadHandler,
    file_too_large,
    store_uploads,
    too_many_files,
)

logger = logging.getLogger(__name__)
TAGS = ["documents"]


def current_user(request: Request) -> User:
    user = signed_in_user(request)
    if user is None:
        raise NotAuthenticated
    return user


def temporal_unavailable() -> ProblemError:
    return ProblemError(
        503,
        "TEMPORAL_UNAVAILABLE",
        "Processing delayed",
        "The upload was stored, but processing could not start now. It starts automatically"
        " with the daily maintenance run at 03:10 UTC.",
    )


UploadResponse = inline_serializer(
    "UploadResponse",
    {
        "created": DocumentSummarySerializer(many=True),
        "duplicates": inline_serializer(
            "DuplicateUpload",
            {"filename": serializers.CharField(), "existing_document_id": serializers.UUIDField()},
            many=True,
        ),
    },
)


class DocumentCollectionView(APIView):
    """`GET /documents` lists; `POST /documents` uploads."""

    def get_permissions(self) -> Sequence[BasePermission]:
        if self.request.method == "POST":
            return [AdminOrAccountant()]
        return [AnyMember()]

    def get_throttles(self) -> list[BaseThrottle]:
        if self.request.method == "POST":
            return [GeneralThrottle(), UploadThrottle()]
        return [GeneralThrottle()]

    def get_parsers(self) -> list[BaseParser]:
        return [MultiPartParser()]

    @extend_schema(
        parameters=[DocumentListQuerySerializer],
        responses={200: DocumentPageSerializer},
        tags=TAGS,
        operation_id="api_v1_documents_list",
    )
    def get(self, request: Request) -> Response:
        user = current_user(request)
        query = DocumentListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        documents = with_summary_data(
            filter_documents(scoped(Document.objects.all(), request), query.validated_data)
        )
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(documents, request, view=self) or []
        return paginator.get_paginated_response(
            [document_summary(document, user) for document in page]
        )

    @extend_schema(
        request=inline_serializer(
            "UploadRequest", {"files": serializers.ListField(child=serializers.FileField())}
        ),
        responses={
            201: UploadResponse,
            503: OpenApiResponse(description="TEMPORAL_UNAVAILABLE: stored, starts later"),
        },
        tags=TAGS,
    )
    def post(self, request: Request) -> Response:
        user = current_user(request)
        files = request.FILES.getlist("files")
        # invoices.middleware installed the size-limited reader before the body was parsed.
        if any(
            isinstance(handler, SizeLimitedUploadHandler) and handler.too_large
            for handler in request._request.upload_handlers
        ):
            raise file_too_large()
        if not files:
            raise ValidationError({"files": ["Send at least one file."]})
        if len(files) > MAX_FILES_PER_REQUEST:
            raise too_many_files()
        result = store_uploads(user, [(file.name or "upload", file.read()) for file in files])
        unavailable = False
        for document in result.created:
            try:
                temporal_client.start_processing(document.id)
            except temporal_client.TemporalUnavailableError:
                # The maintenance workflow starts documents still `received` later.
                logger.warning("Temporal unavailable; document %s waits", document.id)
                unavailable = True
        if unavailable:
            raise temporal_unavailable()
        documents = with_summary_data(
            scoped(Document.objects.filter(id__in=[doc.id for doc in result.created]), request)
        ).order_by("received_at", "id")
        return Response(
            {
                "created": [document_summary(document, user) for document in documents],
                "duplicates": [duplicate.__dict__ for duplicate in result.duplicates],
            },
            status=201,
        )


def not_available() -> ProblemError:
    return ProblemError(
        404, "NOT_AVAILABLE", "Not available", "This document has no such representation."
    )


def get_document(request: Request, document_id: UUID) -> Document:
    """The document, if it belongs to the user's organisation and is not deleted."""
    document = (
        with_summary_data(scoped(Document.objects.filter(deleted_at__isnull=True), request))
        .filter(id=document_id)
        .first()
    )
    if document is None:
        raise Http404
    return document


# Bytes a user uploaded are never rendered as a page from the app's origin.
DOWNLOAD_CSP = "sandbox; default-src 'none'"
VISUALIZATION_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"


def download(data: bytes, content_type: str, filename: str) -> HttpResponse:
    response = HttpResponse(data, content_type=content_type)
    response["Content-Disposition"] = content_disposition_header(True, filename)
    response["Content-Security-Policy"] = DOWNLOAD_CSP
    return response


class DocumentDetailView(APIView):
    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "DELETE":
            return [AdminOnly()]
        return [AnyMember()]

    @extend_schema(responses={200: DocumentDetailSerializer}, tags=TAGS)
    def get(self, request: Request, document_id: UUID) -> Response:
        user = current_user(request)
        return Response(document_detail(get_document(request, document_id), user))

    @extend_schema(
        responses={204: None, 409: OpenApiResponse(description="ALREADY_EXPORTED")}, tags=TAGS
    )
    def delete(self, request: Request, document_id: UUID) -> Response:
        user = current_user(request)
        document = get_document(request, document_id)
        if document.status == Document.Status.EXPORTED:
            raise ProblemError(
                409,
                "ALREADY_EXPORTED",
                "Already exported",
                "An exported invoice cannot be deleted.",
            )
        if document.status in (Document.Status.RECEIVED, Document.Status.PROCESSING):
            raise ProblemError(
                409,
                "INVALID_TRANSITION",
                "Not possible now",
                "A document cannot be deleted while it is being processed.",
            )
        review.mark_deleted(document, user)
        return Response(status=204)


class DocumentFileView(APIView):
    @extend_schema(responses={(200, "application/octet-stream"): OpenApiTypes.BINARY}, tags=TAGS)
    def get(self, request: Request, document_id: UUID) -> HttpResponse:
        document = get_document(request, document_id)
        data = storage.read(document.storage_key)
        return download(data, document.content_type, document.original_filename)


class DocumentXmlView(APIView):
    @extend_schema(responses={(200, "application/xml"): OpenApiTypes.STR}, tags=TAGS)
    def get(self, request: Request, document_id: UUID) -> HttpResponse:
        document = get_document(request, document_id)
        key = storage.derived_key(document.organization_id, document.id, "invoice.xml")
        if not storage.exists(key):
            raise not_available()
        return download(storage.read(key), "application/xml", f"{document.id}.xml")


class DocumentTextView(APIView):
    @extend_schema(responses={(200, "text/plain"): OpenApiTypes.STR}, tags=TAGS)
    def get(self, request: Request, document_id: UUID) -> HttpResponse:
        document = get_document(request, document_id)
        key = storage.derived_key(document.organization_id, document.id, "text.txt")
        if not storage.exists(key):
            raise not_available()
        return download(storage.read(key), "text/plain; charset=utf-8", f"{document.id}.txt")


class DocumentVisualizationView(APIView):
    @extend_schema(responses={(200, "text/html"): OpenApiTypes.STR}, tags=TAGS)
    def get(self, request: Request, document_id: UUID) -> HttpResponse:
        document = get_document(request, document_id)
        key = storage.derived_key(document.organization_id, document.id, "visualization.html")
        if not storage.exists(key):
            raise not_available()
        response = HttpResponse(storage.read(key), content_type="text/html; charset=utf-8")
        response["Content-Security-Policy"] = VISUALIZATION_CSP
        # The app shows this page in its own sandboxed iframe (HANDOFF section 4).
        response["X-Frame-Options"] = "SAMEORIGIN"
        return response
