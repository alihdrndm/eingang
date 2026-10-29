"""Who prepared a document, and so may not approve or reject it under four-eyes.

Four-eyes means a second person decides. Everyone who shaped what the approver sees counts
as the first person: whoever uploaded the file, edited its fields, resolved one of its
checks or marked it reviewed. An invoice that went straight to approval has no reviewer,
so without this the uploader could approve their own upload.
"""

from accounts.models import User
from invoices.models import Document, Event

PREPARING_EVENTS = (
    Event.Type.DOCUMENT_RECEIVED,
    Event.Type.INVOICE_FIELDS_EDITED,
    Event.Type.CHECK_RESOLVED,
    Event.Type.REVIEW_COMPLETED,
)

FOUR_EYES_REASON = "You prepared this invoice. Someone else must approve it."


def prepared_by(document: Document, user: User) -> bool:
    """Whether `user` is the reviewer or acted on the document in a preparing event."""
    if document.reviewed_by_id == user.id:
        return True
    return Event.objects.filter(
        document=document, actor_id=user.id, type__in=PREPARING_EVENTS
    ).exists()
