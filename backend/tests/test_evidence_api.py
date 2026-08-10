"""Tests for the evidence API (Evidence Storage foundation slice).

Covers the founder-ruled behaviours explicitly:

* upload happy path (voice/photo/text) with pending→stored lifecycle,
  audit rows, and occurred_at ≠ created_at (DEC-TIME-001); occurred_at
  optional — absent stays NULL, never server-defaulted;
* size-cap rejection → 413 + row failed + audit "failed";
* storage failure → 502 + row failed + audit "failed";
* NULL-job access rule: until job-linked, readable only by uploader and
  admin (others get 404, never 403);
* job_id is CONFIRMED-ONLY: written by exactly the explicit upload field
  and link-job action; the API surface has no suggestion field;
* link-job: initial link explicit + audited; relink admin-only with
  mandatory reason, audited job_relinked (old/new/reason preserved);
* download always sets Content-Disposition: attachment;
* cross-job 404 semantics on unknown jobs; auth required everywhere;
* no delete route exists.
"""

from __future__ import annotations

import datetime as _datetime
import io
import uuid

import pytest
from sqlalchemy import select

from app.models import Job, JobStatus
from app.models.evidence import Evidence, EvidenceAuditLog, EvidenceStatus

pytestmark = pytest.mark.asyncio

OCCURRED_AT = "2026-08-09T07:30:00+10:00"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _mk_job(db_session, admin, *, name: str = "Evidence Job") -> Job:
    job = Job(
        job_id=uuid.uuid4(),
        job_name=name,
        status=JobStatus.active,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


def _upload_kwargs(
    *,
    content: bytes = b"fake-audio-bytes",
    mime: str = "audio/m4a",
    filename: str = "memo.m4a",
    job_id: uuid.UUID | None = None,
    occurred_at: str | None = OCCURRED_AT,
):
    data = {}
    if occurred_at is not None:
        data["occurred_at"] = occurred_at
    if job_id is not None:
        data["job_id"] = str(job_id)
    return {
        "files": {"file": (filename, io.BytesIO(content), mime)},
        "data": data,
    }


async def _audit_actions(db_session, evidence_id) -> list[str]:
    result = await db_session.execute(
        select(EvidenceAuditLog.action)
        .where(EvidenceAuditLog.evidence_id == evidence_id)
        .order_by(EvidenceAuditLog.created_at)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------- upload


async def test_upload_happy_path_stored_with_audit(
    client, db_session, contributor_token
):
    resp = await client.post(
        "/evidence", **_upload_kwargs(), headers=_auth(contributor_token)
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "stored"
    assert body["media_type"] == "audio"
    assert body["job_id"] is None
    assert body["size_bytes"] == len(b"fake-audio-bytes")
    assert body["sha256"]

    evidence_id = uuid.UUID(body["evidence_id"])
    evidence = await db_session.get(Evidence, evidence_id)
    assert evidence.status == EvidenceStatus.stored
    assert evidence.storage_key is not None
    assert evidence.storage_backend == "local"

    assert await _audit_actions(db_session, evidence_id) == [
        "uploaded",
        "stored",
    ]


@pytest.mark.parametrize(
    ("mime", "expected"),
    [
        ("audio/m4a", "audio"),
        ("image/jpeg", "image"),
        ("text/plain", "text"),
        ("application/pdf", "document"),
    ],
)
async def test_media_type_derived_from_mime(
    client, contributor_token, mime, expected
):
    resp = await client.post(
        "/evidence",
        **_upload_kwargs(mime=mime, filename="f.bin"),
        headers=_auth(contributor_token),
    )
    assert resp.status_code == 201
    assert resp.json()["media_type"] == expected


async def test_occurred_at_distinct_from_created_at(
    client, db_session, contributor_token
):
    """DEC-TIME-001: a morning event uploaded later keeps both times."""
    resp = await client.post(
        "/evidence", **_upload_kwargs(), headers=_auth(contributor_token)
    )
    body = resp.json()
    occurred = _datetime.datetime.fromisoformat(body["occurred_at"])
    created = _datetime.datetime.fromisoformat(body["created_at"])
    assert occurred == _datetime.datetime.fromisoformat(OCCURRED_AT)
    assert created != occurred  # record written now, event in the past


async def test_upload_without_occurred_at_stays_null(
    client, db_session, contributor_token
):
    """Correction 2: absent occurred_at → NULL row, no server default."""
    resp = await client.post(
        "/evidence",
        **_upload_kwargs(occurred_at=None),
        headers=_auth(contributor_token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["occurred_at"] is None
    assert body["status"] == "stored"

    evidence = await db_session.get(Evidence, uuid.UUID(body["evidence_id"]))
    assert evidence.occurred_at is None  # NOT defaulted to upload time
    assert evidence.created_at is not None


async def test_no_occurred_at_defaulting_anywhere():
    """Correction 2: no code path manufactures occurred_at server-side.

    Static guarantees: the model column has no default of any kind, and
    the service stores the caller value verbatim (asserted behaviourally
    above); this test pins the schema-level facts so a future "helpful"
    default breaks loudly.
    """
    from app.models.evidence import Evidence as EvidenceModel

    col = EvidenceModel.__table__.columns["occurred_at"]
    assert col.nullable is True
    assert col.default is None
    assert col.server_default is None
    assert col.onupdate is None
    created = EvidenceModel.__table__.columns["created_at"]
    assert created.nullable is False


async def test_upload_with_explicit_job(
    client, db_session, seeded_admin, contributor_token
):
    job = await _mk_job(db_session, seeded_admin)
    resp = await client.post(
        "/evidence",
        **_upload_kwargs(job_id=job.job_id),
        headers=_auth(contributor_token),
    )
    assert resp.status_code == 201
    assert resp.json()["job_id"] == str(job.job_id)


async def test_upload_unknown_job_404(client, contributor_token):
    resp = await client.post(
        "/evidence",
        **_upload_kwargs(job_id=uuid.uuid4()),
        headers=_auth(contributor_token),
    )
    assert resp.status_code == 404


async def test_upload_requires_auth(client):
    resp = await client.post("/evidence", **_upload_kwargs())
    assert resp.status_code == 401


async def test_upload_size_cap_413_row_failed(
    client, db_session, contributor_token, monkeypatch
):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "evidence_max_upload_bytes", 10)

    resp = await client.post(
        "/evidence",
        **_upload_kwargs(content=b"x" * 64),
        headers=_auth(contributor_token),
    )
    assert resp.status_code == 413

    result = await db_session.execute(
        select(Evidence).where(Evidence.status == EvidenceStatus.failed)
    )
    failed = result.scalars().all()
    assert len(failed) == 1
    assert failed[0].storage_key is None
    actions = await _audit_actions(db_session, failed[0].evidence_id)
    assert actions == ["uploaded", "failed"]


async def test_upload_storage_error_502_row_failed(
    client, db_session, contributor_token
):
    from app.api.evidence import get_evidence_storage
    from app.main import app
    from app.services.evidence_storage import EvidenceStorageError

    class ExplodingStorage:
        backend_name = "local"

        async def put(self, evidence_id, chunks):
            async for _ in chunks:
                break
            raise EvidenceStorageError("backend down")

    app.dependency_overrides[get_evidence_storage] = ExplodingStorage
    try:
        resp = await client.post(
            "/evidence", **_upload_kwargs(), headers=_auth(contributor_token)
        )
    finally:
        app.dependency_overrides.pop(get_evidence_storage, None)

    assert resp.status_code == 502
    result = await db_session.execute(
        select(Evidence).where(Evidence.status == EvidenceStatus.failed)
    )
    failed = result.scalars().all()
    assert len(failed) == 1
    assert await _audit_actions(db_session, failed[0].evidence_id) == [
        "uploaded",
        "failed",
    ]


async def test_upload_streams_in_bounded_chunks(
    client, contributor_token
):
    """Ruling 8: the service hands the adapter bounded chunks only."""
    from app.api.evidence import get_evidence_storage
    from app.main import app
    from app.services.evidence_storage import CHUNK_SIZE, StoredObject

    seen_sizes: list[int] = []

    class SpyStorage:
        backend_name = "local"

        async def put(self, evidence_id, chunks):
            import hashlib

            hasher = hashlib.sha256()
            size = 0
            async for chunk in chunks:
                seen_sizes.append(len(chunk))
                hasher.update(chunk)
                size += len(chunk)
            return StoredObject(
                key=f"evidence/{evidence_id}/{hasher.hexdigest()[:16]}",
                size_bytes=size,
                sha256=hasher.hexdigest(),
            )

    payload = b"y" * (2 * CHUNK_SIZE + 123)  # > 2 chunks
    app.dependency_overrides[get_evidence_storage] = SpyStorage
    try:
        resp = await client.post(
            "/evidence",
            **_upload_kwargs(content=payload),
            headers=_auth(contributor_token),
        )
    finally:
        app.dependency_overrides.pop(get_evidence_storage, None)

    assert resp.status_code == 201
    assert sum(seen_sizes) == len(payload)
    assert len(seen_sizes) >= 3
    assert all(size <= CHUNK_SIZE for size in seen_sizes)


# ------------------------------------------------------- read access rules


async def _upload_as(client, token, **kw) -> dict:
    resp = await client.post(
        "/evidence", **_upload_kwargs(**kw), headers=_auth(token)
    )
    assert resp.status_code == 201
    return resp.json()


async def test_null_job_visible_to_uploader_and_admin_only(
    client, db_session, seeded_admin, admin_token, contributor_token
):
    """Founder ruling 4: unlinked evidence = uploader + admin only."""
    from app.core.security import create_access_token, hash_password
    from app.models.user import LanguageCode, User, UserRole

    body = await _upload_as(client, contributor_token)
    evidence_id = body["evidence_id"]

    other = User(
        user_id=uuid.uuid4(),
        full_name="Other Contributor",
        email="other@example.com",
        password_hash=hash_password("other"),
        role=UserRole.contributor,
        language_preference=LanguageCode.en,
        is_active=True,
    )
    db_session.add(other)
    await db_session.flush()
    other_token = create_access_token({"sub": str(other.user_id)})

    # Uploader: 200. Admin: 200. Unrelated contributor: 404 (not 403).
    assert (
        await client.get(
            f"/evidence/{evidence_id}", headers=_auth(contributor_token)
        )
    ).status_code == 200
    assert (
        await client.get(
            f"/evidence/{evidence_id}", headers=_auth(admin_token)
        )
    ).status_code == 200
    other_resp = await client.get(
        f"/evidence/{evidence_id}", headers=_auth(other_token)
    )
    assert other_resp.status_code == 404

    # Download follows the same rule.
    assert (
        await client.get(
            f"/evidence/{evidence_id}/download", headers=_auth(other_token)
        )
    ).status_code == 404


async def test_linked_evidence_visible_to_other_users(
    client, db_session, seeded_admin, admin_token, contributor_token
):
    from app.core.security import create_access_token, hash_password
    from app.models.user import LanguageCode, User, UserRole

    job = await _mk_job(db_session, seeded_admin)
    body = await _upload_as(client, contributor_token, job_id=job.job_id)

    other = User(
        user_id=uuid.uuid4(),
        full_name="Other Contributor 2",
        email="other2@example.com",
        password_hash=hash_password("other2"),
        role=UserRole.contributor,
        language_preference=LanguageCode.en,
        is_active=True,
    )
    db_session.add(other)
    await db_session.flush()
    other_token = create_access_token({"sub": str(other.user_id)})

    resp = await client.get(
        f"/evidence/{body['evidence_id']}", headers=_auth(other_token)
    )
    assert resp.status_code == 200


# ------------------------------------------------------------- link-job


async def test_link_job_explicit_action_with_audit(
    client, db_session, seeded_admin, contributor_token
):
    job = await _mk_job(db_session, seeded_admin)
    body = await _upload_as(client, contributor_token)
    evidence_id = body["evidence_id"]

    resp = await client.post(
        f"/evidence/{evidence_id}/link-job",
        json={"job_id": str(job.job_id)},
        headers=_auth(contributor_token),
    )
    assert resp.status_code == 200
    assert resp.json()["job_id"] == str(job.job_id)
    assert await _audit_actions(db_session, uuid.UUID(evidence_id)) == [
        "uploaded",
        "stored",
        "job_linked",
    ]


async def test_relink_admin_with_reason_happy_path(
    client, db_session, seeded_admin, admin_token, contributor_token
):
    """Correction 1: admin relink job→job with reason; audit preserves old."""
    job_a = await _mk_job(db_session, seeded_admin, name="Job A")
    job_b = await _mk_job(db_session, seeded_admin, name="Job B")
    body = await _upload_as(client, contributor_token, job_id=job_a.job_id)
    evidence_id = body["evidence_id"]
    key_before = (await db_session.get(Evidence, uuid.UUID(evidence_id))).storage_key

    resp = await client.post(
        f"/evidence/{evidence_id}/link-job",
        json={"job_id": str(job_b.job_id), "reason": "uploaded to wrong job"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["job_id"] == str(job_b.job_id)

    # Bytes/key untouched — only the metadata column changed.
    evidence = await db_session.get(Evidence, uuid.UUID(evidence_id))
    assert evidence.storage_key == key_before

    # Audit row content: old, new, reason, actor, real timestamp.
    result = await db_session.execute(
        select(EvidenceAuditLog)
        .where(EvidenceAuditLog.evidence_id == uuid.UUID(evidence_id))
        .where(EvidenceAuditLog.action == "job_relinked")
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.detail["old_job_id"] == str(job_a.job_id)
    assert row.detail["new_job_id"] == str(job_b.job_id)
    assert row.detail["reason"] == "uploaded to wrong job"
    assert row.actor_user_id == seeded_admin.user_id
    assert row.created_at is not None


async def test_relink_non_admin_403(
    client, db_session, seeded_admin, contributor_token
):
    """Correction 1: non-admin relink rejected per require_admin convention."""
    job_a = await _mk_job(db_session, seeded_admin, name="Job A")
    job_b = await _mk_job(db_session, seeded_admin, name="Job B")
    body = await _upload_as(client, contributor_token, job_id=job_a.job_id)

    resp = await client.post(
        f"/evidence/{body['evidence_id']}/link-job",
        json={"job_id": str(job_b.job_id), "reason": "still not allowed"},
        headers=_auth(contributor_token),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin only"


@pytest.mark.parametrize("reason", [None, "", "   "])
async def test_relink_missing_reason_422(
    client, db_session, seeded_admin, admin_token, contributor_token, reason
):
    job_a = await _mk_job(db_session, seeded_admin, name="Job A")
    job_b = await _mk_job(db_session, seeded_admin, name="Job B")
    body = await _upload_as(client, contributor_token, job_id=job_a.job_id)

    payload = {"job_id": str(job_b.job_id)}
    if reason is not None:
        payload["reason"] = reason
    resp = await client.post(
        f"/evidence/{body['evidence_id']}/link-job",
        json=payload,
        headers=_auth(admin_token),
    )
    assert resp.status_code == 422
    # No audit row was written for the rejected attempt beyond upload/stored.
    actions = await _audit_actions(
        db_session, uuid.UUID(body["evidence_id"])
    )
    assert "job_relinked" not in actions


async def test_link_job_unknown_job_404(client, contributor_token):
    body = await _upload_as(client, contributor_token)
    resp = await client.post(
        f"/evidence/{body['evidence_id']}/link-job",
        json={"job_id": str(uuid.uuid4())},
        headers=_auth(contributor_token),
    )
    assert resp.status_code == 404


async def test_job_id_has_no_suggestion_writer(client, contributor_token):
    """Ruling 7: the API surface exposes no suggested/inferred-job field.

    Extra fields are rejected or ignored — they can never reach
    ``evidence.job_id``. The exhaustive writer census is enforced by
    the service design (two explicit paths) and checked here at the
    contract level.
    """
    kw = _upload_kwargs()
    kw["data"]["suggested_job_id"] = str(uuid.uuid4())
    kw["data"]["job_attribution_status"] = "suggested"
    resp = await client.post(
        "/evidence", **kw, headers=_auth(contributor_token)
    )
    assert resp.status_code == 201
    assert resp.json()["job_id"] is None  # unknown fields had no effect

    # And the schema itself carries no attribution field.
    from app.models.evidence import Evidence as EvidenceModel

    assert not any(
        "suggest" in col.name or "attribution" in col.name
        for col in EvidenceModel.__table__.columns
    )


# ------------------------------------------------------------- download


async def test_download_roundtrip_content_disposition_attachment(
    client, contributor_token
):
    payload = b"downloadable evidence bytes"
    body = await _upload_as(
        client, contributor_token, content=payload, filename="note.txt",
        mime="text/plain",
    )
    resp = await client.get(
        f"/evidence/{body['evidence_id']}/download",
        headers=_auth(contributor_token),
    )
    assert resp.status_code == 200
    assert resp.content == payload
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment")
    # Server-generated safe name: evidence_id + extension from MIME.
    assert f'filename="{body["evidence_id"]}.txt"' in disposition
    # Original name survives as JSON metadata only.
    assert body["original_filename"] == "note.txt"


@pytest.mark.parametrize(
    "hostile",
    [
        'evil";x="y.txt',    # quote breakout
        "crlf\r\nSet-Cookie: pwned=1.txt",  # header injection attempt
        "工地照片 🏗.jpg",  # unicode
    ],
)
async def test_download_hostile_filename_never_reaches_header(
    client, contributor_token, hostile
):
    """Acceptance item: hostile client filenames cannot touch the header."""
    body = await _upload_as(
        client, contributor_token, filename=hostile, mime="text/plain"
    )
    resp = await client.get(
        f"/evidence/{body['evidence_id']}/download",
        headers=_auth(contributor_token),
    )
    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert disposition == f'attachment; filename="{body["evidence_id"]}.txt"'
    assert "\r" not in disposition and "\n" not in disposition
    assert "evil" not in disposition and "pwned" not in disposition


async def test_download_missing_404(client, contributor_token):
    resp = await client.get(
        f"/evidence/{uuid.uuid4()}/download", headers=_auth(contributor_token)
    )
    assert resp.status_code == 404


# ------------------------------------------------------------ job listing


async def test_list_job_evidence(client, db_session, seeded_admin, admin_token, contributor_token):
    job = await _mk_job(db_session, seeded_admin)
    await _upload_as(client, contributor_token, job_id=job.job_id)
    await _upload_as(client, contributor_token, job_id=job.job_id)
    await _upload_as(client, contributor_token)  # unlinked — excluded

    resp = await client.get(
        f"/jobs/{job.job_id}/evidence", headers=_auth(admin_token)
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_list_unknown_job_404(client, admin_token):
    resp = await client.get(
        f"/jobs/{uuid.uuid4()}/evidence", headers=_auth(admin_token)
    )
    assert resp.status_code == 404


# ------------------------------------------------------------- retention


async def test_no_delete_route_exists(client, contributor_token, admin_token):
    """DEC-EVIDENCE-001: no HTTP surface can destroy evidence."""
    body = await _upload_as(client, contributor_token)
    for token in (contributor_token, admin_token):
        resp = await client.delete(
            f"/evidence/{body['evidence_id']}", headers=_auth(token)
        )
        assert resp.status_code == 405
