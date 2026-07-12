"""PR 7 — attachment endpoint tests (issue → confirm → download).

No storage mocking on the happy paths: presigning is pure local HMAC
(no network), so these tests exercise REAL SigV4 URLs built against
the conftest's fake endpoint — asserting virtual-host addressing, the
signed expiry, and the storage key end-to-end. Only the 503 path
monkeypatches the storage layer.

Authz cells mirror the §4 matrix: any team member may request an
upload URL for a visible item (attaching evidence is team-wide);
confirm is uploader-or-admin; download is team-wide but only for
confirmed attachments. 404-before-403 holds for unknowable ids.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
import sqlalchemy as sa

import app.services.timeline as timeline_svc
import app.services.timeline_storage as storage_mod
from app.core.security import create_access_token, hash_password
from app.models import (
    AttachmentUploadStatus,
    Job,
    JobStatus,
    TimelineAttachment,
    TimelineItemType,
    User,
    UserRole,
)
from app.models.user import LanguageCode
from app.schemas.timeline import TimelineItemCreate

_OCCURRED = "2026-07-06T09:00:00+00:00"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _mk_job(db_session, admin) -> Job:
    job = Job(
        job_id=uuid.uuid4(),
        job_name="Kelly House",
        status=JobStatus.active,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


async def _post_item(client, token, job, *, item_type="photo") -> dict:
    r = await client.post(
        f"/jobs/{job.job_id}/timeline",
        headers=_auth(token),
        json={"item_type": item_type, "occurred_at": _OCCURRED},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _upload_body(**overrides) -> dict:
    body = {
        "filename": "site-photo.jpg",
        "content_type": "image/jpeg",
        "taken_at": "2026-07-06T08:55:00+00:00",
        "gps_lat": -33.8688,
        "gps_lng": 151.2093,
    }
    body.update(overrides)
    return body


async def _issue_upload(client, token, item_id, **overrides) -> dict:
    r = await client.post(
        f"/timeline/{item_id}/attachments",
        headers=_auth(token),
        json=_upload_body(**overrides),
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest_asyncio.fixture
async def second_contributor(db_session) -> User:
    user = User(
        user_id=uuid.uuid4(),
        full_name="Second Contributor",
        email="second-contributor@example.com",
        password_hash=hash_password("x"),
        role=UserRole.contributor,
        language_preference=LanguageCode.en,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def second_contributor_token(second_contributor) -> str:
    return create_access_token({"sub": str(second_contributor.user_id)})


# --------------------------------------------------------------------------- #
# 401                                                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,url,body",
    [
        ("post", f"/timeline/{uuid.uuid4()}/attachments", {"filename": "x.jpg"}),
        (
            "post",
            f"/timeline/attachments/{uuid.uuid4()}/confirm",
            {"byte_size": 1},
        ),
        ("get", f"/timeline/attachments/{uuid.uuid4()}", None),
    ],
)
async def test_unauthenticated_gets_401(client, method, url, body):
    kwargs = {"json": body} if body is not None else {}
    r = await getattr(client, method)(url, **kwargs)
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Issue endpoint — real presigned PUT                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_issue_returns_real_presigned_put(
    client, db_session, seeded_admin, contributor_token
):
    job = await _mk_job(db_session, seeded_admin)
    item = await _post_item(client, contributor_token, job)
    issued = await _issue_upload(client, contributor_token, item["timeline_item_id"])

    assert set(issued) == {"attachment_id", "storage_key", "presigned_url"}
    key = issued["storage_key"]
    assert key.startswith(
        f"jobs/{job.job_id}/timeline/{item['timeline_item_id']}/"
    )
    assert key.endswith("-site-photo.jpg")

    url = issued["presigned_url"]
    # Virtual-host addressing against the fake endpoint, real SigV4 markers.
    assert url.startswith("https://sitetracker-test-bucket.storage.test.invalid/")
    assert "X-Amz-Signature=" in url
    assert "X-Amz-Expires=600" in url
    assert key in url


@pytest.mark.asyncio
async def test_issue_persists_pending_row_with_evidence_metadata(
    client, db_session, seeded_admin, contributor_token, seeded_contributor
):
    job = await _mk_job(db_session, seeded_admin)
    item = await _post_item(client, contributor_token, job)
    issued = await _issue_upload(client, contributor_token, item["timeline_item_id"])

    row = (
        await db_session.execute(
            sa.select(TimelineAttachment).where(
                TimelineAttachment.attachment_id
                == uuid.UUID(issued["attachment_id"])
            )
        )
    ).scalar_one()
    assert row.upload_status.value == "pending"
    assert row.created_by == seeded_contributor.user_id
    assert row.taken_at is not None
    assert row.gps_lat == pytest.approx(-33.8688)
    assert row.byte_size is None  # dimensions arrive at confirm


@pytest.mark.asyncio
async def test_any_team_member_may_attach_to_visible_item(
    client, db_session, seeded_admin, admin_token, second_contributor_token
):
    """The issue endpoint is not creator-gated (matrix: contributor ✅)."""
    job = await _mk_job(db_session, seeded_admin)
    item = await _post_item(client, admin_token, job)  # admin's item
    issued = await _issue_upload(
        client, second_contributor_token, item["timeline_item_id"]
    )
    assert issued["attachment_id"]


@pytest.mark.asyncio
async def test_issue_404_on_ghost_and_soft_deleted_item(
    client, db_session, seeded_admin, admin_token, contributor_token
):
    r = await client.post(
        f"/timeline/{uuid.uuid4()}/attachments",
        headers=_auth(contributor_token),
        json=_upload_body(),
    )
    assert r.status_code == 404

    job = await _mk_job(db_session, seeded_admin)
    item = await _post_item(client, admin_token, job)
    r = await client.delete(
        f"/timeline/{item['timeline_item_id']}", headers=_auth(admin_token)
    )
    assert r.status_code == 204
    r = await client.post(
        f"/timeline/{item['timeline_item_id']}/attachments",
        headers=_auth(contributor_token),
        json=_upload_body(),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_issue_422_cells(client, db_session, seeded_admin, admin_token):
    job = await _mk_job(db_session, seeded_admin)
    item = await _post_item(client, admin_token, job)
    url = f"/timeline/{item['timeline_item_id']}/attachments"

    for bad in (
        _upload_body(content_type="text/html"),
        _upload_body(taken_at="2026-07-06T08:55:00"),  # naive
        _upload_body(gps_lat=91.0),
    ):
        r = await client.post(url, headers=_auth(admin_token), json=bad)
        assert r.status_code == 422, bad


@pytest.mark.asyncio
async def test_issue_503_when_storage_unconfigured(
    client, db_session, seeded_admin, admin_token, monkeypatch
):
    job = await _mk_job(db_session, seeded_admin)
    item = await _post_item(client, admin_token, job)

    def _boom(*args, **kwargs):
        raise storage_mod.StorageNotConfigured()

    monkeypatch.setattr(storage_mod, "generate_presigned_put", _boom)
    r = await client.post(
        f"/timeline/{item['timeline_item_id']}/attachments",
        headers=_auth(admin_token),
        json=_upload_body(),
    )
    assert r.status_code == 503


# --------------------------------------------------------------------------- #
# Confirm — uploader-or-admin; 404-before-403                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_confirm_flow_and_permissions(
    client,
    db_session,
    seeded_admin,
    admin_token,
    contributor_token,
    second_contributor_token,
):
    job = await _mk_job(db_session, seeded_admin)
    item = await _post_item(client, contributor_token, job)
    issued = await _issue_upload(client, contributor_token, item["timeline_item_id"])
    confirm_url = f"/timeline/attachments/{issued['attachment_id']}/confirm"
    dims = {"byte_size": 384_000, "width": 1600, "height": 1200}

    # A different contributor cannot confirm someone else's upload.
    r = await client.post(
        confirm_url, headers=_auth(second_contributor_token), json=dims
    )
    assert r.status_code == 403

    # The uploader confirms.
    r = await client.post(confirm_url, headers=_auth(contributor_token), json=dims)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["upload_status"] == "confirmed"
    assert body["byte_size"] == 384_000
    assert body["width"] == 1600

    # Weak-network retry: re-confirm is an idempotent 200.
    r = await client.post(confirm_url, headers=_auth(contributor_token), json=dims)
    assert r.status_code == 200

    # Admin may confirm anyone's upload.
    issued2 = await _issue_upload(client, contributor_token, item["timeline_item_id"])
    r = await client.post(
        f"/timeline/attachments/{issued2['attachment_id']}/confirm",
        headers=_auth(admin_token),
        json=dims,
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_confirm_404_cells(
    client, db_session, seeded_admin, admin_token, contributor_token
):
    dims = {"byte_size": 1}
    r = await client.post(
        f"/timeline/attachments/{uuid.uuid4()}/confirm",
        headers=_auth(contributor_token),
        json=dims,
    )
    assert r.status_code == 404

    # Attachment whose parent item was soft-deleted: 404, never 403 —
    # even for a non-creator caller (no-leak ordering).
    job = await _mk_job(db_session, seeded_admin)
    item = await _post_item(client, admin_token, job)
    issued = await _issue_upload(client, admin_token, item["timeline_item_id"])
    r = await client.delete(
        f"/timeline/{item['timeline_item_id']}", headers=_auth(admin_token)
    )
    assert r.status_code == 204
    r = await client.post(
        f"/timeline/attachments/{issued['attachment_id']}/confirm",
        headers=_auth(contributor_token),
        json=dims,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_confirm_422_on_bad_dimensions(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin)
    item = await _post_item(client, admin_token, job)
    issued = await _issue_upload(client, admin_token, item["timeline_item_id"])
    url = f"/timeline/attachments/{issued['attachment_id']}/confirm"

    for bad in ({"byte_size": 0}, {"byte_size": 2**31}, {}):
        r = await client.post(url, headers=_auth(admin_token), json=bad)
        assert r.status_code == 422, bad


# --------------------------------------------------------------------------- #
# Download — team-wide read, confirmed-only                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_download_confirmed_team_visible(
    client, db_session, seeded_admin, contributor_token, second_contributor_token
):
    job = await _mk_job(db_session, seeded_admin)
    item = await _post_item(client, contributor_token, job)
    issued = await _issue_upload(client, contributor_token, item["timeline_item_id"])
    r = await client.post(
        f"/timeline/attachments/{issued['attachment_id']}/confirm",
        headers=_auth(contributor_token),
        json={"byte_size": 100},
    )
    assert r.status_code == 200

    # A different contributor on the same job downloads it (team-visible).
    r = await client.get(
        f"/timeline/attachments/{issued['attachment_id']}",
        headers=_auth(second_contributor_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["upload_status"] == "confirmed"
    assert body["download_url"] is not None
    assert "X-Amz-Signature=" in body["download_url"]
    assert issued["storage_key"] in body["download_url"]


@pytest.mark.asyncio
async def test_download_pending_is_422(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin)
    item = await _post_item(client, admin_token, job)
    issued = await _issue_upload(client, admin_token, item["timeline_item_id"])

    r = await client.get(
        f"/timeline/attachments/{issued['attachment_id']}",
        headers=_auth(admin_token),
    )
    assert r.status_code == 422
    assert "not been confirmed" in r.json()["detail"]


@pytest.mark.asyncio
async def test_download_404_cells(
    client, db_session, seeded_admin, admin_token, contributor_token
):
    # Ghost id.
    r = await client.get(
        f"/timeline/attachments/{uuid.uuid4()}",
        headers=_auth(contributor_token),
    )
    assert r.status_code == 404

    job = await _mk_job(db_session, seeded_admin)
    item = await _post_item(client, admin_token, job)
    issued = await _issue_upload(client, admin_token, item["timeline_item_id"])

    # Soft-deleted attachment row.
    await db_session.execute(
        sa.update(TimelineAttachment)
        .where(
            TimelineAttachment.attachment_id
            == uuid.UUID(issued["attachment_id"])
        )
        .values(deleted_at=datetime.now(UTC))
        .execution_options(include_deleted=True)
    )
    await db_session.flush()
    r = await client.get(
        f"/timeline/attachments/{issued['attachment_id']}",
        headers=_auth(contributor_token),
    )
    assert r.status_code == 404

    # Attachment under a soft-deleted item.
    issued2 = await _issue_upload(client, admin_token, item["timeline_item_id"])
    r = await client.delete(
        f"/timeline/{item['timeline_item_id']}", headers=_auth(admin_token)
    )
    assert r.status_code == 204
    r = await client.get(
        f"/timeline/attachments/{issued2['attachment_id']}",
        headers=_auth(contributor_token),
    )
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Orphan-cleanup placeholder                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_purge_orphan_attachments_sweeps_only_stale_pending(
    db_session, seeded_admin, seeded_contributor
):
    job = await _mk_job(db_session, seeded_admin)
    item = await timeline_svc.create_timeline_item(
        db_session,
        job_id=job.job_id,
        current_user=seeded_contributor,
        payload=TimelineItemCreate(
            item_type=TimelineItemType.photo,
            occurred_at=datetime(2026, 7, 6, 9, 0, tzinfo=UTC),
        ),
    )

    old = datetime.now(UTC) - timedelta(hours=48)

    def _att(status: AttachmentUploadStatus, created_at):
        return TimelineAttachment(
            attachment_id=uuid.uuid4(),
            timeline_item_id=item.timeline_item_id,
            storage_key=f"k/{uuid.uuid4().hex}.jpg",
            content_type="image/jpeg",
            upload_status=status,
            created_by=seeded_contributor.user_id,
            created_at=created_at,
        )

    stale_pending = _att(AttachmentUploadStatus.pending, old)
    fresh_pending = _att(AttachmentUploadStatus.pending, datetime.now(UTC))
    old_confirmed = _att(AttachmentUploadStatus.confirmed, old)
    db_session.add_all([stale_pending, fresh_pending, old_confirmed])
    await db_session.flush()

    swept = await timeline_svc.purge_orphan_attachments(
        db_session, older_than_hours=24
    )
    assert swept == 1

    # Only the stale pending row is gone from filtered reads.
    remaining = (
        (
            await db_session.execute(
                sa.select(TimelineAttachment.attachment_id).where(
                    TimelineAttachment.timeline_item_id == item.timeline_item_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(remaining) == {
        fresh_pending.attachment_id,
        old_confirmed.attachment_id,
    }
