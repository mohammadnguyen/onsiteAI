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
    IssueSeverity,
    IssueStatus,
    TimelineItem,
    TimelineItemType,
)
from app.schemas.timeline import (
    AttachmentConfirm,
    AttachmentUploadRequest,
    ChecklistToggle,
    IssueStatusUpdate,
    TimelineItemCreate,
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


def test_list_response_shape():
    resp = TimelineItemListResponse(items=[])
    assert resp.items == []
    assert resp.next_cursor is None
