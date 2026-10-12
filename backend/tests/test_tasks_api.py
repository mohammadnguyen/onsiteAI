"""Site Log → Task (plan v2): the HTTP surface and its rules.

Covers plan v2 §7: create / replay / key misuse, the create_client_id
lookup, entry access (404) and withdrawal (409), D1 assignee scope, the
assignee of a task from a job-less entry seeing the task but not the entry
or its files, the proposed title never leaving the audit, the server-side
derivation of that title, the task keeping its own job when the entry is
re-linked, job set/change rules, status and edit permissions, one audit row
per changed field, the entry's revisions untouched, and list visibility.
Synthetic text only.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.core.security import create_access_token, hash_password
from app.models import (
    CaptureStatus,
    Job,
    JobStatus,
    SiteLogEvent,
    SiteLogEventRevision,
    Task,
    TaskAuditLog,
)
from app.models.task import validate_task_audit_detail
from app.models.user import LanguageCode, User, UserRole
from app.services import tasks as task_svc
from app.services.tasks import access

pytestmark = pytest.mark.asyncio

TASK_KEYS = {
    "task_id", "title", "status", "origin", "due_date", "job", "assignee",
    "created_by", "source", "permissions", "created_at", "updated_at",
}
SOURCE_KEYS = {"site_log_event_id", "revision_no", "readable", "job_id"}
PERMISSION_KEYS = {"change_status", "edit", "assign_others", "set_job", "change_job"}
ROOF = "Engineer asks for extra roof bracing"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _mk_user(db, *, role=UserRole.contributor, active=True, name=None):
    uid = uuid.uuid4()
    user = User(
        user_id=uid,
        full_name=name or f"User {str(uid)[:6]}",
        email=f"{uid}@example.com",
        password_hash=hash_password("x"),
        role=role,
        language_preference=LanguageCode.en,
        is_active=active,
    )
    db.add(user)
    await db.flush()
    return user, create_access_token({"sub": str(uid)})


async def _mk_job(db, admin, *, status=JobStatus.active, name="Job"):
    job = Job(job_id=uuid.uuid4(), job_name=name, status=status, created_by=admin.user_id)
    db.add(job)
    await db.flush()
    return job


async def _mk_entry(db, author, *, job=None, body=ROOF, withdrawn=False):
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
    if withdrawn:
        db.add(
            SiteLogEventRevision(
                site_log_event_id=event.site_log_event_id,
                revision_no=2,
                body_text=body,
                withdrawn=True,
                reason="recorded by mistake",
                actor_user_id=author.user_id,
            )
        )
    await db.flush()
    return event


async def _create(client, token, event_id, **body):
    payload = {"create_client_id": str(uuid.uuid4()), "title": ROOF, **body}
    return await client.post(
        f"/site-log-events/{event_id}/tasks", json=payload, headers=_auth(token)
    )


async def _audit_rows(db, task_id=None):
    q = select(TaskAuditLog).order_by(TaskAuditLog.created_at)
    if task_id is not None:
        q = q.where(TaskAuditLog.task_id == uuid.UUID(str(task_id)))
    return list((await db.execute(q)).scalars().all())


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


# --------------------------------------------------------------- create


async def test_create_happy_path_shape_and_confirmation_record(
    client, db_session, seeded_admin, seeded_contributor, contributor_token
):
    job = await _mk_job(db_session, seeded_admin, name="Smith St")
    entry = await _mk_entry(db_session, seeded_contributor, job=job)
    r = await _create(
        client, contributor_token, entry.site_log_event_id,
        title="  Add roof bracing  ", due_date="2026-10-17",
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == TASK_KEYS
    assert set(body["source"]) == SOURCE_KEYS
    assert set(body["permissions"]) == PERMISSION_KEYS
    assert body["title"] == "Add roof bracing"  # trimmed
    assert body["status"] == "to_do" and body["origin"] == "manual"
    assert body["due_date"] == "2026-10-17"
    assert body["job"]["job_id"] == str(job.job_id) and body["job"]["job_name"] == "Smith St"
    assert set(body["job"]) == {"job_id", "job_name", "job_code", "status"}  # no money
    assert body["created_by"]["user_id"] == str(seeded_contributor.user_id)
    assert body["assignee"] is None
    assert body["source"] == {
        "site_log_event_id": str(entry.site_log_event_id),
        "revision_no": 1,
        "readable": True,
        "job_id": str(job.job_id),
    }
    assert body["permissions"] == {
        "change_status": True, "edit": True, "assign_others": False,
        "set_job": False, "change_job": False,
    }
    assert "proposed_title" not in r.text

    rows = await _audit_rows(db_session, body["task_id"])
    assert [row.action for row in rows] == ["created"]
    created = rows[0]
    assert created.actor_user_id == seeded_contributor.user_id
    cf = created.changed_fields
    assert cf["source_site_log_event_id"] == str(entry.site_log_event_id)
    assert cf["source_revision_no"] == 1
    assert cf["proposed_title"] == ROOF  # derived by the server
    assert cf["final_title"] == "Add roof bracing"
    assert cf["title_edited"] is True
    assert cf["due_date"] == "2026-10-17" and cf["job_id"] == str(job.job_id)
    assert cf["origin"] == "manual" and len(cf["create_fingerprint"]) == 64


async def test_unedited_title_is_recorded_as_not_edited(
    client, db_session, seeded_contributor, contributor_token
):
    body = "\n\n   Engineer asks for extra roof bracing   \r\nNorth side, before Friday"
    entry = await _mk_entry(db_session, seeded_contributor, body=body)
    r = await _create(client, contributor_token, entry.site_log_event_id, title=ROOF)
    assert r.status_code == 201, r.text
    [created] = await _audit_rows(db_session, r.json()["task_id"])
    assert created.changed_fields["proposed_title"] == ROOF
    assert created.changed_fields["title_edited"] is False


async def test_replay_returns_same_task_and_key_misuse_is_refused(
    client, db_session, seeded_contributor, contributor_token
):
    entry = await _mk_entry(db_session, seeded_contributor)
    key = str(uuid.uuid4())
    r1 = await _create(client, contributor_token, entry.site_log_event_id, create_client_id=key)
    assert r1.status_code == 201
    r2 = await _create(client, contributor_token, entry.site_log_event_id, create_client_id=key)
    assert r2.status_code == 200 and r2.json()["task_id"] == r1.json()["task_id"]
    assert await _count(db_session, Task) == 1
    assert len(await _audit_rows(db_session)) == 1
    r3 = await _create(
        client, contributor_token, entry.site_log_event_id, create_client_id=key,
        title="Something else",
    )
    assert r3.status_code == 409
    other_entry = await _mk_entry(db_session, seeded_contributor)
    r4 = await _create(client, contributor_token, other_entry.site_log_event_id,
                       create_client_id=key)
    assert r4.status_code == 409  # same key, different source entry
    # A new key is a deliberate second task from the same entry.
    r5 = await _create(client, contributor_token, entry.site_log_event_id)
    assert r5.status_code == 201 and r5.json()["task_id"] != r1.json()["task_id"]
    assert await _count(db_session, Task) == 2


async def test_lost_race_on_the_key_returns_the_existing_task(
    client, db_session, seeded_contributor, contributor_token, monkeypatch
):
    """The SAVEPOINT path: the pre-check misses, the insert hits the unique
    key, and the request answers with the task the other request made."""
    from app.services.tasks import create as create_module

    entry = await _mk_entry(db_session, seeded_contributor)
    key = str(uuid.uuid4())
    first = await _create(client, contributor_token, entry.site_log_event_id,
                          create_client_id=key)
    assert first.status_code == 201
    real = create_module._by_client_id
    calls = {"n": 0}

    async def miss_once(db, user, cid):
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(db, user, cid)

    monkeypatch.setattr(create_module, "_by_client_id", miss_once)
    again = await _create(client, contributor_token, entry.site_log_event_id,
                          create_client_id=key)
    assert calls["n"] == 2, "the insert path must have been taken"
    assert again.status_code == 200 and again.json()["task_id"] == first.json()["task_id"]
    assert await _count(db_session, Task) == 1


async def test_lookup_by_create_client_id_returns_only_the_callers_own(
    client, db_session, seeded_admin, seeded_contributor, contributor_token, admin_token
):
    job = await _mk_job(db_session, seeded_admin)
    entry = await _mk_entry(db_session, seeded_contributor, job=job)
    key = str(uuid.uuid4())
    made = await _create(client, contributor_token, entry.site_log_event_id,
                         create_client_id=key)
    assert made.status_code == 201
    mine = await client.get(f"/tasks?create_client_id={key}", headers=_auth(contributor_token))
    assert [t["task_id"] for t in mine.json()] == [made.json()["task_id"]]
    # The admin can see the task, but the lookup is about one's OWN create.
    other = await client.get(f"/tasks?create_client_id={key}", headers=_auth(admin_token))
    assert other.status_code == 200 and other.json() == []
    unknown = await client.get(f"/tasks?create_client_id={uuid.uuid4()}",
                               headers=_auth(contributor_token))
    assert unknown.json() == []


async def test_entry_access_and_withdrawal(
    client, db_session, seeded_admin, seeded_contributor, contributor_token
):
    _other, other_token = await _mk_user(db_session)
    jobless = await _mk_entry(db_session, seeded_contributor)
    # Someone who cannot read the entry cannot make a task from it.
    assert (await _create(client, other_token, jobless.site_log_event_id)).status_code == 404
    assert (await _create(client, contributor_token, uuid.uuid4())).status_code == 404
    assert await _count(db_session, Task) == 0

    entry = await _mk_entry(db_session, seeded_contributor)
    key = str(uuid.uuid4())
    made = await _create(client, contributor_token, entry.site_log_event_id,
                         create_client_id=key)
    assert made.status_code == 201
    # Withdraw the entry afterwards.
    db_session.add(SiteLogEventRevision(
        site_log_event_id=entry.site_log_event_id, revision_no=2, body_text=ROOF,
        withdrawn=True, reason="mistake", actor_user_id=seeded_contributor.user_id,
    ))
    await db_session.flush()
    assert (await _create(client, contributor_token, entry.site_log_event_id)).status_code == 409
    # The create that already went through still answers with its task.
    replay = await _create(client, contributor_token, entry.site_log_event_id,
                           create_client_id=key)
    assert replay.status_code == 200 and replay.json()["task_id"] == made.json()["task_id"]

    withdrawn = await _mk_entry(db_session, seeded_contributor, withdrawn=True)
    refused = await _create(client, contributor_token, withdrawn.site_log_event_id)
    assert refused.status_code == 409


async def test_validation(client, db_session, seeded_contributor, contributor_token):
    entry = await _mk_entry(db_session, seeded_contributor)
    eid = entry.site_log_event_id
    assert (await _create(client, contributor_token, eid, title="   ")).status_code == 422
    assert (await _create(client, contributor_token, eid, title="x" * 201)).status_code == 422
    assert (await _create(client, contributor_token, eid, title="x" * 200)).status_code == 201
    assert (await _create(client, contributor_token, eid, due_date="17/10/2026")).status_code == 422
    assert (await _create(client, contributor_token, eid, proposed_title="x")).status_code == 422
    assert (await client.post(f"/site-log-events/{eid}/tasks", json={})).status_code == 401


# ------------------------------------------------------------- assignees (D1)


async def test_assignee_scope_d1(
    client, db_session, seeded_admin, admin_token, seeded_contributor, contributor_token
):
    other, _ = await _mk_user(db_session)
    inactive, _ = await _mk_user(db_session, active=False)
    entry = await _mk_entry(db_session, seeded_contributor)
    eid = entry.site_log_event_id
    # A contributor may assign only themselves (or nobody).
    r = await _create(client, contributor_token, eid, assignee_user_id=str(other.user_id))
    assert r.status_code == 422
    r = await _create(client, contributor_token, eid,
                      assignee_user_id=str(seeded_contributor.user_id))
    assert r.status_code == 201
    assert r.json()["assignee"]["user_id"] == str(seeded_contributor.user_id)
    # An admin may pick any ACTIVE user.
    admin_entry = await _mk_entry(db_session, seeded_admin)
    aid = admin_entry.site_log_event_id
    r = await _create(client, admin_token, aid, assignee_user_id=str(inactive.user_id))
    assert r.status_code == 422
    r = await _create(client, admin_token, aid, assignee_user_id=str(uuid.uuid4()))
    assert r.status_code == 422
    r = await _create(client, admin_token, aid, assignee_user_id=str(other.user_id))
    assert r.status_code == 201 and r.json()["assignee"]["full_name"] == other.full_name

    # The same scope applies when changing the assignee later.
    task_id = (await _create(client, contributor_token, eid)).json()["task_id"]
    r = await client.patch(f"/tasks/{task_id}", json={"assignee_user_id": str(other.user_id)},
                           headers=_auth(contributor_token))
    assert r.status_code == 422
    r = await client.patch(f"/tasks/{task_id}",
                           json={"assignee_user_id": str(seeded_contributor.user_id)},
                           headers=_auth(contributor_token))
    assert r.status_code == 200
    r = await client.patch(f"/tasks/{task_id}", json={"assignee_user_id": None},
                           headers=_auth(contributor_token))
    assert r.status_code == 200 and r.json()["assignee"] is None


async def test_assignee_of_a_jobless_entry_task_sees_the_task_but_not_the_entry(
    client, db_session, seeded_admin, admin_token, seeded_contributor, contributor_token,
    site_log_session_factory,
):
    # Declared through the real API so the entry has its stored text evidence.
    declared = await client.post(
        "/site-log-events",
        json={"capture_client_id": str(uuid.uuid4()),
              "body_text": "Private note SECRETWORD about the neighbour"},
        headers=_auth(admin_token),
    )
    assert declared.status_code == 201, declared.text
    entry = declared.json()
    evidence_ids = [a["evidence_id"] for a in entry["attachments"] if a["evidence_id"]]
    assert evidence_ids, "the inline text evidence must exist"

    made = await _create(
        client, admin_token, entry["site_log_event_id"],
        title="Call the neighbour", assignee_user_id=str(seeded_contributor.user_id),
    )
    assert made.status_code == 201
    task_id = made.json()["task_id"]

    seen = await client.get(f"/tasks/{task_id}", headers=_auth(contributor_token))
    assert seen.status_code == 200
    assert seen.json()["source"]["readable"] is False
    assert seen.json()["source"]["job_id"] is None
    assert seen.json()["permissions"]["change_status"] is True
    assert seen.json()["permissions"]["edit"] is False
    # Assigning grants nothing on the entry or its files.
    assert (await client.get(f"/site-log-events/{entry['site_log_event_id']}",
                             headers=_auth(contributor_token))).status_code == 404
    assert (await client.get(f"/site-log-events/{entry['site_log_event_id']}/tasks",
                             headers=_auth(contributor_token))).status_code == 404
    for evidence_id in evidence_ids:
        assert (await client.get(f"/evidence/{evidence_id}",
                                 headers=_auth(contributor_token))).status_code == 404
        assert (await client.get(f"/evidence/{evidence_id}/download",
                                 headers=_auth(contributor_token))).status_code == 404
    # Nothing the assignee can fetch carries the entry's words.
    listed = await client.get("/tasks", headers=_auth(contributor_token))
    moved = await client.patch(f"/tasks/{task_id}", json={"status": "in_progress"},
                               headers=_auth(contributor_token))
    assert moved.status_code == 200
    for text in (seen.text, listed.text, moved.text):
        assert "SECRETWORD" not in text and "proposed_title" not in text


async def test_proposed_title_never_leaves_the_audit(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin)
    entry = await _mk_entry(db_session, seeded_admin, job=job,
                            body="SECRETWORD check the gutter flashing")
    made = await _create(client, admin_token, entry.site_log_event_id,
                         title="Check the gutter flashing")
    assert made.status_code == 201
    task_id = made.json()["task_id"]
    [created] = await _audit_rows(db_session, task_id)
    assert created.changed_fields["proposed_title"].startswith("SECRETWORD")
    responses = [
        made,
        await client.get(f"/tasks/{task_id}", headers=_auth(admin_token)),
        await client.get("/tasks", headers=_auth(admin_token)),
        await client.get(f"/site-log-events/{entry.site_log_event_id}/tasks",
                         headers=_auth(admin_token)),
        await client.patch(f"/tasks/{task_id}", json={"status": "done"},
                           headers=_auth(admin_token)),
    ]
    for r in responses:
        assert r.status_code in (200, 201)
        assert "SECRETWORD" not in r.text and "proposed_title" not in r.text


# ------------------------------------------------------------------- jobs


async def test_entry_relink_does_not_move_the_task(
    client, db_session, seeded_admin, admin_token
):
    job_a = await _mk_job(db_session, seeded_admin, name="A")
    job_b = await _mk_job(db_session, seeded_admin, name="B")
    entry = await _mk_entry(db_session, seeded_admin, job=job_a)
    task_id = (await _create(client, admin_token, entry.site_log_event_id)).json()["task_id"]
    relinked = await client.post(
        f"/site-log-events/{entry.site_log_event_id}/relink-job",
        json={"job_id": str(job_b.job_id), "reason": "wrong job"},
        headers=_auth(admin_token),
    )
    assert relinked.status_code == 200, relinked.text
    task = (await client.get(f"/tasks/{task_id}", headers=_auth(admin_token))).json()
    assert task["job"]["job_id"] == str(job_a.job_id)  # unchanged
    assert task["source"]["job_id"] == str(job_b.job_id)  # the entry's current job
    rows = await _audit_rows(db_session, task_id)
    assert [r.action for r in rows] == ["created"]


async def test_job_set_and_change_rules(
    client, db_session, seeded_admin, admin_token, seeded_contributor, contributor_token
):
    job_a = await _mk_job(db_session, seeded_admin, name="A")
    job_b = await _mk_job(db_session, seeded_admin, name="B")
    done_job = await _mk_job(db_session, seeded_admin, name="Done", status=JobStatus.completed)
    entry = await _mk_entry(db_session, seeded_contributor)  # no job
    made = await _create(client, contributor_token, entry.site_log_event_id)
    task_id = made.json()["task_id"]
    assert made.json()["job"] is None and made.json()["permissions"]["set_job"] is True

    url = f"/tasks/{task_id}"
    assert (await client.patch(url, json={"job_id": str(uuid.uuid4())},
                               headers=_auth(contributor_token))).status_code == 404
    assert (await client.patch(url, json={"job_id": str(done_job.job_id)},
                               headers=_auth(contributor_token))).status_code == 422
    # First set: the creator may do it, no reason needed.
    as_creator = _auth(contributor_token)
    r = await client.patch(url, json={"job_id": str(job_a.job_id)}, headers=as_creator)
    assert r.status_code == 200 and r.json()["job"]["job_id"] == str(job_a.job_id)
    assert r.json()["permissions"]["set_job"] is False
    # Changing it: admin only, with a reason; never cleared.
    r = await client.patch(url, json={"job_id": str(job_b.job_id)}, headers=as_creator)
    assert r.status_code == 403
    r = await client.patch(url, json={"job_id": str(job_b.job_id)}, headers=_auth(admin_token))
    assert r.status_code == 422
    r = await client.patch(url, json={"job_id": None, "reason": "x"}, headers=_auth(admin_token))
    assert r.status_code == 422
    r = await client.patch(url, json={"job_id": str(job_b.job_id), "reason": " moved site "},
                           headers=_auth(admin_token))
    assert r.status_code == 200 and r.json()["job"]["job_id"] == str(job_b.job_id)
    sets = [row for row in await _audit_rows(db_session, task_id) if row.action == "job_set"]
    assert [s.changed_fields for s in sets] == [
        {"from": None, "to": str(job_a.job_id), "reason": None},
        {"from": str(job_a.job_id), "to": str(job_b.job_id), "reason": "moved site"},
    ]


# ------------------------------------------------------- status and edits


async def test_status_and_edit_permissions(
    client, db_session, seeded_admin, admin_token, seeded_contributor, contributor_token
):
    outsider, outsider_token = await _mk_user(db_session)
    viewer, viewer_token = await _mk_user(db_session)
    jobless = await _mk_entry(db_session, seeded_admin)
    task_id = (await _create(client, admin_token, jobless.site_log_event_id,
                             assignee_user_id=str(seeded_contributor.user_id))).json()["task_id"]
    url = f"/tasks/{task_id}"
    # The assignee moves the status but cannot edit the task.
    r = await client.patch(url, json={"status": "blocked"}, headers=_auth(contributor_token))
    assert r.status_code == 200 and r.json()["status"] == "blocked"
    assert (await client.patch(url, json={"title": "New"},
                               headers=_auth(contributor_token))).status_code == 403
    assert (await client.patch(url, json={"due_date": "2026-11-01"},
                               headers=_auth(contributor_token))).status_code == 403
    # Nor give it a job: setting an empty job is for the creator or an admin.
    assignee_view = (await client.get(url, headers=_auth(contributor_token))).json()
    assert assignee_view["permissions"]["set_job"] is False
    some_job = await _mk_job(db_session, seeded_admin, name="Some job")
    assert (await client.patch(url, json={"job_id": str(some_job.job_id)},
                               headers=_auth(contributor_token))).status_code == 403
    # Someone who cannot see a job-less task gets 404, whatever they send.
    assert (await client.get(url, headers=_auth(outsider_token))).status_code == 404
    assert (await client.patch(url, json={"status": "done"},
                               headers=_auth(outsider_token))).status_code == 404

    # A task with a job is visible to any active user, who still cannot change it.
    job = await _mk_job(db_session, seeded_admin)
    on_job = await _mk_entry(db_session, seeded_admin, job=job)
    job_task = (await _create(client, admin_token, on_job.site_log_event_id)).json()["task_id"]
    seen = await client.get(f"/tasks/{job_task}", headers=_auth(viewer_token))
    assert seen.status_code == 200
    assert seen.json()["permissions"] == {
        "change_status": False, "edit": False, "assign_others": False,
        "set_job": False, "change_job": False,
    }
    assert (await client.patch(f"/tasks/{job_task}", json={"status": "done"},
                               headers=_auth(viewer_token))).status_code == 403
    # Sending a field unchanged is not a change and needs no permission.
    same = await client.patch(f"/tasks/{job_task}", json={"status": "to_do"},
                              headers=_auth(viewer_token))
    assert same.status_code == 200
    # Any status may move to any status.
    for target in ("done", "to_do", "in_progress", "done", "blocked"):
        r = await client.patch(f"/tasks/{job_task}", json={"status": target},
                               headers=_auth(admin_token))
        assert r.status_code == 200 and r.json()["status"] == target


async def test_one_audit_row_per_changed_field_and_no_op_writes_nothing(
    client, db_session, seeded_contributor, contributor_token
):
    entry = await _mk_entry(db_session, seeded_contributor)
    task_id = (await _create(client, contributor_token, entry.site_log_event_id)).json()["task_id"]
    url = f"/tasks/{task_id}"
    r = await client.patch(
        url,
        json={"title": "Brace the north roof", "status": "in_progress", "due_date": "2026-10-20"},
        headers=_auth(contributor_token),
    )
    assert r.status_code == 200, r.text
    rows = await _audit_rows(db_session, task_id)
    by_action = {row.action: row.changed_fields for row in rows}
    assert sorted(by_action) == ["created", "due_date_changed", "status_changed", "title_changed"]
    assert by_action["title_changed"] == {"from": ROOF, "to": "Brace the north roof"}
    assert by_action["status_changed"] == {"from": "to_do", "to": "in_progress"}
    assert by_action["due_date_changed"] == {"from": None, "to": "2026-10-20"}
    assert all(row.actor_user_id == seeded_contributor.user_id for row in rows)

    # The same values again: 200, nothing written.
    again = await client.patch(
        url,
        json={"title": "  Brace the north roof ", "status": "in_progress",
              "due_date": "2026-10-20"},
        headers=_auth(contributor_token),
    )
    assert again.status_code == 200
    assert len(await _audit_rows(db_session, task_id)) == 4
    # Clearing the due date is a change.
    cleared = await client.patch(url, json={"due_date": None}, headers=_auth(contributor_token))
    assert cleared.status_code == 200 and cleared.json()["due_date"] is None
    assert len(await _audit_rows(db_session, task_id)) == 5
    # Malformed requests.
    assert (await client.patch(url, json={}, headers=_auth(contributor_token))).status_code == 422
    assert (await client.patch(url, json={"title": " "},
                               headers=_auth(contributor_token))).status_code == 422
    assert (await client.patch(url, json={"status": None},
                               headers=_auth(contributor_token))).status_code == 422
    assert (await client.patch(url, json={"status": "cancelled"},
                               headers=_auth(contributor_token))).status_code == 422
    assert (await client.patch(url, json={"source_site_log_event_id": str(uuid.uuid4())},
                               headers=_auth(contributor_token))).status_code == 422
    assert (await client.patch(f"/tasks/{uuid.uuid4()}", json={"status": "done"},
                               headers=_auth(contributor_token))).status_code == 404
    assert len(await _audit_rows(db_session, task_id)) == 5


async def test_the_source_entry_is_never_changed(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin)
    entry = await _mk_entry(db_session, seeded_admin, job=job)
    before = [
        (r.revision_no, r.body_text, r.withdrawn)
        for r in (await db_session.execute(
            select(SiteLogEventRevision).where(
                SiteLogEventRevision.site_log_event_id == entry.site_log_event_id))).scalars()
    ]
    task_id = (await _create(client, admin_token, entry.site_log_event_id,
                             title="Edited title")).json()["task_id"]
    await client.patch(f"/tasks/{task_id}", json={"title": "Edited again", "status": "done"},
                       headers=_auth(admin_token))
    after = [
        (r.revision_no, r.body_text, r.withdrawn)
        for r in (await db_session.execute(
            select(SiteLogEventRevision)
            .where(SiteLogEventRevision.site_log_event_id == entry.site_log_event_id)
            .execution_options(populate_existing=True))).scalars()
    ]
    assert after == before
    event = (await db_session.execute(
        select(SiteLogEvent)
        .where(SiteLogEvent.site_log_event_id == entry.site_log_event_id)
        .execution_options(populate_existing=True))).scalar_one()
    assert event.job_id == job.job_id


# ------------------------------------------------------------------ lists


async def test_list_visibility_and_filters(
    client, db_session, seeded_admin, admin_token, seeded_contributor, contributor_token
):
    job = await _mk_job(db_session, seeded_admin)
    on_job = await _mk_entry(db_session, seeded_admin, job=job)
    admin_jobless = await _mk_entry(db_session, seeded_admin)
    own = await _mk_entry(db_session, seeded_contributor)
    t_job = (await _create(client, admin_token, on_job.site_log_event_id)).json()["task_id"]
    t_assigned = (await _create(client, admin_token, admin_jobless.site_log_event_id,
                                assignee_user_id=str(seeded_contributor.user_id))).json()["task_id"]
    hidden = await _create(client, admin_token, admin_jobless.site_log_event_id)
    t_hidden = hidden.json()["task_id"]
    t_own = (await _create(client, contributor_token, own.site_log_event_id)).json()["task_id"]

    async def ids(token, query=""):
        r = await client.get(f"/tasks{query}", headers=_auth(token))
        assert r.status_code == 200, r.text
        return {t["task_id"] for t in r.json()}

    assert await ids(contributor_token) == {t_job, t_assigned, t_own}
    assert await ids(admin_token) == {t_job, t_assigned, t_hidden, t_own}
    assert await ids(contributor_token, "?mine=assigned") == {t_assigned}
    assert await ids(contributor_token, "?mine=created") == {t_own}
    assert await ids(contributor_token, f"?job_id={job.job_id}") == {t_job}
    await client.patch(f"/tasks/{t_assigned}", json={"status": "done"},
                       headers=_auth(contributor_token))
    assert await ids(contributor_token, "?status=done") == {t_assigned}
    assert await ids(contributor_token, "?status=to_do") == {t_job, t_own}
    assert (await client.get("/tasks?status=cancelled",
                             headers=_auth(contributor_token))).status_code == 422
    paged = await client.get("/tasks?limit=1&offset=1", headers=_auth(admin_token))
    assert paged.status_code == 200 and len(paged.json()) == 1

    # Entry task lists: readable entries only; visible tasks only.
    r = await client.get(f"/site-log-events/{admin_jobless.site_log_event_id}/tasks",
                         headers=_auth(contributor_token))
    assert r.status_code == 404
    r = await client.get(f"/site-log-events/{admin_jobless.site_log_event_id}/tasks",
                         headers=_auth(admin_token))
    assert {t["task_id"] for t in r.json()} == {t_assigned, t_hidden}
    r = await client.get(f"/site-log-events/{on_job.site_log_event_id}/tasks",
                         headers=_auth(contributor_token))
    assert {t["task_id"] for t in r.json()} == {t_job}


async def test_list_clause_agrees_with_the_view_rule(
    db_session, seeded_admin, seeded_contributor
):
    """The SQL list filter and can_view_task must select the same tasks."""
    other, _ = await _mk_user(db_session)
    job = await _mk_job(db_session, seeded_admin)
    entry = await _mk_entry(db_session, seeded_admin)
    shapes = [
        dict(created_by=seeded_admin, assignee=None, job=None),
        dict(created_by=seeded_admin, assignee=seeded_contributor, job=None),
        dict(created_by=seeded_admin, assignee=None, job=job),
        dict(created_by=seeded_contributor, assignee=None, job=None),
        dict(created_by=other, assignee=None, job=None),
        dict(created_by=other, assignee=seeded_contributor, job=job),
    ]
    tasks = []
    for s in shapes:
        t = Task(
            task_id=uuid.uuid4(), job_id=s["job"].job_id if s["job"] else None,
            source_site_log_event_id=entry.site_log_event_id, source_revision_no=1,
            title="t", assignee_user_id=s["assignee"].user_id if s["assignee"] else None,
            created_by_user_id=s["created_by"].user_id, create_client_id=uuid.uuid4(),
        )
        db_session.add(t)
        tasks.append(t)
    await db_session.flush()
    for viewer in (seeded_admin, seeded_contributor, other):
        clause = access.visible_tasks_clause(viewer)
        q = select(Task.task_id)
        if clause is not None:
            q = q.where(clause)
        listed = set((await db_session.execute(q)).scalars())
        expected = {
            t.task_id
            for t in tasks
            if access.can_view_task(viewer, t, job if t.job_id else None)
        }
        assert listed == expected, viewer.full_name


# ------------------------------------------------------------------ rules


@pytest.mark.parametrize(
    "body, expected",
    [
        (None, None),
        ("", None),
        ("   \n\t\n", None),
        (ROOF, ROOF),
        ("  first line  \nsecond", "first line"),
        ("\r\n\r\n  after blanks \r\nmore", "after blanks"),
        ("old mac\rbreak", "old mac"),
        ("x" * 250, "x" * 200),
        ("y" * 199 + " z", "y" * 199),  # the cut never leaves trailing space
        ("🏗️" * 150, ("🏗️" * 150)[:200]),  # code points, not UTF-16 units
    ],
)
async def test_proposed_title_rule(body, expected):
    assert task_svc.proposed_title_from(body) == expected


async def test_task_audit_never_takes_the_entry_body():
    with pytest.raises(ValueError):
        validate_task_audit_detail({"body_text": "anything"})
    with pytest.raises(ValueError):
        validate_task_audit_detail({"nested": {"transcript": "x"}})
    assert validate_task_audit_detail({"from": "a", "to": "b"}) == {"from": "a", "to": "b"}
