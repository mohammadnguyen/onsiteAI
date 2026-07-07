"""PR 5 — Timeline HTTP API tests (authz matrix per route).

Reviewer-mandated cells, both enforced here:

* **404-before-403.** For every item verb (GET / PATCH / DELETE /
  PATCH-status), a contributor who is NOT the creator probing an id
  they cannot see — nonexistent, or soft-deleted (someone else's) —
  gets **404**, never a 403 that would confirm the id exists. A
  permissions-first router would leak a 403 on the soft-deleted case;
  the service's existence-check-first ordering must win. The checklist
  toggle's cross-job cell gets the same no-leak 404.
* **Thin router.** ``app/api/timeline.py`` contains zero authorization
  or state-machine logic: no ``require_admin``, no ``UserRole``, no
  ``.role`` access — and structurally not a single ``if`` statement
  (a pure forward-and-map layer needs none). AST-enforced.

Everything else: per-route 401s, role matrix on writes, the status
machine over HTTP, filters/pagination/cursor errors, checklist flows.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.core.security import create_access_token, hash_password
from app.models import Job, JobChecklistItem, JobStatus, User, UserRole
from app.models.user import LanguageCode

_OCCURRED = "2026-07-06T09:00:00+00:00"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _mk_job(db_session, admin, *, name: str = "Kelly House") -> Job:
    job = Job(
        job_id=uuid.uuid4(),
        job_name=name,
        status=JobStatus.active,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


async def _mk_checklist(db_session, job, *, label="flood test", sort_order=0):
    row = JobChecklistItem(
        checklist_item_id=uuid.uuid4(),
        job_id=job.job_id,
        label=label,
        sort_order=sort_order,
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _note_body(**overrides) -> dict:
    body = {"item_type": "daily_note", "body": "Slab poured.", "occurred_at": _OCCURRED}
    body.update(overrides)
    return body


def _issue_body(**overrides) -> dict:
    body = {"item_type": "issue", "title": "Leaking pipe", "occurred_at": _OCCURRED}
    body.update(overrides)
    return body


async def _post_item(client, token, job, body) -> dict:
    r = await client.post(
        f"/jobs/{job.job_id}/timeline", headers=_auth(token), json=body
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
# Thin-router guarantee (reviewer criterion 2)                                 #
# --------------------------------------------------------------------------- #
def test_router_is_thin_no_role_or_state_logic():
    """AST guarantee: the router imports no admin/role machinery, never
    reads ``.role``, and contains not a single ``if`` statement — every
    decision belongs to the service."""
    import ast
    from pathlib import Path

    import app.api.timeline as router_mod

    source = Path(router_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in ("require_admin", "UserRole"):
                    offenders.append(f"import {alias.name} @L{node.lineno}")
        if isinstance(node, ast.Attribute) and node.attr == "role":
            offenders.append(f".role access @L{node.lineno}")
        if isinstance(node, (ast.If, ast.IfExp)):
            offenders.append(f"if-statement @L{node.lineno}")
    assert offenders == [], f"router is not thin: {offenders}"


# --------------------------------------------------------------------------- #
# 401 — every route requires auth                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,url,body",
    [
        ("get", f"/jobs/{uuid.uuid4()}/timeline", None),
        ("post", f"/jobs/{uuid.uuid4()}/timeline", {"item_type": "daily_note"}),
        ("get", f"/timeline/{uuid.uuid4()}", None),
        ("patch", f"/timeline/{uuid.uuid4()}", {"body": "x"}),
        ("delete", f"/timeline/{uuid.uuid4()}", None),
        ("patch", f"/timeline/{uuid.uuid4()}/status", {"status": "resolved"}),
        ("get", f"/jobs/{uuid.uuid4()}/checklist", None),
        (
            "patch",
            f"/jobs/{uuid.uuid4()}/checklist/{uuid.uuid4()}/toggle",
            {"is_done": True},
        ),
    ],
)
async def test_unauthenticated_gets_401(client, method, url, body):
    kwargs = {"json": body} if body is not None else {}
    r = await getattr(client, method)(url, **kwargs)
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# 404-before-403 matrix (reviewer criterion 1) — all four item verbs          #
# --------------------------------------------------------------------------- #
def _item_verb_requests(item_id: str):
    return [
        ("get", f"/timeline/{item_id}", None),
        ("patch", f"/timeline/{item_id}", {"body": "probe"}),
        ("delete", f"/timeline/{item_id}", None),
        ("patch", f"/timeline/{item_id}/status", {"status": "resolved"}),
    ]


@pytest.mark.asyncio
async def test_nonexistent_item_404_for_all_verbs_as_non_creator(
    client, contributor_token, seeded_contributor
):
    ghost = uuid.uuid4()
    for method, url, body in _item_verb_requests(str(ghost)):
        kwargs = {"json": body} if body is not None else {}
        r = await getattr(client, method)(
            url, headers=_auth(contributor_token), **kwargs
        )
        assert r.status_code == 404, f"{method} {url}: {r.status_code} {r.text}"


@pytest.mark.asyncio
async def test_soft_deleted_foreign_item_404_not_403_for_all_verbs(
    client,
    db_session,
    seeded_admin,
    admin_token,
    second_contributor_token,
):
    """The decisive no-leak cells: the item EXISTED, belongs to someone
    else, and is soft-deleted. A permissions-first router would answer
    403 ('not your item') and confirm the id — the contract demands 404
    on every verb."""
    job = await _mk_job(db_session, seeded_admin)
    created = await _post_item(client, admin_token, job, _issue_body())
    item_id = created["timeline_item_id"]
    r = await client.delete(f"/timeline/{item_id}", headers=_auth(admin_token))
    assert r.status_code == 204

    for method, url, body in _item_verb_requests(item_id):
        kwargs = {"json": body} if body is not None else {}
        r = await getattr(client, method)(
            url, headers=_auth(second_contributor_token), **kwargs
        )
        assert r.status_code == 404, (
            f"{method} {url} leaked existence: {r.status_code} {r.text}"
        )


@pytest.mark.asyncio
async def test_checklist_toggle_cross_job_404_no_leak(
    client, db_session, seeded_admin, contributor_token, seeded_contributor
):
    job_a = await _mk_job(db_session, seeded_admin, name="Job A")
    job_b = await _mk_job(db_session, seeded_admin, name="Job B")
    row_b = await _mk_checklist(db_session, job_b)

    # Real checklist id under the wrong job: identical 404 to a ghost id.
    r = await client.patch(
        f"/jobs/{job_a.job_id}/checklist/{row_b.checklist_item_id}/toggle",
        headers=_auth(contributor_token),
        json={"is_done": True},
    )
    assert r.status_code == 404
    r = await client.patch(
        f"/jobs/{job_a.job_id}/checklist/{uuid.uuid4()}/toggle",
        headers=_auth(contributor_token),
        json={"is_done": True},
    )
    assert r.status_code == 404
    # Ghost job with a real checklist id: job 404s first.
    r = await client.patch(
        f"/jobs/{uuid.uuid4()}/checklist/{row_b.checklist_item_id}/toggle",
        headers=_auth(contributor_token),
        json={"is_done": True},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unknown_job_404_on_job_scoped_routes(client, contributor_token):
    ghost = uuid.uuid4()
    r = await client.get(f"/jobs/{ghost}/timeline", headers=_auth(contributor_token))
    assert r.status_code == 404
    r = await client.post(
        f"/jobs/{ghost}/timeline", headers=_auth(contributor_token),
        json=_note_body(),
    )
    assert r.status_code == 404
    r = await client.get(f"/jobs/{ghost}/checklist", headers=_auth(contributor_token))
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# 403 cells — item exists and is visible, caller lacks write rights            #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_non_creator_write_403_on_live_item(
    client, db_session, seeded_admin, contributor_token,
    second_contributor_token,
):
    job = await _mk_job(db_session, seeded_admin)
    created = await _post_item(client, contributor_token, job, _note_body())
    item_id = created["timeline_item_id"]

    r = await client.patch(
        f"/timeline/{item_id}", headers=_auth(second_contributor_token),
        json={"body": "hijack"},
    )
    assert r.status_code == 403
    r = await client.delete(
        f"/timeline/{item_id}", headers=_auth(second_contributor_token)
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_closed_gate_403_for_contributor_200_for_admin(
    client, db_session, seeded_admin, admin_token, contributor_token
):
    job = await _mk_job(db_session, seeded_admin)
    created = await _post_item(client, contributor_token, job, _issue_body())
    item_id = created["timeline_item_id"]

    r = await client.patch(
        f"/timeline/{item_id}/status", headers=_auth(contributor_token),
        json={"status": "resolved"},
    )
    assert r.status_code == 200

    r = await client.patch(
        f"/timeline/{item_id}/status", headers=_auth(contributor_token),
        json={"status": "closed"},
    )
    assert r.status_code == 403

    r = await client.patch(
        f"/timeline/{item_id}/status", headers=_auth(admin_token),
        json={"status": "closed"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "closed"

    # Leaving closed is admin-only too.
    r = await client.patch(
        f"/timeline/{item_id}/status", headers=_auth(contributor_token),
        json={"status": "resolved"},
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# 422 / 400 contract cells                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_422_cells(client, db_session, seeded_admin, admin_token):
    job = await _mk_job(db_session, seeded_admin)

    # Born-closed issue (schema validator).
    r = await client.post(
        f"/jobs/{job.job_id}/timeline", headers=_auth(admin_token),
        json=_issue_body(status="closed"),
    )
    assert r.status_code == 422

    # Issue without title (schema validator).
    r = await client.post(
        f"/jobs/{job.job_id}/timeline", headers=_auth(admin_token),
        json={"item_type": "issue", "occurred_at": _OCCURRED},
    )
    assert r.status_code == 422

    # Naive occurred_at (AwareDatetime contract).
    r = await client.post(
        f"/jobs/{job.job_id}/timeline", headers=_auth(admin_token),
        json=_note_body(occurred_at="2026-07-06T09:00:00"),
    )
    assert r.status_code == 422

    # title:null on an issue (service obligation).
    created = await _post_item(client, admin_token, job, _issue_body())
    r = await client.patch(
        f"/timeline/{created['timeline_item_id']}", headers=_auth(admin_token),
        json={"title": None},
    )
    assert r.status_code == 422

    # Illegal direct transition open->closed, even for admin.
    r = await client.patch(
        f"/timeline/{created['timeline_item_id']}/status",
        headers=_auth(admin_token), json={"status": "closed"},
    )
    assert r.status_code == 422

    # Status transition on a non-issue.
    note = await _post_item(client, admin_token, job, _note_body())
    r = await client.patch(
        f"/timeline/{note['timeline_item_id']}/status",
        headers=_auth(admin_token), json={"status": "resolved"},
    )
    assert r.status_code == 422

    # Naive datetime filter on the list route.
    r = await client.get(
        f"/jobs/{job.job_id}/timeline?date_from=2026-07-06T09:00:00",
        headers=_auth(admin_token),
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_invalid_cursor_400(client, db_session, seeded_admin, admin_token):
    job = await _mk_job(db_session, seeded_admin)
    r = await client.get(
        f"/jobs/{job.job_id}/timeline?cursor=not-a-cursor",
        headers=_auth(admin_token),
    )
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# Happy paths + wire shapes                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_create_and_detail_shapes(
    client, db_session, seeded_admin, contributor_token, seeded_contributor
):
    job = await _mk_job(db_session, seeded_admin)
    created = await _post_item(client, contributor_token, job, _issue_body())

    assert created["item_type"] == "issue"
    assert created["status"] == "open"  # schema default applied
    assert created["job_id"] == str(job.job_id)
    assert created["created_by"] == str(seeded_contributor.user_id)
    assert created["attachment_count"] == 0
    assert "deleted_at" not in created  # never on the wire

    r = await client.get(
        f"/timeline/{created['timeline_item_id']}",
        headers=_auth(contributor_token),
    )
    assert r.status_code == 200
    detail = r.json()
    assert detail["attachments"] == []
    assert detail["attachment_count"] == 0


@pytest.mark.asyncio
async def test_list_team_visibility_filters_and_pagination(
    client, db_session, seeded_admin, admin_token, contributor_token
):
    job = await _mk_job(db_session, seeded_admin)
    n1 = await _post_item(
        client, admin_token, job,
        _note_body(occurred_at="2026-07-06T09:00:00+00:00"),
    )
    iss = await _post_item(
        client, contributor_token, job,
        _issue_body(occurred_at="2026-07-06T10:00:00+00:00"),
    )
    n2 = await _post_item(
        client, admin_token, job,
        _note_body(occurred_at="2026-07-06T11:00:00+00:00"),
    )

    # Contributor sees everyone's items, newest occurred_at first.
    r = await client.get(
        f"/jobs/{job.job_id}/timeline", headers=_auth(contributor_token)
    )
    assert r.status_code == 200
    page = r.json()
    assert [i["timeline_item_id"] for i in page["items"]] == [
        n2["timeline_item_id"],
        iss["timeline_item_id"],
        n1["timeline_item_id"],
    ]
    assert page["next_cursor"] is None

    # Type + status filters (status uses the ?status= alias).
    r = await client.get(
        f"/jobs/{job.job_id}/timeline?item_type=issue&status=open",
        headers=_auth(contributor_token),
    )
    assert [i["timeline_item_id"] for i in r.json()["items"]] == [
        iss["timeline_item_id"]
    ]

    # Keyset pagination walk over HTTP.
    r = await client.get(
        f"/jobs/{job.job_id}/timeline?limit=2", headers=_auth(admin_token)
    )
    first = r.json()
    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None
    r = await client.get(
        f"/jobs/{job.job_id}/timeline?limit=2&cursor={first['next_cursor']}",
        headers=_auth(admin_token),
    )
    second = r.json()
    assert [i["timeline_item_id"] for i in second["items"]] == [
        n1["timeline_item_id"]
    ]
    assert second["next_cursor"] is None


@pytest.mark.asyncio
async def test_update_and_delete_happy_paths(
    client, db_session, seeded_admin, admin_token, contributor_token
):
    job = await _mk_job(db_session, seeded_admin)
    created = await _post_item(client, contributor_token, job, _note_body())
    item_id = created["timeline_item_id"]

    r = await client.patch(
        f"/timeline/{item_id}", headers=_auth(contributor_token),
        json={"body": "amended"},
    )
    assert r.status_code == 200
    assert r.json()["body"] == "amended"

    # Admin can edit anyone's item.
    r = await client.patch(
        f"/timeline/{item_id}", headers=_auth(admin_token),
        json={"title": "Admin note"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Admin note"

    r = await client.delete(f"/timeline/{item_id}", headers=_auth(admin_token))
    assert r.status_code == 204
    # Idempotency is non-silent: the row is gone.
    r = await client.delete(f"/timeline/{item_id}", headers=_auth(admin_token))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_checklist_endpoints(
    client, db_session, seeded_admin, contributor_token, seeded_contributor
):
    job = await _mk_job(db_session, seeded_admin)
    second = await _mk_checklist(db_session, job, label="second", sort_order=2)
    first = await _mk_checklist(db_session, job, label="first", sort_order=1)

    r = await client.get(
        f"/jobs/{job.job_id}/checklist", headers=_auth(contributor_token)
    )
    assert r.status_code == 200
    assert [row["checklist_item_id"] for row in r.json()] == [
        str(first.checklist_item_id),
        str(second.checklist_item_id),
    ]

    r = await client.patch(
        f"/jobs/{job.job_id}/checklist/{first.checklist_item_id}/toggle",
        headers=_auth(contributor_token),
        json={"is_done": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["is_done"] is True
    assert body["done_by"] == str(seeded_contributor.user_id)
    assert body["done_at"] is not None
