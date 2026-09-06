"""WP A A2a — HTTP surface of the Site Log capture lifecycle.

Status-code mapping per route, contributor response shape (no
eligibility, no attempt counters), bound-Evidence permission inheritance
through the legacy ``/evidence`` routes, and existence-hiding denials
that write no audit row. Synthetic bytes only.
"""

from __future__ import annotations

import io
import uuid

import pytest
from sqlalchemy import func, select, update

from app.models import (
    Evidence,
    EvidenceAuditLog,
    Job,
    JobStatus,
    SiteLogEvent,
    SiteLogEventAttachment,
    SiteLogEventAuditLog,
)
from app.models.user import User
from app.services import site_log as svc

pytestmark = pytest.mark.asyncio

EVENT_KEYS = {
    "site_log_event_id", "author_user_id", "job_id", "job_state",
    "capture_status", "created_at", "revision", "attachments",
}
ATTACHMENT_KEYS = {
    "attachment_client_id", "declared_media_type", "declared_size_bytes",
    "state", "evidence_id",
}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _file(content=b"synthetic audio bytes", mime="audio/m4a", name="memo.m4a"):
    return {"file": (name, io.BytesIO(content), mime)}


def _att(media="audio"):
    return {"attachment_client_id": str(uuid.uuid4()), "declared_media_type": media}


async def _mk_job(db, admin, *, status=JobStatus.active, name="Job"):
    job = Job(job_id=uuid.uuid4(), job_name=name, status=status, created_by=admin.user_id)
    db.add(job)
    await db.flush()
    return job


async def _declare(client, token, **body):
    payload = {"capture_client_id": str(uuid.uuid4()), **body}
    return await client.post("/site-log-events", json=payload, headers=_auth(token))


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


@pytest.fixture
async def other_token(db_session):
    from app.core.security import create_access_token, hash_password
    from app.models.user import LanguageCode, User, UserRole

    user = User(
        user_id=uuid.uuid4(), full_name="Other Contributor", email="other@example.com",
        password_hash=hash_password("x"), role=UserRole.contributor,
        language_preference=LanguageCode.en, is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return create_access_token({"sub": str(user.user_id)})


# --------------------------------------------------------------- declare


async def test_declare_shapes_status_codes(client, contributor_token, site_log_session_factory):
    cid = str(uuid.uuid4())
    att = _att()
    r = await _declare(client, contributor_token, capture_client_id=cid,
                       body_text="fix leak", attachments=[att])
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == EVENT_KEYS
    assert body["job_state"] == "unassigned" and body["capture_status"] == "pending_upload"
    assert body["attachments"] and all(set(a) == ATTACHMENT_KEYS for a in body["attachments"])
    assert "eligibility" not in r.text and "upload_attempt_no" not in r.text
    # identical replay → 200, same id
    r2 = await _declare(client, contributor_token, capture_client_id=cid,
                        body_text="fix leak", attachments=[att])
    assert r2.status_code == 200 and r2.json()["site_log_event_id"] == body["site_log_event_id"]
    # divergent replay → 409
    r3 = await _declare(client, contributor_token, capture_client_id=cid,
                        body_text="fix leak!", attachments=[att])
    assert r3.status_code == 409
    # shape 1 → 201 complete
    r4 = await _declare(client, contributor_token, body_text="note only")
    assert r4.status_code == 201 and r4.json()["capture_status"] == "complete"
    # shape 4 / blank / inline collision / unknown job → 422 / 404
    assert (await _declare(client, contributor_token)).status_code == 422
    assert (await _declare(client, contributor_token, body_text="  \n")).status_code == 422
    cid2 = uuid.uuid4()
    r5 = await _declare(
        client, contributor_token, capture_client_id=str(cid2), body_text="x",
        attachments=[{"attachment_client_id": str(svc.inline_attachment_id(cid2)),
                      "declared_media_type": "text"}],
    )
    assert r5.status_code == 422
    r6 = await _declare(client, contributor_token, body_text="x", job_id=str(uuid.uuid4()))
    assert r6.status_code == 404
    assert (await client.post("/site-log-events", json={})).status_code == 401


async def test_declare_completed_job_422(
    client, db_session, seeded_admin, contributor_token, site_log_session_factory
):
    done = await _mk_job(db_session, seeded_admin, status=JobStatus.completed)
    r = await _declare(client, contributor_token, body_text="x", job_id=str(done.job_id))
    assert r.status_code == 422


# ---------------------------------------------------------------- upload


async def test_upload_lifecycle_status_codes(
    client, db_session, contributor_token, admin_token, other_token, site_log_session_factory
):
    att = _att()
    r = await _declare(client, contributor_token, attachments=[att])
    eid, acid = r.json()["site_log_event_id"], att["attachment_client_id"]
    url = f"/site-log-events/{eid}/attachments/{acid}"

    # not-yet-ready finalize → 409 with per-attachment states
    f = await client.post(f"/site-log-events/{eid}/finalize", headers=_auth(contributor_token))
    assert f.status_code == 409 and f.json()["detail"]["states"][acid] == "awaiting_upload"

    # MIME mismatch → 422, nothing acquired
    m = await client.put(url, files=_file(mime="image/png"), headers=_auth(contributor_token))
    assert m.status_code == 422
    # another contributor cannot upload (existence hidden) → 404
    o = await client.put(url, files=_file(), headers=_auth(other_token))
    assert o.status_code == 404
    # unknown attachment id → 404
    u = await client.put(f"/site-log-events/{eid}/attachments/{uuid.uuid4()}",
                         files=_file(), headers=_auth(contributor_token))
    assert u.status_code == 404

    up = await client.put(url, files=_file(), headers=_auth(contributor_token))
    assert up.status_code == 201, up.text
    body = up.json()
    assert body["state"] == "stored" and body["evidence_id"] and body["sha256"]
    assert set(body) == {"attachment_client_id", "state", "evidence_id", "sha256", "size_bytes"}
    # replay → 200 same evidence
    again = await client.put(url, files=_file(b"anything"), headers=_auth(contributor_token))
    assert again.status_code == 200 and again.json()["evidence_id"] == body["evidence_id"]

    fin = await client.post(f"/site-log-events/{eid}/finalize", headers=_auth(contributor_token))
    assert fin.status_code == 200 and fin.json()["capture_status"] == "complete"

    # Bound Evidence readable by the author through the legacy route, and
    # download works; a stranger gets 404 for both while unassigned.
    ev = await client.get(f"/evidence/{body['evidence_id']}", headers=_auth(contributor_token))
    assert ev.status_code == 200
    dl = await client.get(f"/evidence/{body['evidence_id']}/download",
                          headers=_auth(contributor_token))
    assert dl.status_code == 200 and dl.content == b"synthetic audio bytes"
    assert (await client.get(f"/evidence/{body['evidence_id']}",
                             headers=_auth(other_token))).status_code == 404
    assert (await client.get(f"/evidence/{body['evidence_id']}/download",
                             headers=_auth(other_token))).status_code == 404
    assert (await client.get(f"/evidence/{body['evidence_id']}",
                             headers=_auth(admin_token))).status_code == 200


async def test_upload_too_large_413_and_pending_409(
    client, db_session, contributor_token, admin_token, site_log_session_factory, monkeypatch
):
    from app.config import get_settings

    att = _att()
    r = await _declare(client, contributor_token, attachments=[att])
    eid, acid = r.json()["site_log_event_id"], att["attachment_client_id"]
    url = f"/site-log-events/{eid}/attachments/{acid}"
    monkeypatch.setattr(get_settings(), "evidence_max_upload_bytes", 4)
    big = await client.put(url, files=_file(b"12345"), headers=_auth(contributor_token))
    assert big.status_code == 413
    monkeypatch.setattr(get_settings(), "evidence_max_upload_bytes", 1024 * 1024)
    row = (
        await db_session.execute(
            select(SiteLogEventAttachment).where(
                SiteLogEventAttachment.attachment_client_id == uuid.UUID(acid)
            ).execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert row.state.value == "failed" and row.upload_attempt_no == 1
    # partial_failed is a valid, repairable finalize outcome
    fin = await client.post(f"/site-log-events/{eid}/finalize", headers=_auth(contributor_token))
    assert fin.status_code == 200 and fin.json()["capture_status"] == "partial_failed"

    # Simulate a stuck pending attempt: reset rules over HTTP.
    await db_session.execute(
        update(SiteLogEventAttachment)
        .where(SiteLogEventAttachment.attachment_id == row.attachment_id)
        .values(state="pending")
    )
    retry = await client.put(url, files=_file(), headers=_auth(contributor_token))
    assert retry.status_code == 409  # in progress, no self-heal
    reset_url = f"{url}/reset"
    assert (await client.post(reset_url, json={"reason": "stuck"},
                              headers=_auth(contributor_token))).status_code == 403
    assert (await client.post(reset_url, json={"reason": ""},
                              headers=_auth(admin_token))).status_code == 422
    assert (await client.post(reset_url, json={"reason": "stuck"},
                              headers=_auth(admin_token))).status_code == 409  # too young
    await db_session.execute(
        update(SiteLogEventAttachment)
        .where(SiteLogEventAttachment.attachment_id == row.attachment_id)
        .values(updated_at=func.now() - func.make_interval(0, 0, 0, 0, 0, 16))
    )
    ok = await client.post(reset_url, json={"reason": "stuck after crash"},
                           headers=_auth(admin_token))
    assert ok.status_code == 200 and ok.json()["state"] == "failed"
    assert (await client.post(reset_url, json={"reason": "again"},
                              headers=_auth(admin_token))).status_code == 409  # nothing pending
    fixed = await client.put(url, files=_file(), headers=_auth(contributor_token))
    assert fixed.status_code == 201


async def test_upload_storage_error_502_row_failed(
    client, db_session, contributor_token, site_log_session_factory, monkeypatch
):
    from app.services.evidence_storage import EvidenceStorageError, get_evidence_storage

    class _Broken:
        backend_name = "broken"

        async def put(self, evidence_id, chunks, *, attempt_no=None):
            async for _ in chunks:
                pass
            raise EvidenceStorageError("backend down")

        def open(self, key):
            raise AssertionError

        async def exists(self, key):
            return False

    from app.main import app

    att = _att()
    r = await _declare(client, contributor_token, attachments=[att])
    eid, acid = r.json()["site_log_event_id"], att["attachment_client_id"]
    app.dependency_overrides[get_evidence_storage] = lambda: _Broken()
    try:
        up = await client.put(f"/site-log-events/{eid}/attachments/{acid}",
                              files=_file(), headers=_auth(contributor_token))
    finally:
        app.dependency_overrides.pop(get_evidence_storage, None)
    assert up.status_code == 502
    row = (
        await db_session.execute(
            select(SiteLogEventAttachment.state, SiteLogEventAttachment.evidence_id).where(
                SiteLogEventAttachment.attachment_client_id == uuid.UUID(acid)
            )
        )
    ).one()
    assert row.state.value == "failed" and row.evidence_id is not None
    ev_status = (
        await db_session.execute(
            select(Evidence.status).where(Evidence.evidence_id == row.evidence_id)
        )
    ).scalar_one()
    assert ev_status.value == "failed"


async def test_inline_text_storage_failure_502_with_state(
    client, contributor_token, site_log_session_factory
):
    from app.main import app
    from app.services.evidence_storage import EvidenceStorageError, get_evidence_storage

    class _Broken:
        backend_name = "broken"

        async def put(self, evidence_id, chunks, *, attempt_no=None):
            raise EvidenceStorageError("backend down")

        def open(self, key):
            raise AssertionError

        async def exists(self, key):
            return False

    cid = str(uuid.uuid4())
    app.dependency_overrides[get_evidence_storage] = lambda: _Broken()
    try:
        r = await _declare(client, contributor_token, capture_client_id=cid, body_text="hello")
    finally:
        app.dependency_overrides.pop(get_evidence_storage, None)
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert set(detail) == EVENT_KEYS
    assert detail["attachments"][0]["state"] == "failed"
    # Durable + replayable: same declaration now succeeds with a healthy backend.
    r2 = await _declare(client, contributor_token, capture_client_id=cid, body_text="hello")
    assert r2.status_code == 200 and r2.json()["capture_status"] == "complete"


# ---------------------------------------------------------- job attribution


async def test_assign_relink_routes(
    client, db_session, seeded_admin, contributor_token, admin_token, other_token,
    site_log_session_factory,
):
    job_a = await _mk_job(db_session, seeded_admin, name="A")
    job_b = await _mk_job(db_session, seeded_admin, name="B")
    done = await _mk_job(db_session, seeded_admin, name="Done", status=JobStatus.completed)
    att = _att()
    r = await _declare(client, contributor_token, attachments=[att])
    eid, acid = r.json()["site_log_event_id"], att["attachment_client_id"]
    up = await client.put(f"/site-log-events/{eid}/attachments/{acid}", files=_file(),
                          headers=_auth(contributor_token))
    evidence_id = up.json()["evidence_id"]

    assign = f"/site-log-events/{eid}/assign-job"
    relink = f"/site-log-events/{eid}/relink-job"
    assert (await client.post(assign, json={"job_id": str(uuid.uuid4())},
                              headers=_auth(contributor_token))).status_code == 404
    assert (await client.post(assign, json={"job_id": str(done.job_id)},
                              headers=_auth(contributor_token))).status_code == 422
    assert (await client.post(assign, json={"job_id": str(job_a.job_id)},
                              headers=_auth(other_token))).status_code == 404
    # relink before assignment → 409
    assert (await client.post(relink, json={"job_id": str(job_a.job_id), "reason": "r"},
                              headers=_auth(admin_token))).status_code == 409
    ok = await client.post(assign, json={"job_id": str(job_a.job_id)},
                           headers=_auth(contributor_token))
    assert ok.status_code == 200 and ok.json()["job_state"] == "confirmed"
    assert (await client.post(assign, json={"job_id": str(job_b.job_id)},
                              headers=_auth(contributor_token))).status_code == 409
    # bound Evidence followed the event
    ev = await client.get(f"/evidence/{evidence_id}", headers=_auth(other_token))
    assert ev.status_code == 200 and ev.json()["job_id"] == str(job_a.job_id)
    # legacy link-job on bound Evidence → 409
    lk = await client.post(f"/evidence/{evidence_id}/link-job",
                           json={"job_id": str(job_b.job_id), "reason": "x"},
                           headers=_auth(admin_token))
    assert lk.status_code == 409

    assert (await client.post(relink, json={"job_id": str(job_b.job_id), "reason": "r"},
                              headers=_auth(contributor_token))).status_code == 403
    assert (await client.post(relink, json={"job_id": str(job_b.job_id)},
                              headers=_auth(admin_token))).status_code == 422
    assert (await client.post(relink, json={"job_id": str(job_a.job_id), "reason": "same"},
                              headers=_auth(admin_token))).status_code == 409
    assert (await client.post(relink, json={"job_id": str(done.job_id), "reason": "c"},
                              headers=_auth(admin_token))).status_code == 422
    rl = await client.post(relink, json={"job_id": str(job_b.job_id), "reason": "misfiled"},
                           headers=_auth(admin_token))
    assert rl.status_code == 200 and rl.json()["job_id"] == str(job_b.job_id)
    ev = await client.get(f"/evidence/{evidence_id}", headers=_auth(other_token))
    assert ev.json()["job_id"] == str(job_b.job_id)

    # listings
    lst = await client.get(f"/jobs/{job_b.job_id}/site-log-events", headers=_auth(other_token))
    assert lst.status_code == 200 and [e["site_log_event_id"] for e in lst.json()] == [eid]
    assert (await client.get(f"/jobs/{job_a.job_id}/site-log-events",
                             headers=_auth(other_token))).json() == []
    assert (await client.get(f"/jobs/{uuid.uuid4()}/site-log-events",
                             headers=_auth(other_token))).status_code == 404


async def test_unassigned_listing_scoped_and_get_denials(
    client, db_session, contributor_token, admin_token, other_token, site_log_session_factory
):
    mine = (await _declare(client, contributor_token, body_text="mine")).json()
    theirs = (await _declare(client, other_token, body_text="theirs")).json()
    ids = lambda r: {e["site_log_event_id"] for e in r.json()}  # noqa: E731
    r_c = await client.get("/site-log-events/unassigned", headers=_auth(contributor_token))
    assert ids(r_c) == {mine["site_log_event_id"]}
    assert ids(await client.get("/site-log-events/unassigned", headers=_auth(admin_token))) == {
        mine["site_log_event_id"], theirs["site_log_event_id"]
    }
    g = await client.get(f"/site-log-events/{theirs['site_log_event_id']}",
                         headers=_auth(contributor_token))
    assert g.status_code == 404
    g = await client.get(f"/site-log-events/{theirs['site_log_event_id']}",
                         headers=_auth(admin_token))
    assert g.status_code == 200 and set(g.json()) == EVENT_KEYS
    assert (await client.get(f"/site-log-events/{uuid.uuid4()}",
                             headers=_auth(admin_token))).status_code == 404


async def test_cross_tenant_denials_write_no_audit(
    client, db_session, admin_token, site_log_session_factory
):
    att = _att()
    r = await _declare(client, admin_token, attachments=[att])
    eid, acid = r.json()["site_log_event_id"], att["attachment_client_id"]
    await db_session.execute(
        update(SiteLogEvent)
        .where(SiteLogEvent.site_log_event_id == uuid.UUID(eid))
        .values(tenant_id=uuid.UUID("00000000-0000-0000-0000-00000000dead"))
    )
    before = (
        await _count(db_session, SiteLogEventAuditLog),
        await _count(db_session, EvidenceAuditLog),
    )
    h = _auth(admin_token)
    assert (await client.get(f"/site-log-events/{eid}", headers=h)).status_code == 404
    assert (await client.put(f"/site-log-events/{eid}/attachments/{acid}", files=_file(),
                             headers=h)).status_code == 404
    assert (await client.post(f"/site-log-events/{eid}/finalize", headers=h)).status_code == 404
    assert (await client.post(f"/site-log-events/{eid}/assign-job",
                              json={"job_id": str(uuid.uuid4())}, headers=h)).status_code == 404
    assert (await client.post(f"/site-log-events/{eid}/relink-job",
                              json={"job_id": str(uuid.uuid4()), "reason": "r"},
                              headers=h)).status_code == 404
    assert (await client.post(f"/site-log-events/{eid}/attachments/{acid}/reset",
                              json={"reason": "r"}, headers=h)).status_code == 404
    after = (
        await _count(db_session, SiteLogEventAuditLog),
        await _count(db_session, EvidenceAuditLog),
    )
    assert after == before


# ------------------------------------------------ admin-only matrix (ruling A)


async def _audit_counts(db):
    return (
        await _count(db, SiteLogEventAuditLog),
        await _count(db, EvidenceAuditLog),
    )


async def _att_state(db, acid):
    return (
        await db.execute(
            select(SiteLogEventAttachment.state, SiteLogEventAttachment.upload_attempt_no)
            .where(SiteLogEventAttachment.attachment_client_id == uuid.UUID(acid))
        )
    ).one()


async def test_admin_only_matrix_reset_and_relink(
    client, db_session, seeded_admin, contributor_token, admin_token, other_token,
    site_log_session_factory,
):
    """unknown / cross-tenant / unreadable → 404; readable non-admin → 403;
    admin → normal; every denial leaves zero state change and zero audit."""
    job_a = await _mk_job(db_session, seeded_admin, name="A")
    job_b = await _mk_job(db_session, seeded_admin, name="B")
    # Unassigned event authored by the contributor, attachment left pending.
    att_u = _att()
    r = await _declare(client, contributor_token, attachments=[att_u])
    eid_u, acid_u = r.json()["site_log_event_id"], att_u["attachment_client_id"]
    # Assigned event authored by the contributor (readable by everyone).
    att_a = _att()
    r = await _declare(client, contributor_token, attachments=[att_a], job_id=str(job_a.job_id))
    eid_a, acid_a = r.json()["site_log_event_id"], att_a["attachment_client_id"]
    for eid, acid in ((eid_u, acid_u), (eid_a, acid_a)):
        att_id = (
            await db_session.execute(
                select(SiteLogEventAttachment.attachment_id)
                .where(SiteLogEventAttachment.attachment_client_id == uuid.UUID(acid))
            )
        ).scalar_one()
        await svc.acquire_attachment(
            db_session, user=(await db_session.get(User, uuid.UUID(r.json()["author_user_id"]))),
            event_id=uuid.UUID(eid), attachment_client_id=uuid.UUID(acid), mime_type="audio/m4a",
        )
        await db_session.execute(
            update(SiteLogEventAttachment)
            .where(SiteLogEventAttachment.attachment_id == att_id)
            .values(updated_at=func.now() - func.make_interval(0, 0, 0, 0, 0, 20))
        )
    # Cross-tenant copy of an assigned event.
    att_x = _att()
    r = await _declare(client, admin_token, attachments=[att_x], job_id=str(job_a.job_id))
    eid_x, acid_x = r.json()["site_log_event_id"], att_x["attachment_client_id"]
    await db_session.execute(
        update(SiteLogEvent).where(SiteLogEvent.site_log_event_id == uuid.UUID(eid_x))
        .values(tenant_id=uuid.UUID("00000000-0000-0000-0000-00000000dead"))
    )

    reset = lambda eid, acid: f"/site-log-events/{eid}/attachments/{acid}/reset"  # noqa: E731
    relink = lambda eid: f"/site-log-events/{eid}/relink-job"  # noqa: E731
    reset_body = {"reason": "stuck"}
    relink_body = {"job_id": str(job_b.job_id), "reason": "misfiled"}
    unknown = uuid.uuid4()
    denials = [
        # (label, method, url, body, token, expected)
        ("reset unknown/contributor", reset(unknown, acid_u), reset_body, contributor_token, 404),
        ("reset unknown/admin", reset(unknown, acid_u), reset_body, admin_token, 404),
        ("reset cross-tenant/admin", reset(eid_x, acid_x), reset_body, admin_token, 404),
        ("reset unreadable/other", reset(eid_u, acid_u), reset_body, other_token, 404),
        ("reset readable-author/403", reset(eid_u, acid_u), reset_body, contributor_token, 403),
        ("reset readable-via-job/403", reset(eid_a, acid_a), reset_body, other_token, 403),
        ("relink unknown/contributor", relink(unknown), relink_body, contributor_token, 404),
        ("relink unknown/admin", relink(unknown), relink_body, admin_token, 404),
        ("relink cross-tenant/admin", relink(eid_x), relink_body, admin_token, 404),
        ("relink unreadable/other", relink(eid_u), relink_body, other_token, 404),
        ("relink readable-author/403", relink(eid_a), relink_body, contributor_token, 403),
        ("relink readable-via-job/403", relink(eid_a), relink_body, other_token, 403),
    ]
    before = await _audit_counts(db_session)
    states_before = {acid: await _att_state(db_session, acid) for acid in (acid_u, acid_a)}
    for label, url, body, token, expected in denials:
        resp = await client.post(url, json=body, headers=_auth(token))
        assert resp.status_code == expected, (label, resp.status_code, resp.text)
        assert await _audit_counts(db_session) == before, label
        for acid in (acid_u, acid_a):
            assert await _att_state(db_session, acid) == states_before[acid], label
        for eid, job in ((eid_u, None), (eid_a, job_a.job_id)):
            cur = (
                await db_session.execute(
                    select(SiteLogEvent.job_id)
                    .where(SiteLogEvent.site_log_event_id == uuid.UUID(eid))
                )
            ).scalar_one()
            assert cur == job, label
    # Authorized admin: normal operation on both.
    ok = await client.post(reset(eid_a, acid_a), json=reset_body, headers=_auth(admin_token))
    assert ok.status_code == 200 and ok.json()["state"] == "failed"
    ok = await client.post(relink(eid_a), json=relink_body, headers=_auth(admin_token))
    assert ok.status_code == 200 and ok.json()["job_id"] == str(job_b.job_id)


async def test_inline_replay_edge_cases_over_http(
    client, db_session, contributor_token, site_log_session_factory
):
    """failed inline replay: 502 while the backend stays broken, 200 once it
    works; pending inline replay: 200 without any mutation."""
    from app.main import app
    from app.services.evidence_storage import EvidenceStorageError, get_evidence_storage

    class _Broken:
        backend_name = "broken"

        async def put(self, evidence_id, chunks, *, attempt_no=None):
            raise EvidenceStorageError("backend down")

        def open(self, key):
            raise AssertionError

        async def exists(self, key):
            return False

    cid = str(uuid.uuid4())
    app.dependency_overrides[get_evidence_storage] = lambda: _Broken()
    try:
        r1 = await _declare(client, contributor_token, capture_client_id=cid, body_text="x")
        r2 = await _declare(client, contributor_token, capture_client_id=cid, body_text="x")
    finally:
        app.dependency_overrides.pop(get_evidence_storage, None)
    assert (r1.status_code, r2.status_code) == (502, 502)
    inline = svc.inline_attachment_id(uuid.UUID(cid))
    assert await _att_state(db_session, str(inline)) == ("failed", 2)
    r3 = await _declare(client, contributor_token, capture_client_id=cid, body_text="x")
    assert r3.status_code == 200 and r3.json()["capture_status"] == "complete"
    assert await _att_state(db_session, str(inline)) == ("stored", 3)

    # pending inline (prior process death): replay 200, nothing changes.
    cid2 = str(uuid.uuid4())
    ok = await _declare(client, contributor_token, capture_client_id=cid2, body_text="y")
    assert ok.status_code == 201
    inline2 = svc.inline_attachment_id(uuid.UUID(cid2))
    await db_session.execute(
        update(SiteLogEventAttachment)
        .where(SiteLogEventAttachment.attachment_client_id == inline2)
        .values(state="pending")
    )
    await db_session.execute(
        update(SiteLogEvent)
        .where(SiteLogEvent.site_log_event_id == uuid.UUID(ok.json()["site_log_event_id"]))
        .values(capture_status="pending_upload")
    )
    before = await _audit_counts(db_session)
    rep = await _declare(client, contributor_token, capture_client_id=cid2, body_text="y")
    assert rep.status_code == 200, rep.text
    assert rep.json()["capture_status"] == "pending_upload"
    assert rep.json()["attachments"][0]["state"] == "pending"
    assert await _att_state(db_session, str(inline2)) == ("pending", 1)
    assert await _audit_counts(db_session) == before
