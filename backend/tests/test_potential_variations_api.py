"""Site Log → Potential Variation (DEC-PV-001): the HTTP surface and its rules.

Covers: flag / replay / key misuse; the pinned version (a changed entry is
refused as ``source_changed``, but only after a used key has been answered);
withdrawn and unreadable entries; the three identities (builder = admin,
flagger, unrelated contributor) on every route; the builder-only decision as
one unit and the builder-only note; lost source access hiding every
entry-derived field; job set/change rules; Task independence both ways; a
job a flag points at refusing deletion; the entry's current attachments
(inline-text row left out by identity); response key sets with no price or
approval field; the audit; logs without text.

Synthetic text only, modelled on the founder's simulated walk-through
("owner asks on site for two extra power points in the kitchen").
"""

from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import func, select, update

from app.core.security import create_access_token, hash_password
from app.models import (
    AttachmentState,
    CaptureStatus,
    Job,
    JobStatus,
    PotentialVariation,
    PotentialVariationAuditLog,
    SiteLogEvent,
    SiteLogEventAttachment,
    SiteLogEventRevision,
    TaskAuditLog,
)
from app.models.potential_variation import (
    TITLE_MAX_CHARS,
    validate_pv_audit_detail,
)
from app.models.task import TITLE_MAX_CHARS as TASK_TITLE_MAX_CHARS
from app.models.user import LanguageCode, User, UserRole
from app.services.potential_variations import access
from app.services.site_log.inline import inline_attachment_id

pytestmark = pytest.mark.asyncio

PV_KEYS = {
    "potential_variation_id", "title", "review_state", "ended_reason", "decision_note",
    "origin", "job", "created_by", "decided_by", "decided_at", "source", "permissions",
    "created_at", "updated_at",
}
SOURCE_KEYS = {"site_log_event_id", "revision_no", "readable", "job_id"}
DETAIL_SOURCE_KEYS = SOURCE_KEYS | {
    "pinned", "current_revision_no", "current_withdrawn", "current_attachments",
}
PINNED_KEYS = {"revision_no", "body_text", "internal_location", "occurred_at", "created_at"}
PERMISSION_KEYS = {"decide", "read_note", "set_job", "change_job"}
# Words no key of any response may contain: a flag is not a price, an
# approval, a signature or an authority to start or charge.
COMMERCIAL_WORDS = (
    "amount", "price", "cost", "approv", "sign", "authoris", "authoriz",
    "invoice", "charge", "paid", "quote",
)

KITCHEN = "Owner asked on site for two extra power points in the kitchen\nNear the island bench"
KITCHEN_TITLE = "Owner asked on site for two extra power points in the kitchen"
NOTE = "E2 shows 6 GPOs in kitchen"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _mk_user(db, *, role=UserRole.contributor, name=None):
    uid = uuid.uuid4()
    user = User(
        user_id=uid,
        full_name=name or f"User {str(uid)[:6]}",
        email=f"{uid}@example.com",
        password_hash=hash_password("x"),
        role=role,
        language_preference=LanguageCode.en,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user, create_access_token({"sub": str(uid)})


async def _mk_job(db, admin, *, status=JobStatus.active, name="Simulated job"):
    job = Job(job_id=uuid.uuid4(), job_name=name, status=status, created_by=admin.user_id)
    db.add(job)
    await db.flush()
    return job


async def _mk_entry(db, author, *, job=None, body=KITCHEN):
    """A Site Log entry written straight to the tables (no storage needed)."""
    event = SiteLogEvent(
        site_log_event_id=uuid.uuid4(),
        author_user_id=author.user_id,
        job_id=job.job_id if job is not None else None,
        capture_client_id=uuid.uuid4(),
        capture_status=CaptureStatus.complete,
    )
    db.add(event)
    await db.flush()
    db.add(
        SiteLogEventRevision(
            site_log_event_id=event.site_log_event_id,
            revision_no=1,
            body_text=body,
            actor_user_id=author.user_id,
        )
    )
    await db.flush()
    return event


async def _add_revision(db, event, revision_no, *, body=KITCHEN, withdrawn=False):
    db.add(
        SiteLogEventRevision(
            site_log_event_id=event.site_log_event_id,
            revision_no=revision_no,
            body_text=body,
            withdrawn=withdrawn,
            reason="corrected" if not withdrawn else "recorded by mistake",
            actor_user_id=event.author_user_id,
        )
    )
    await db.flush()


async def _flag(client, token, event_id, *, revision_no=1, **body):
    payload = {
        "create_client_id": str(uuid.uuid4()),
        "title": KITCHEN_TITLE,
        "source_revision_no": revision_no,
        **body,
    }
    return await client.post(
        f"/site-log-events/{event_id}/potential-variations", json=payload, headers=_auth(token)
    )


async def _decide(client, token, pv_id, state, reason=None, note=None):
    return await client.patch(
        f"/potential-variations/{pv_id}",
        json={"decision": {"review_state": state, "ended_reason": reason, "note": note}},
        headers=_auth(token),
    )


async def _audit(db, pv_id=None):
    q = select(PotentialVariationAuditLog).order_by(PotentialVariationAuditLog.created_at)
    if pv_id is not None:
        q = q.where(PotentialVariationAuditLog.potential_variation_id == uuid.UUID(str(pv_id)))
    return list((await db.execute(q)).scalars().all())


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


def _all_keys(obj) -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            keys |= _all_keys(v)
    return keys


def _assert_no_commercial_keys(body) -> None:
    for key in _all_keys(body):
        for word in COMMERCIAL_WORDS:
            assert word not in key.lower(), f"response key {key!r} reads as {word!r}"


# --------------------------------------------------------------- flagging


async def test_flag_shape_and_confirmation_record(
    client, db_session, seeded_admin, seeded_contributor, contributor_token
):
    job = await _mk_job(db_session, seeded_admin, name="Simulated kitchen job")
    entry = await _mk_entry(db_session, seeded_contributor, job=job)
    r = await _flag(client, contributor_token, entry.site_log_event_id,
                    title="  Two extra power points, kitchen  ")
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == PV_KEYS
    assert set(body["source"]) == SOURCE_KEYS
    assert set(body["permissions"]) == PERMISSION_KEYS
    _assert_no_commercial_keys(body)
    assert body["title"] == "Two extra power points, kitchen"  # trimmed
    assert body["review_state"] == "to_check" and body["origin"] == "manual"
    assert body["ended_reason"] is None and body["decision_note"] is None
    assert body["decided_by"] is None and body["decided_at"] is None
    assert body["job"] == {
        "job_id": str(job.job_id), "job_name": "Simulated kitchen job",
        "job_code": None, "status": "active",
    }
    assert body["created_by"]["user_id"] == str(seeded_contributor.user_id)
    assert body["source"] == {
        "site_log_event_id": str(entry.site_log_event_id),
        "revision_no": 1,
        "readable": True,
        "job_id": str(job.job_id),
    }
    # The flagger can neither decide nor read the builder's note.
    assert body["permissions"] == {
        "decide": False, "read_note": False, "set_job": False, "change_job": False,
    }
    assert "proposed_title" not in r.text

    [created] = await _audit(db_session, body["potential_variation_id"])
    assert created.action == "created"
    assert created.actor_user_id == seeded_contributor.user_id
    cf = created.changed_fields
    assert cf["source_site_log_event_id"] == str(entry.site_log_event_id)
    assert cf["source_revision_no"] == 1
    assert cf["proposed_title"] == KITCHEN_TITLE  # derived by the server
    assert cf["final_title"] == "Two extra power points, kitchen"
    assert cf["title_edited"] is True
    assert cf["job_id"] == str(job.job_id)
    assert cf["review_state"] == "to_check" and cf["origin"] == "manual"
    assert len(cf["create_fingerprint"]) == 64
    assert "body_text" not in cf


async def test_unedited_title_is_recorded_as_not_edited(
    client, db_session, seeded_contributor, contributor_token
):
    entry = await _mk_entry(db_session, seeded_contributor)
    r = await _flag(client, contributor_token, entry.site_log_event_id)
    assert r.status_code == 201, r.text
    [created] = await _audit(db_session, r.json()["potential_variation_id"])
    assert created.changed_fields["proposed_title"] == KITCHEN_TITLE
    assert created.changed_fields["title_edited"] is False


async def test_replay_returns_the_same_flag_and_key_misuse_is_refused(
    client, db_session, seeded_contributor, contributor_token
):
    entry = await _mk_entry(db_session, seeded_contributor)
    key = str(uuid.uuid4())
    r1 = await _flag(client, contributor_token, entry.site_log_event_id, create_client_id=key)
    r2 = await _flag(client, contributor_token, entry.site_log_event_id, create_client_id=key)
    assert (r1.status_code, r2.status_code) == (201, 200)
    assert r1.json()["potential_variation_id"] == r2.json()["potential_variation_id"]

    misuse = await _flag(client, contributor_token, entry.site_log_event_id,
                         create_client_id=key, title="Something else")
    assert misuse.status_code == 409
    assert misuse.json()["detail"] == (
        "create_client_id reused with a different potential variation"
    )
    assert await _count(db_session, PotentialVariation) == 1
    assert [a.action for a in await _audit(db_session)] == ["created"]


async def test_a_used_key_is_answered_before_the_version_check(
    client, db_session, seeded_contributor, contributor_token, admin_token
):
    """The entry is corrected after the flag was saved: the same request
    sent again (its answer was lost) still gets its flag, never
    ``source_changed`` - otherwise the app would give the key up and a
    second flag could follow."""
    entry = await _mk_entry(db_session, seeded_contributor)
    key = str(uuid.uuid4())
    first = await _flag(client, contributor_token, entry.site_log_event_id, create_client_id=key)
    assert first.status_code == 201
    await _add_revision(db_session, entry, 2, body="Owner now wants four power points")

    replay = await _flag(client, contributor_token, entry.site_log_event_id, create_client_id=key)
    assert replay.status_code == 200, replay.text
    assert replay.json()["potential_variation_id"] == first.json()["potential_variation_id"]

    # A NEW flag made from the version the person saw before the correction
    # is refused: they look at the entry again and confirm again.
    stale = await _flag(client, contributor_token, entry.site_log_event_id, revision_no=1)
    assert stale.status_code == 409
    assert stale.json()["detail"].startswith("source_changed:")

    fresh = await _flag(client, contributor_token, entry.site_log_event_id, revision_no=2,
                        title="Owner now wants four power points")
    assert fresh.status_code == 201, fresh.text
    assert fresh.json()["source"]["revision_no"] == 2

    # The first flag still shows the version it was made from.
    detail = await client.get(
        f"/potential-variations/{first.json()['potential_variation_id']}",
        headers=_auth(admin_token),
    )
    src = detail.json()["source"]
    assert src["pinned"]["revision_no"] == 1 and src["pinned"]["body_text"] == KITCHEN
    assert src["current_revision_no"] == 2 and src["current_withdrawn"] is False


async def test_withdrawn_and_unreadable_entries_are_refused(
    client, db_session, seeded_contributor, contributor_token
):
    withdrawn = await _mk_entry(db_session, seeded_contributor)
    await _add_revision(db_session, withdrawn, 2, withdrawn=True)
    for rev in (1, 2):
        r = await _flag(client, contributor_token, withdrawn.site_log_event_id, revision_no=rev)
        assert r.status_code == 409
        assert r.json()["detail"] == "The source entry is withdrawn"

    # A job-less entry someone else wrote: not readable, so not flaggable.
    other, _ = await _mk_user(db_session)
    hidden = await _mk_entry(db_session, other)
    r = await _flag(client, contributor_token, hidden.site_log_event_id)
    assert r.status_code == 404
    assert r.json()["detail"] == "Site log event not found"
    r = await _flag(client, contributor_token, uuid.uuid4())
    assert r.status_code == 404
    assert await _count(db_session, PotentialVariation) == 0


async def test_title_and_version_validation(
    client, db_session, seeded_contributor, contributor_token
):
    entry = await _mk_entry(db_session, seeded_contributor)
    for title in ("   ", "x" * (TITLE_MAX_CHARS + 1)):
        r = await _flag(client, contributor_token, entry.site_log_event_id, title=title)
        assert r.status_code == 422, r.text
    r = await _flag(client, contributor_token, entry.site_log_event_id, revision_no=0)
    assert r.status_code == 422
    r = await client.post(
        f"/site-log-events/{entry.site_log_event_id}/potential-variations",
        json={"create_client_id": str(uuid.uuid4()), "title": KITCHEN_TITLE},
        headers=_auth(contributor_token),
    )
    assert r.status_code == 422  # the version the person saw is required
    # A version that does not exist is "changed", never pinned.
    r = await _flag(client, contributor_token, entry.site_log_event_id, revision_no=7)
    assert r.status_code == 409 and r.json()["detail"].startswith("source_changed:")
    assert await _count(db_session, PotentialVariation) == 0


async def test_title_limit_matches_the_task_prefill():
    # The proposed title comes from the task service's derivation, cut to
    # the task limit; a flag must be able to hold it unchanged.
    assert TITLE_MAX_CHARS == TASK_TITLE_MAX_CHARS


async def test_audit_guard_refuses_entry_text():
    with pytest.raises(ValueError):
        validate_pv_audit_detail({"from": {"body_text": "x"}})
    assert validate_pv_audit_detail({"note": NOTE, "final_title": "t"})


# ------------------------------------------------------------ three identities


async def test_who_sees_what_admin_flagger_and_unrelated_contributor(
    client, db_session, seeded_admin, admin_token, seeded_contributor, contributor_token
):
    outsider, outsider_token = await _mk_user(db_session)
    job = await _mk_job(db_session, seeded_admin)
    # On a job: every active user may read the ENTRY - but not the flag.
    entry = await _mk_entry(db_session, seeded_contributor, job=job)
    pv_id = (await _flag(client, contributor_token, entry.site_log_event_id)).json()[
        "potential_variation_id"
    ]

    for token in (admin_token, contributor_token):
        listed = await client.get("/potential-variations", headers=_auth(token))
        assert [p["potential_variation_id"] for p in listed.json()] == [pv_id]
        from_entry = await client.get(
            f"/site-log-events/{entry.site_log_event_id}/potential-variations",
            headers=_auth(token),
        )
        assert [p["potential_variation_id"] for p in from_entry.json()] == [pv_id]
        detail = await client.get(f"/potential-variations/{pv_id}", headers=_auth(token))
        assert detail.status_code == 200
        assert detail.json()["title"] == KITCHEN_TITLE

    assert (await client.get("/potential-variations", headers=_auth(outsider_token))).json() == []
    from_entry = await client.get(
        f"/site-log-events/{entry.site_log_event_id}/potential-variations",
        headers=_auth(outsider_token),
    )
    assert from_entry.status_code == 200 and from_entry.json() == []
    r = await client.get(f"/potential-variations/{pv_id}", headers=_auth(outsider_token))
    assert r.status_code == 404
    for patch in ({"decision": {"review_state": "following_up"}}, {"job_id": str(job.job_id)}):
        r = await client.patch(f"/potential-variations/{pv_id}", json=patch,
                               headers=_auth(outsider_token))
        assert r.status_code == 404, patch

    # The outsider may still flag the same entry (they can read it).
    mine = await _flag(client, outsider_token, entry.site_log_event_id)
    assert mine.status_code == 201
    # ... and then sees only their own flag.
    listed = await client.get("/potential-variations", headers=_auth(outsider_token))
    assert [p["potential_variation_id"] for p in listed.json()] == [
        mine.json()["potential_variation_id"]
    ]
    admin_list = await client.get("/potential-variations", headers=_auth(admin_token))
    assert len(admin_list.json()) == 2
    assert admin_list.json()[0]["permissions"] == {
        "decide": True, "read_note": True, "set_job": False, "change_job": True,
    }


async def test_visibility_clause_matches_the_python_rule(db_session, seeded_admin):
    flagger, _ = await _mk_user(db_session)
    other, _ = await _mk_user(db_session)
    entry = await _mk_entry(db_session, flagger)
    for creator in (flagger, other, seeded_admin):
        db_session.add(
            PotentialVariation(
                source_site_log_event_id=entry.site_log_event_id, source_revision_no=1,
                title="t", created_by_user_id=creator.user_id, create_client_id=uuid.uuid4(),
            )
        )
    await db_session.flush()
    pvs = (await db_session.execute(select(PotentialVariation))).scalars().all()
    for user in (seeded_admin, flagger, other):
        clause = access.visible_pvs_clause(user)
        q = select(PotentialVariation.potential_variation_id)
        if clause is not None:
            q = q.where(clause)
        by_sql = set((await db_session.execute(q)).scalars().all())
        by_python = {p.potential_variation_id for p in pvs if access.can_view_pv(user, p)}
        assert by_sql == by_python


# -------------------------------------------------------------- decisions


async def test_only_the_builder_decides_and_reads_the_note(
    client, db_session, seeded_admin, admin_token, seeded_contributor, contributor_token
):
    entry = await _mk_entry(db_session, seeded_contributor)
    pv_id = (await _flag(client, contributor_token, entry.site_log_event_id)).json()[
        "potential_variation_id"
    ]
    r = await _decide(client, admin_token, pv_id, "following_up", note="Client to confirm")
    assert r.status_code == 200, r.text
    assert r.json()["decision_note"] == "Client to confirm"
    assert r.json()["decided_by"]["user_id"] == str(seeded_admin.user_id)
    assert r.json()["decided_at"] is not None

    seen = (await client.get(f"/potential-variations/{pv_id}",
                             headers=_auth(contributor_token))).json()
    assert seen["review_state"] == "following_up"
    assert seen["decision_note"] is None  # the note is the builder's
    assert seen["permissions"]["decide"] is False
    assert seen["permissions"]["read_note"] is False
    listed = (await client.get("/potential-variations", headers=_auth(contributor_token))).json()
    assert listed[0]["decision_note"] is None

    # Any decision from the flagger is refused BEFORE it is compared with
    # the current one - even the identical decision, so the hidden note
    # cannot be guessed through no-op answers.
    for state, note in (("following_up", "Client to confirm"), ("ended", None), ("to_check", None)):
        reason = "already_in_contract" if state == "ended" else None
        r = await _decide(client, contributor_token, pv_id, state, reason, note)
        assert r.status_code == 403, (state, r.text)
    assert [a.action for a in await _audit(db_session, pv_id)] == ["created", "decided"]


async def test_decision_rules_end_with_reason_and_reopen(
    client, db_session, admin_token, seeded_contributor, contributor_token
):
    """The simulated walk-through: the builder checks the contract and ends
    follow-up as "already in the contract", then re-opens it."""
    entry = await _mk_entry(db_session, seeded_contributor)
    pv_id = (await _flag(client, contributor_token, entry.site_log_event_id)).json()[
        "potential_variation_id"
    ]
    bad = [
        ("ended", None, None),  # ending needs a reason
        ("ended", "other", None),  # "other" needs a note
        ("ended", "other", "   "),  # a blank note is no note
        ("following_up", "already_in_contract", None),  # only ended has a reason
        ("ended", "already_in_contract", "x" * 501),  # note too long
    ]
    for state, reason, note in bad:
        r = await _decide(client, admin_token, pv_id, state, reason, note)
        assert r.status_code == 422, (state, reason, note, r.text)

    r = await _decide(client, admin_token, pv_id, "ended", "already_in_contract", f"  {NOTE}  ")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["review_state"], body["ended_reason"], body["decision_note"]) == (
        "ended", "already_in_contract", NOTE,
    )
    # Still found under its job filter / all-jobs list after it ended.
    listed = await client.get("/potential-variations?review_state=ended",
                              headers=_auth(admin_token))
    assert [p["potential_variation_id"] for p in listed.json()] == [pv_id]

    # The same decision again is a no-op: no audit row.
    again = await _decide(client, admin_token, pv_id, "ended", "already_in_contract", NOTE)
    assert again.status_code == 200
    assert again.json()["updated_at"] == body["updated_at"]

    reopened = await _decide(client, admin_token, pv_id, "to_check")
    assert reopened.status_code == 200
    assert reopened.json()["ended_reason"] is None
    assert reopened.json()["decision_note"] is None

    other = await _decide(client, admin_token, pv_id, "ended", "other", "Client changed mind")
    assert other.status_code == 200

    rows = await _audit(db_session, pv_id)
    assert [a.action for a in rows] == ["created", "decided", "decided", "decided"]
    assert rows[1].changed_fields == {
        "from": {"review_state": "to_check", "ended_reason": None, "note": None},
        "to": {"review_state": "ended", "ended_reason": "already_in_contract", "note": NOTE},
    }
    # Re-opening cleared the row's reason; the audit kept it.
    assert rows[2].changed_fields["from"]["ended_reason"] == "already_in_contract"
    assert rows[2].changed_fields["to"]["ended_reason"] is None


async def test_patch_needs_something_to_change(
    client, db_session, admin_token, seeded_contributor, contributor_token
):
    entry = await _mk_entry(db_session, seeded_contributor)
    pv_id = (await _flag(client, contributor_token, entry.site_log_event_id)).json()[
        "potential_variation_id"
    ]
    r = await client.patch(f"/potential-variations/{pv_id}", json={}, headers=_auth(admin_token))
    assert r.status_code == 422
    r = await client.patch(f"/potential-variations/{pv_id}", json={"title": "new"},
                           headers=_auth(admin_token))
    assert r.status_code == 422  # the title is fixed after save


# ------------------------------------------------------ lost source access


async def test_lost_source_access_hides_every_entry_derived_field(
    client, db_session, seeded_admin, admin_token, contributor_token, seeded_contributor
):
    """The flagger could read the entry when flagging (it was on a job). If
    that access goes (simulated here by taking the entry off its job, which
    leaves only its author and admins able to read it), the flag stays
    theirs, but nothing taken from the entry reaches them: not the title it
    was prefilled from, not the text, not the attachment summary."""
    author, _ = await _mk_user(db_session)
    job = await _mk_job(db_session, seeded_admin)
    entry = await _mk_entry(db_session, author, job=job)
    key = str(uuid.uuid4())
    pv_id = (await _flag(client, contributor_token, entry.site_log_event_id,
                         create_client_id=key)).json()["potential_variation_id"]

    await db_session.execute(
        update(SiteLogEvent)
        .where(SiteLogEvent.site_log_event_id == entry.site_log_event_id)
        .values(job_id=None)
    )
    await db_session.flush()

    detail = await client.get(f"/potential-variations/{pv_id}", headers=_auth(contributor_token))
    assert detail.status_code == 200
    d = detail.json()
    assert set(d["source"]) == DETAIL_SOURCE_KEYS
    assert d["title"] is None
    assert d["source"] == {
        "site_log_event_id": str(entry.site_log_event_id),
        "revision_no": 1,
        "readable": False,
        "job_id": None,
        "pinned": None,
        "current_revision_no": None,
        "current_withdrawn": None,
        "current_attachments": None,
    }
    assert KITCHEN_TITLE not in detail.text and "island" not in detail.text
    # The flag keeps its own job (identity only).
    assert d["job"]["job_id"] == str(job.job_id)

    for url in ("/potential-variations", f"/potential-variations?create_client_id={key}"):
        listed = await client.get(url, headers=_auth(contributor_token))
        assert [p["title"] for p in listed.json()] == [None], url
        assert KITCHEN_TITLE not in listed.text
    r = await client.get(
        f"/site-log-events/{entry.site_log_event_id}/potential-variations",
        headers=_auth(contributor_token),
    )
    assert r.status_code == 404

    # The builder still reads everything.
    admin_view = (await client.get(f"/potential-variations/{pv_id}",
                                   headers=_auth(admin_token))).json()
    assert admin_view["title"] == KITCHEN_TITLE
    assert admin_view["source"]["pinned"]["body_text"] == KITCHEN


async def test_detail_shows_the_entrys_current_attachments_without_the_inline_row(
    client, db_session, admin_token, seeded_contributor, contributor_token
):
    entry = await _mk_entry(db_session, seeded_contributor)
    pv_id = (await _flag(client, contributor_token, entry.site_log_event_id)).json()[
        "potential_variation_id"
    ]
    # Added AFTER the flag: attachments belong to the entry, not a version.
    db_session.add_all([
        SiteLogEventAttachment(
            site_log_event_id=entry.site_log_event_id,
            attachment_client_id=inline_attachment_id(entry.capture_client_id),
            declared_media_type="text",
        ),
        SiteLogEventAttachment(
            site_log_event_id=entry.site_log_event_id,
            attachment_client_id=uuid.uuid4(),
            declared_media_type="image",
        ),
        SiteLogEventAttachment(  # a .txt the user attached: "text" too, kept
            site_log_event_id=entry.site_log_event_id,
            attachment_client_id=uuid.uuid4(),
            declared_media_type="text",
            state=AttachmentState.failed,
        ),
    ])
    await db_session.flush()
    for token in (contributor_token, admin_token):
        src = (await client.get(f"/potential-variations/{pv_id}",
                                headers=_auth(token))).json()["source"]
        assert set(src["pinned"]) == PINNED_KEYS
        assert sorted(
            (a["declared_media_type"], a["state"]) for a in src["current_attachments"]
        ) == [("image", "awaiting_upload"), ("text", "failed")]


# ------------------------------------------------------------------- jobs


async def test_job_set_and_change_rules(
    client, db_session, seeded_admin, admin_token, seeded_contributor, contributor_token
):
    entry = await _mk_entry(db_session, seeded_contributor)  # job-less
    job_a = await _mk_job(db_session, seeded_admin, name="A")
    job_b = await _mk_job(db_session, seeded_admin, name="B")
    done = await _mk_job(db_session, seeded_admin, name="Done", status=JobStatus.completed)
    created = (await _flag(client, contributor_token, entry.site_log_event_id)).json()
    pv_id = created["potential_variation_id"]
    assert created["job"] is None and created["permissions"]["set_job"] is True

    no_job = await client.get("/potential-variations?no_job=true", headers=_auth(admin_token))
    assert [p["potential_variation_id"] for p in no_job.json()] == [pv_id]
    both = await client.get(f"/potential-variations?no_job=true&job_id={job_a.job_id}",
                            headers=_auth(admin_token))
    assert both.status_code == 422

    async def set_job(token, job_id):
        return await client.patch(f"/potential-variations/{pv_id}", json={"job_id": job_id},
                                  headers=_auth(token))

    assert (await set_job(contributor_token, str(done.job_id))).status_code == 422
    assert (await set_job(contributor_token, str(uuid.uuid4()))).status_code == 404
    r = await set_job(contributor_token, str(job_a.job_id))
    assert r.status_code == 200 and r.json()["job"]["job_id"] == str(job_a.job_id)
    assert r.json()["permissions"]["set_job"] is False

    # Once set, the flagger cannot move it; the same job again is a no-op.
    assert (await set_job(contributor_token, str(job_b.job_id))).status_code == 403
    assert (await set_job(contributor_token, str(job_a.job_id))).status_code == 200
    assert (await set_job(admin_token, None)).status_code == 422
    r = await set_job(admin_token, str(job_b.job_id))
    assert r.status_code == 200 and r.json()["job"]["job_id"] == str(job_b.job_id)

    by_job = await client.get(f"/potential-variations?job_id={job_b.job_id}",
                              headers=_auth(admin_token))
    assert [p["potential_variation_id"] for p in by_job.json()] == [pv_id]
    rows = [a for a in await _audit(db_session, pv_id) if a.action == "job_set"]
    assert [a.changed_fields for a in rows] == [
        {"from": None, "to": str(job_a.job_id)},
        {"from": str(job_a.job_id), "to": str(job_b.job_id)},
    ]


async def test_a_job_a_flag_points_at_is_archived_not_deleted(
    client, db_session, seeded_admin, admin_token, seeded_contributor, contributor_token
):
    job = await _mk_job(db_session, seeded_admin, name="Only a flag")
    entry = await _mk_entry(db_session, seeded_contributor)
    pv_id = (await _flag(client, contributor_token, entry.site_log_event_id)).json()[
        "potential_variation_id"
    ]
    r = await client.patch(f"/potential-variations/{pv_id}", json={"job_id": str(job.job_id)},
                           headers=_auth(contributor_token))
    assert r.status_code == 200
    r = await client.delete(f"/jobs/{job.job_id}", headers=_auth(admin_token))
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "Job has 1 potential variation and cannot be deleted. Archive it instead."
    )


# ------------------------------------------------------- Task independence


async def test_task_and_flag_never_change_each_other(
    client, db_session, admin_token, seeded_contributor, contributor_token
):
    entry = await _mk_entry(db_session, seeded_contributor)
    # A flag with no task first: no task is required.
    pv = (await _flag(client, contributor_token, entry.site_log_event_id)).json()
    task = await client.post(
        f"/site-log-events/{entry.site_log_event_id}/tasks",
        json={"create_client_id": str(uuid.uuid4()), "title": "Run two extra GPO circuits"},
        headers=_auth(contributor_token),
    )
    assert task.status_code == 201, task.text
    task_id = task.json()["task_id"]

    before = (await client.get(f"/potential-variations/{pv['potential_variation_id']}",
                               headers=_auth(admin_token))).json()
    pv_audit_before = len(await _audit(db_session, pv["potential_variation_id"]))
    r = await client.patch(f"/tasks/{task_id}", json={"status": "done"},
                           headers=_auth(contributor_token))
    assert r.status_code == 200 and r.json()["status"] == "done"
    after = (await client.get(f"/potential-variations/{pv['potential_variation_id']}",
                              headers=_auth(admin_token))).json()
    assert after == before  # state, updated_at and everything else unchanged
    assert len(await _audit(db_session, pv["potential_variation_id"])) == pv_audit_before

    task_before = (await client.get(f"/tasks/{task_id}", headers=_auth(admin_token))).json()
    task_audit_before = await _count(db_session, TaskAuditLog)
    r = await _decide(client, admin_token, pv["potential_variation_id"], "ended",
                      "handled_outside_forey")
    assert r.status_code == 200
    task_after = (await client.get(f"/tasks/{task_id}", headers=_auth(admin_token))).json()
    assert task_after == task_before
    assert await _count(db_session, TaskAuditLog) == task_audit_before


# ------------------------------------------------- entry untouched, logs


async def test_the_entry_is_never_written(
    client, db_session, admin_token, seeded_contributor, contributor_token
):
    entry = await _mk_entry(db_session, seeded_contributor)
    snapshot = [
        (r.revision_no, r.body_text, r.withdrawn, r.created_at)
        for r in (await db_session.execute(select(SiteLogEventRevision))).scalars()
    ]
    events_before = await _count(db_session, SiteLogEvent)
    pv_id = (await _flag(client, contributor_token, entry.site_log_event_id)).json()[
        "potential_variation_id"
    ]
    await _decide(client, admin_token, pv_id, "ended", "client_not_proceeding")
    db_session.expire_all()
    assert [
        (r.revision_no, r.body_text, r.withdrawn, r.created_at)
        for r in (await db_session.execute(select(SiteLogEventRevision))).scalars()
    ] == snapshot
    assert await _count(db_session, SiteLogEvent) == events_before


async def test_logs_carry_ids_and_states_never_text(
    client, db_session, admin_token, seeded_contributor, contributor_token, caplog
):
    entry = await _mk_entry(db_session, seeded_contributor)
    with caplog.at_level(logging.INFO, logger="app.services.potential_variations"):
        pv_id = (await _flag(client, contributor_token, entry.site_log_event_id)).json()[
            "potential_variation_id"
        ]
        await _decide(client, admin_token, pv_id, "ended", "already_in_contract", NOTE)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert pv_id in text and "already_in_contract" in text
    for secret in (KITCHEN_TITLE, "island", NOTE):
        assert secret not in text
