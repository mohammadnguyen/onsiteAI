"""PR 3 — Timeline Pydantic schema validation tests.

Pure-Pydantic (no DB): each rejection asserts ``ValidationError``, which
FastAPI surfaces as a 422 at the route boundary. Covers:

* required-field omissions,
* the issue conditional contract (title required; status defaults to
  ``open``; non-issue types reject ``status``),
* the ``exclude_unset`` conditional-spread contract on
  :class:`TimelineItemUpdate` (omitted ≠ explicit ``null``),
* the JPEG/PNG ``content_type`` whitelist,
* illegal enum values,
* GPS range bounds,
* ``from_attributes`` serialisation from ORM instances.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models import (
    AttachmentUploadStatus,
    IssueSeverity,
    IssueStatus,
    JobChecklistItem,
    TimelineAttachment,
    TimelineItem,
    TimelineItemType,
)
from app.schemas.timeline import (
    AttachmentConfirm,
    AttachmentPublic,
    AttachmentUploadRequest,
    ChecklistItemPublic,
    ChecklistToggle,
    IssueStatusUpdate,
    TimelineItemCreate,
    TimelineItemDetailPublic,
    TimelineItemListResponse,
    TimelineItemPublic,
    TimelineItemUpdate,
)

_OCCURRED = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Required-field omissions (-> 422 at the route boundary)                     #
# --------------------------------------------------------------------------- #
def test_create_requires_item_type_and_occurred_at():
    with pytest.raises(ValidationError) as exc:
        TimelineItemCreate()
    missing = {e["loc"][0] for e in exc.value.errors() if e["type"] == "missing"}
    assert missing == {"item_type", "occurred_at"}


def test_issue_status_update_requires_status():
    with pytest.raises(ValidationError):
        IssueStatusUpdate()


def test_checklist_toggle_requires_is_done():
    with pytest.raises(ValidationError):
        ChecklistToggle()


def test_upload_request_requires_filename_and_content_type():
    with pytest.raises(ValidationError) as exc:
        AttachmentUploadRequest()
    missing = {e["loc"][0] for e in exc.value.errors() if e["type"] == "missing"}
    assert missing == {"filename", "content_type"}


def test_confirm_requires_positive_byte_size():
    with pytest.raises(ValidationError):
        AttachmentConfirm()
    with pytest.raises(ValidationError):
        AttachmentConfirm(byte_size=0)
    assert AttachmentConfirm(byte_size=1).width is None


def test_confirm_rejects_values_beyond_int32_columns():
    # The columns are 32-bit Integer; overflow must 422 at the edge,
    # not error at the DB.
    with pytest.raises(ValidationError):
        AttachmentConfirm(byte_size=2**31)
    with pytest.raises(ValidationError):
        AttachmentConfirm(byte_size=1, width=2**31)
    assert AttachmentConfirm(byte_size=2**31 - 1).byte_size == 2**31 - 1


# --------------------------------------------------------------------------- #
# Issue conditional contract                                                  #
# --------------------------------------------------------------------------- #
def test_issue_requires_title():
    with pytest.raises(ValidationError, match="issue requires a title"):
        TimelineItemCreate(
            item_type=TimelineItemType.issue, occurred_at=_OCCURRED
        )


def test_issue_status_defaults_to_open():
    item = TimelineItemCreate(
        item_type=TimelineItemType.issue,
        title="Leaking pipe",
        occurred_at=_OCCURRED,
    )
    assert item.status is IssueStatus.open


def test_issue_explicit_status_kept():
    item = TimelineItemCreate(
        item_type=TimelineItemType.issue,
        title="Leaking pipe",
        status=IssueStatus.resolved,
        occurred_at=_OCCURRED,
    )
    assert item.status is IssueStatus.resolved


def test_issue_cannot_be_created_closed():
    # closed is the admin verification *transition* (resolved -> closed);
    # a born-closed issue would bypass that gate entirely.
    with pytest.raises(ValidationError, match="cannot be created closed"):
        TimelineItemCreate(
            item_type=TimelineItemType.issue,
            title="Leaking pipe",
            status=IssueStatus.closed,
            occurred_at=_OCCURRED,
        )


@pytest.mark.parametrize(
    "non_issue_type",
    [TimelineItemType.daily_note, TimelineItemType.photo],
)
def test_non_issue_rejects_status(non_issue_type):
    with pytest.raises(ValidationError, match="only valid for item_type='issue'"):
        TimelineItemCreate(
            item_type=non_issue_type,
            status=IssueStatus.open,
            occurred_at=_OCCURRED,
        )


def test_non_issue_without_status_ok():
    item = TimelineItemCreate(
        item_type=TimelineItemType.daily_note,
        body="Slab poured.",
        occurred_at=_OCCURRED,
    )
    assert item.status is None
    # severity is a Phase 2 reserved column the DB permits on any row;
    # the schema deliberately does not gate it on item_type.
    assert item.severity is None


# --------------------------------------------------------------------------- #
# TimelineItemUpdate — exclude_unset conditional-spread contract              #
# --------------------------------------------------------------------------- #
def test_update_empty_dumps_nothing():
    assert TimelineItemUpdate().model_dump(exclude_unset=True) == {}


def test_update_partial_dumps_only_set_fields():
    upd = TimelineItemUpdate(title="New title")
    assert upd.model_dump(exclude_unset=True) == {"title": "New title"}


def test_update_explicit_null_distinguished_from_omitted():
    # Explicit null = "clear the checklist link"; omitted = "don't touch".
    upd = TimelineItemUpdate(checklist_item_id=None)
    assert upd.model_dump(exclude_unset=True) == {"checklist_item_id": None}


def test_update_has_no_item_type_or_status_fields():
    # Type is immutable post-capture; status changes go through the
    # dedicated /status transition endpoint. A generic edit must not
    # carry either field.
    assert "item_type" not in TimelineItemUpdate.model_fields
    assert "status" not in TimelineItemUpdate.model_fields


def test_update_rejects_empty_title():
    with pytest.raises(ValidationError):
        TimelineItemUpdate(title="")


@pytest.mark.parametrize("field", ["occurred_at", "requires_evidence"])
def test_update_rejects_explicit_null_on_not_null_columns(field):
    # Omitted = untouched (fine); explicit null on a NOT NULL column can
    # only ever become an IntegrityError 500 downstream -> 422 here.
    with pytest.raises(ValidationError, match="cannot be set to null"):
        TimelineItemUpdate(**{field: None})
    # The same fields omitted entirely are fine.
    assert TimelineItemUpdate().model_dump(exclude_unset=True) == {}


# --------------------------------------------------------------------------- #
# Aware-datetime enforcement (occurred_at is the timeline sort key)           #
# --------------------------------------------------------------------------- #
_NAIVE = datetime(2026, 7, 6, 9, 0)  # no tzinfo


def test_create_rejects_naive_occurred_at():
    with pytest.raises(ValidationError):
        TimelineItemCreate(
            item_type=TimelineItemType.daily_note, occurred_at=_NAIVE
        )


def test_update_rejects_naive_occurred_at():
    with pytest.raises(ValidationError):
        TimelineItemUpdate(occurred_at=_NAIVE)


def test_upload_request_rejects_naive_taken_at():
    with pytest.raises(ValidationError):
        AttachmentUploadRequest(
            filename="f", content_type="image/jpeg", taken_at=_NAIVE
        )


def test_aware_datetimes_accepted():
    item = TimelineItemCreate(
        item_type=TimelineItemType.daily_note,
        occurred_at="2026-07-06T09:00:00+10:00",
    )
    assert item.occurred_at.utcoffset() is not None


# --------------------------------------------------------------------------- #
# Attachment content-type whitelist + GPS bounds                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ok", ["image/jpeg", "image/png"])
def test_upload_request_accepts_whitelisted_types(ok):
    req = AttachmentUploadRequest(filename="site.jpg", content_type=ok)
    assert req.content_type == ok


@pytest.mark.parametrize(
    "bad", ["image/gif", "image/webp", "application/pdf", "text/html", ""]
)
def test_upload_request_rejects_non_whitelisted_types(bad):
    with pytest.raises(ValidationError):
        AttachmentUploadRequest(filename="f", content_type=bad)


@pytest.mark.parametrize(
    "field,value",
    [("gps_lat", 90.01), ("gps_lat", -90.01), ("gps_lng", 180.01), ("gps_lng", -180.01)],
)
def test_upload_request_rejects_out_of_range_gps(field, value):
    with pytest.raises(ValidationError):
        AttachmentUploadRequest(
            filename="f", content_type="image/jpeg", **{field: value}
        )


def test_upload_request_accepts_valid_gps():
    req = AttachmentUploadRequest(
        filename="site.jpg",
        content_type="image/jpeg",
        gps_lat=-33.8688,
        gps_lng=151.2093,
    )
    assert req.gps_lat == pytest.approx(-33.8688)


# --------------------------------------------------------------------------- #
# Illegal enum values                                                         #
# --------------------------------------------------------------------------- #
def test_create_rejects_unknown_item_type():
    with pytest.raises(ValidationError):
        TimelineItemCreate(item_type="meeting", occurred_at=_OCCURRED)


def test_status_update_rejects_unknown_status():
    with pytest.raises(ValidationError):
        IssueStatusUpdate(status="in_progress")


def test_create_rejects_unknown_severity():
    with pytest.raises(ValidationError):
        TimelineItemCreate(
            item_type=TimelineItemType.issue,
            title="t",
            severity="urgent",
            occurred_at=_OCCURRED,
        )


def test_create_accepts_reserved_phase2_types():
    # delay/variation/inspection/completion are valid enum values today
    # (MVP UI just doesn't expose them) — the schema must not block them.
    item = TimelineItemCreate(
        item_type=TimelineItemType.delay, occurred_at=_OCCURRED
    )
    assert item.item_type is TimelineItemType.delay


# --------------------------------------------------------------------------- #
# from_attributes serialisation                                               #
# --------------------------------------------------------------------------- #
def test_public_from_orm_instance():
    """TimelineItemPublic serialises straight off an ORM instance (no DB)."""
    orm = TimelineItem(
        timeline_item_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        item_type=TimelineItemType.issue,
        title="Leaking pipe",
        body=None,
        status=IssueStatus.open,
        severity=IssueSeverity.high,
        checklist_item_id=None,
        assigned_user_id=None,
        requires_evidence=False,
        occurred_at=_OCCURRED,
        created_by=uuid.uuid4(),
        created_at=_OCCURRED,
        updated_at=_OCCURRED,
    )
    pub = TimelineItemPublic.model_validate(orm)
    assert pub.timeline_item_id == orm.timeline_item_id
    assert pub.item_type is TimelineItemType.issue
    assert pub.status is IssueStatus.open
    assert pub.severity is IssueSeverity.high
    # Not a column: from_attributes falls back to the default when the
    # source object lacks the attribute. The list service overrides it.
    assert pub.attachment_count == 0


def _orm_attachment(item_id: uuid.UUID) -> TimelineAttachment:
    return TimelineAttachment(
        attachment_id=uuid.uuid4(),
        timeline_item_id=item_id,
        storage_key="jobs/kelly/abc.jpg",
        content_type="image/jpeg",
        byte_size=384_000,
        width=1600,
        height=1200,
        taken_at=_OCCURRED,
        gps_lat=-33.8688,
        gps_lng=151.2093,
        upload_status=AttachmentUploadStatus.confirmed,
        created_by=uuid.uuid4(),
        created_at=_OCCURRED,
    )


def test_attachment_public_from_orm_instance():
    """Every AttachmentPublic field maps off the ORM row; download_url
    (not a column) falls back to its None default."""
    orm = _orm_attachment(uuid.uuid4())
    pub = AttachmentPublic.model_validate(orm)
    assert pub.attachment_id == orm.attachment_id
    assert pub.storage_key == "jobs/kelly/abc.jpg"
    assert pub.upload_status is AttachmentUploadStatus.confirmed
    assert pub.gps_lat == pytest.approx(-33.8688)
    assert pub.download_url is None


def test_checklist_item_public_from_orm_instance():
    orm = JobChecklistItem(
        checklist_item_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        label="flood test",
        phase="Waterproofing",
        sort_order=3,
        is_done=True,
        done_at=_OCCURRED,
        done_by=uuid.uuid4(),
        requires_evidence=False,
        created_at=_OCCURRED,
    )
    pub = ChecklistItemPublic.model_validate(orm)
    assert pub.label == "flood test"
    assert pub.phase == "Waterproofing"
    assert pub.sort_order == 3
    assert pub.is_done is True


def test_detail_public_with_nested_attachments():
    item_id = uuid.uuid4()
    orm = TimelineItem(
        timeline_item_id=item_id,
        job_id=uuid.uuid4(),
        item_type=TimelineItemType.photo,
        title=None,
        body=None,
        status=None,
        severity=None,
        checklist_item_id=None,
        assigned_user_id=None,
        requires_evidence=False,
        occurred_at=_OCCURRED,
        created_by=uuid.uuid4(),
        created_at=_OCCURRED,
        updated_at=_OCCURRED,
    )
    # Transient instance: assigning the collection needs no DB session
    # (mirrors what the detail service produces via selectinload).
    orm.attachments = [_orm_attachment(item_id)]
    detail = TimelineItemDetailPublic.model_validate(orm)
    assert len(detail.attachments) == 1
    assert detail.attachments[0].timeline_item_id == item_id


def test_list_response_shape():
    resp = TimelineItemListResponse(items=[])
    assert resp.items == []
    assert resp.next_cursor is None
