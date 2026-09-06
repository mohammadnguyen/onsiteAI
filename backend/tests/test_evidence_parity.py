"""WP A A2a — V1 Evidence parity.

Evidence that is NOT bound to a Site Log event must behave exactly as
before A2a: the legacy read rule, ``link_job`` initial link / admin
relink, and the API surface. These tests exercise the unbound path
through the same functions the bound path changed.
"""

from __future__ import annotations

import io
import uuid

import pytest

from app.models import Job, JobStatus
from app.models.evidence import Evidence, EvidenceStatus
from app.services import evidence as evidence_service
from app.services.evidence import _can_read, _can_read_async

pytestmark = pytest.mark.asyncio


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _unbound(db, uploader, *, job_id=None):
    ev = Evidence(
        evidence_id=uuid.uuid4(), job_id=job_id, uploaded_by_user_id=uploader.user_id,
        media_type="audio", mime_type="audio/m4a", original_filename=None,
        status=EvidenceStatus.pending, occurred_at=None,
    )
    db.add(ev)
    await db.flush()
    return ev


@pytest.fixture
async def other_user(db_session):
    from app.core.security import hash_password
    from app.models.user import LanguageCode, User, UserRole

    user = User(
        user_id=uuid.uuid4(), full_name="Other", email="parity-other@example.com",
        password_hash=hash_password("x"), role=UserRole.contributor,
        language_preference=LanguageCode.en, is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def test_unbound_read_rule_identical_to_v1(
    db_session, seeded_admin, seeded_contributor, other_user
):
    job = Job(job_id=uuid.uuid4(), job_name="P", status=JobStatus.active,
              created_by=seeded_admin.user_id)
    db_session.add(job)
    await db_session.flush()
    for job_id in (None, job.job_id):
        ev = await _unbound(db_session, seeded_contributor, job_id=job_id)
        for user in (seeded_admin, seeded_contributor, other_user):
            assert await _can_read_async(db_session, user, ev) is _can_read(user, ev)
        assert _can_read(other_user, ev) is (job_id is not None)


async def test_unbound_link_job_unchanged(db_session, seeded_admin, seeded_contributor):
    job_a = Job(job_id=uuid.uuid4(), job_name="A", status=JobStatus.active,
                created_by=seeded_admin.user_id)
    job_b = Job(job_id=uuid.uuid4(), job_name="B", status=JobStatus.active,
                created_by=seeded_admin.user_id)
    db_session.add_all([job_a, job_b])
    await db_session.flush()
    ev = await _unbound(db_session, seeded_contributor)
    out = await evidence_service.link_job(
        db_session, seeded_contributor, ev.evidence_id, job_a.job_id
    )
    assert out.job_id == job_a.job_id
    with pytest.raises(evidence_service.EvidenceRelinkForbidden):
        await evidence_service.link_job(
            db_session, seeded_contributor, ev.evidence_id, job_b.job_id, reason="r"
        )
    out = await evidence_service.link_job(
        db_session, seeded_admin, ev.evidence_id, job_b.job_id, reason="moved"
    )
    assert out.job_id == job_b.job_id


async def test_legacy_upload_route_unchanged(client, contributor_token, admin_token):
    resp = await client.post(
        "/evidence",
        files={"file": ("memo.m4a", io.BytesIO(b"legacy bytes"), "audio/m4a")},
        data={},
        headers=_auth(contributor_token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "stored" and body["job_id"] is None
    assert (await client.get(f"/evidence/{body['evidence_id']}",
                             headers=_auth(admin_token))).status_code == 200
    dl = await client.get(f"/evidence/{body['evidence_id']}/download",
                          headers=_auth(contributor_token))
    assert dl.status_code == 200 and dl.content == b"legacy bytes"
