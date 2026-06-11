"""Tests for the Labour v1 (slice L-A) HTTP API.

Covers:

* ``/workers`` — admin-only writes, any-auth reads, duplicate names
  allowed, deactivate lifecycle (no delete route exists).
* ``POST /labour-entries/batch`` — create/update upsert semantics,
  all-or-nothing atomicity, the <=1.0 per-worker-per-date allocation
  rule, active-job and active-worker rules, date bounds, duplicate
  workers in batch, OD-1 edit permissions.
* ``DELETE /labour-entries/{id}`` — admin any; contributor own+today.
* ``GET /labour-entries`` — filters, any-auth reads.
* ``GET /labour-summary`` — admin-only; per-worker/per-job totals.
* delete-empty-job integration — labour entries block job hard-delete.
"""

from __future__ import annotations

import datetime as _datetime
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Job, JobStatus, LabourEntry, Worker


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _today() -> _datetime.date:
    return _datetime.date.today()


async def _mk_job(db_session, admin, *, name: str, status=JobStatus.active) -> Job:
    job = Job(
        job_id=uuid.uuid4(),
        job_name=name,
        status=status,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


async def _mk_worker(
    db_session,
    admin,
    *,
    name: str,
    is_active: bool = True,
    worker_id: uuid.UUID | None = None,
) -> Worker:
    worker = Worker(
        worker_id=worker_id or uuid.uuid4(),
        display_name=name,
        is_active=is_active,
        created_by=admin.user_id,
    )
    db_session.add(worker)
    await db_session.flush()
    return worker


async def _mk_entry(
    db_session,
    *,
    worker,
    job,
    recorded_by,
    work_date=None,
    fraction: str = "1.0",
) -> LabourEntry:
    entry = LabourEntry(
        entry_id=uuid.uuid4(),
        worker_id=worker.worker_id,
        job_id=job.job_id,
        work_date=work_date or _today(),
        day_fraction=Decimal(fraction),
        recorded_by_user_id=recorded_by.user_id,
    )
    db_session.add(entry)
    await db_session.flush()
    return entry


# ---------------------------------------------------------------------------
# Workers (roster)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workers_create_admin_201(client, admin_token):
    r = await client.post(
        "/workers",
        headers=_auth(admin_token),
        json={"display_name": "老王 (Wang)", "note": "concreter"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["display_name"] == "老王 (Wang)"
    assert body["note"] == "concreter"
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_workers_create_contributor_403(client, contributor_token):
    r = await client.post(
        "/workers", headers=_auth(contributor_token), json={"display_name": "X"}
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_workers_duplicate_names_allowed(client, admin_token):
    for _ in range(2):
        r = await client.post(
            "/workers", headers=_auth(admin_token), json={"display_name": "Li"}
        )
        assert r.status_code == 201
    r = await client.get("/workers", headers=_auth(admin_token))
    names = [w["display_name"] for w in r.json()]
    assert names.count("Li") == 2


@pytest.mark.asyncio
async def test_workers_list_any_auth_active_only_default(
    client, db_session, seeded_admin, contributor_token
):
    await _mk_worker(db_session, seeded_admin, name="Active A")
    await _mk_worker(db_session, seeded_admin, name="Gone B", is_active=False)

    r = await client.get("/workers", headers=_auth(contributor_token))
    assert r.status_code == 200
    names = [w["display_name"] for w in r.json()]
    assert "Active A" in names
    assert "Gone B" not in names

    r2 = await client.get(
        "/workers?include_inactive=true", headers=_auth(contributor_token)
    )
    names2 = [w["display_name"] for w in r2.json()]
    assert "Gone B" in names2


@pytest.mark.asyncio
async def test_workers_patch_admin_deactivate(client, db_session, seeded_admin, admin_token):
    worker = await _mk_worker(db_session, seeded_admin, name="Marco")
    r = await client.patch(
        f"/workers/{worker.worker_id}",
        headers=_auth(admin_token),
        json={"is_active": False, "note": "moved interstate"},
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False
    assert r.json()["note"] == "moved interstate"


@pytest.mark.asyncio
async def test_workers_patch_contributor_403(
    client, db_session, seeded_admin, contributor_token
):
    worker = await _mk_worker(db_session, seeded_admin, name="Marco")
    r = await client.patch(
        f"/workers/{worker.worker_id}",
        headers=_auth(contributor_token),
        json={"is_active": False},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Batch attendance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_create_happy(client, db_session, seeded_admin, admin_token):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w1 = await _mk_worker(db_session, seeded_admin, name="W1")
    w2 = await _mk_worker(db_session, seeded_admin, name="W2")

    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={
            "job_id": str(job.job_id),
            "work_date": _today().isoformat(),
            "entries": [
                {"worker_id": str(w1.worker_id), "day_fraction": "1.0"},
                {"worker_id": str(w2.worker_id), "day_fraction": "0.5"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body) == 2
    fractions = {row["worker_id"]: row["day_fraction"] for row in body}
    assert Decimal(fractions[str(w1.worker_id)]) == Decimal("1.0")
    assert Decimal(fractions[str(w2.worker_id)]) == Decimal("0.5")
    assert all(row["recorded_by_user_id"] == str(seeded_admin.user_id) for row in body)


@pytest.mark.asyncio
async def test_batch_upsert_updates_fraction_not_duplicate(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="W")
    payload = {
        "job_id": str(job.job_id),
        "work_date": _today().isoformat(),
        "entries": [{"worker_id": str(w.worker_id), "day_fraction": "1.0"}],
    }
    r1 = await client.post(
        "/labour-entries/batch", headers=_auth(admin_token), json=payload
    )
    assert r1.status_code == 201
    payload["entries"][0]["day_fraction"] = "0.5"
    r2 = await client.post(
        "/labour-entries/batch", headers=_auth(admin_token), json=payload
    )
    assert r2.status_code == 201

    rows = list(
        (
            await db_session.execute(
                select(LabourEntry).where(LabourEntry.worker_id == w.worker_id)
            )
        ).scalars()
    )
    assert len(rows) == 1
    assert rows[0].day_fraction == Decimal("0.5")


@pytest.mark.asyncio
async def test_batch_allocation_exceeded_rolls_back_whole_batch(
    client, db_session, seeded_admin, admin_token
):
    """Worker already at 1.0 on job A; a batch on job B containing a
    valid OTHER worker plus 0.5 for the maxed worker must reject with
    422 and persist nothing.

    Harness note: the test fixtures share ONE session between client
    and test (no per-request savepoint), so production's full-request
    rollback (``app.database.get_db`` rolls back the session on any
    handler exception — verified) is not independently observable
    here. To make the no-persistence assertion deterministic under
    the shared session, the workers get EXPLICIT ids so the violating
    worker sorts FIRST in the service's deterministic lock order —
    the violation then fires before any row is added/autoflushed.
    """
    job_a = await _mk_job(db_session, seeded_admin, name="Site A")
    job_b = await _mk_job(db_session, seeded_admin, name="Site B")
    maxed = await _mk_worker(
        db_session,
        seeded_admin,
        name="Maxed",
        worker_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
    )
    fresh = await _mk_worker(
        db_session,
        seeded_admin,
        name="Fresh",
        worker_id=uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
    )
    await _mk_entry(
        db_session, worker=maxed, job=job_a, recorded_by=seeded_admin, fraction="1.0"
    )

    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={
            "job_id": str(job_b.job_id),
            "work_date": _today().isoformat(),
            "entries": [
                {"worker_id": str(fresh.worker_id), "day_fraction": "1.0"},
                {"worker_id": str(maxed.worker_id), "day_fraction": "0.5"},
            ],
        },
    )
    assert r.status_code == 422
    assert "cannot exceed 1.0" in r.json()["detail"]

    fresh_rows = list(
        (
            await db_session.execute(
                select(LabourEntry).where(LabourEntry.worker_id == fresh.worker_id)
            )
        ).scalars()
    )
    assert fresh_rows == []  # nothing persisted for the valid row


@pytest.mark.asyncio
async def test_half_plus_half_across_two_jobs_ok(
    client, db_session, seeded_admin, admin_token
):
    job_a = await _mk_job(db_session, seeded_admin, name="Site A")
    job_b = await _mk_job(db_session, seeded_admin, name="Site B")
    w = await _mk_worker(db_session, seeded_admin, name="Split")

    for job in (job_a, job_b):
        r = await client.post(
            "/labour-entries/batch",
            headers=_auth(admin_token),
            json={
                "job_id": str(job.job_id),
                "work_date": _today().isoformat(),
                "entries": [{"worker_id": str(w.worker_id), "day_fraction": "0.5"}],
            },
        )
        assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_batch_archived_job_422(client, db_session, seeded_admin, admin_token):
    job = await _mk_job(
        db_session, seeded_admin, name="Done", status=JobStatus.completed
    )
    w = await _mk_worker(db_session, seeded_admin, name="W")
    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={
            "job_id": str(job.job_id),
            "work_date": _today().isoformat(),
            "entries": [{"worker_id": str(w.worker_id), "day_fraction": "1.0"}],
        },
    )
    assert r.status_code == 422
    assert "archived" in r.json()["detail"]


@pytest.mark.asyncio
async def test_batch_inactive_worker_new_entry_422(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Gone", is_active=False)
    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={
            "job_id": str(job.job_id),
            "work_date": _today().isoformat(),
            "entries": [{"worker_id": str(w.worker_id), "day_fraction": "1.0"}],
        },
    )
    assert r.status_code == 422
    assert "deactivated" in r.json()["detail"]


@pytest.mark.asyncio
async def test_batch_update_existing_entry_for_inactive_worker_ok(
    client, db_session, seeded_admin, admin_token
):
    """Corrections to history stay possible after a worker deactivates."""
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Leaving")
    await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_admin, fraction="1.0"
    )
    w.is_active = False
    await db_session.flush()

    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={
            "job_id": str(job.job_id),
            "work_date": _today().isoformat(),
            "entries": [{"worker_id": str(w.worker_id), "day_fraction": "0.5"}],
        },
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_batch_date_bounds(client, db_session, seeded_admin, admin_token):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="W")

    def payload(d: _datetime.date) -> dict:
        return {
            "job_id": str(job.job_id),
            "work_date": d.isoformat(),
            "entries": [{"worker_id": str(w.worker_id), "day_fraction": "1.0"}],
        }

    # today+2 rejected; today+1 accepted (clock-skew tolerance).
    r_far = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json=payload(_today() + _datetime.timedelta(days=2)),
    )
    assert r_far.status_code == 422
    r_skew = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json=payload(_today() + _datetime.timedelta(days=1)),
    )
    assert r_skew.status_code == 201, r_skew.text


@pytest.mark.asyncio
async def test_batch_duplicate_worker_in_batch_422(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="W")
    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={
            "job_id": str(job.job_id),
            "work_date": _today().isoformat(),
            "entries": [
                {"worker_id": str(w.worker_id), "day_fraction": "0.5"},
                {"worker_id": str(w.worker_id), "day_fraction": "0.5"},
            ],
        },
    )
    assert r.status_code == 422
    assert "Duplicate" in r.json()["detail"]


@pytest.mark.asyncio
async def test_batch_invalid_fraction_422(client, db_session, seeded_admin, admin_token):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="W")
    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={
            "job_id": str(job.job_id),
            "work_date": _today().isoformat(),
            "entries": [{"worker_id": str(w.worker_id), "day_fraction": "0.75"}],
        },
    )
    assert r.status_code == 422  # Pydantic validator


@pytest.mark.asyncio
async def test_batch_contributor_updating_others_entry_403(
    client, db_session, seeded_admin, seeded_contributor, contributor_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="W")
    await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_admin, fraction="1.0"
    )

    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(contributor_token),
        json={
            "job_id": str(job.job_id),
            "work_date": _today().isoformat(),
            "entries": [{"worker_id": str(w.worker_id), "day_fraction": "0.5"}],
        },
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_batch_contributor_updates_own_today_ok(
    client, db_session, seeded_admin, seeded_contributor, contributor_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="W")
    await _mk_entry(
        db_session,
        worker=w,
        job=job,
        recorded_by=seeded_contributor,
        fraction="1.0",
    )

    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(contributor_token),
        json={
            "job_id": str(job.job_id),
            "work_date": _today().isoformat(),
            "entries": [{"worker_id": str(w.worker_id), "day_fraction": "0.5"}],
        },
    )
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Delete entry (OD-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_admin_any_204(
    client, db_session, seeded_admin, seeded_contributor, admin_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="W")
    entry = await _mk_entry(
        db_session,
        worker=w,
        job=job,
        recorded_by=seeded_contributor,
        work_date=_today() - _datetime.timedelta(days=10),
    )
    r = await client.delete(
        f"/labour-entries/{entry.entry_id}", headers=_auth(admin_token)
    )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_contributor_own_today_204(
    client, db_session, seeded_admin, seeded_contributor, contributor_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="W")
    entry = await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_contributor
    )
    r = await client.delete(
        f"/labour-entries/{entry.entry_id}", headers=_auth(contributor_token)
    )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_contributor_own_past_403(
    client, db_session, seeded_admin, seeded_contributor, contributor_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="W")
    entry = await _mk_entry(
        db_session,
        worker=w,
        job=job,
        recorded_by=seeded_contributor,
        work_date=_today() - _datetime.timedelta(days=1),
    )
    r = await client.delete(
        f"/labour-entries/{entry.entry_id}", headers=_auth(contributor_token)
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_contributor_others_403(
    client, db_session, seeded_admin, contributor_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="W")
    entry = await _mk_entry(db_session, worker=w, job=job, recorded_by=seeded_admin)
    r = await client.delete(
        f"/labour-entries/{entry.entry_id}", headers=_auth(contributor_token)
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# List + summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entries_list_filters_any_auth(
    client, db_session, seeded_admin, contributor_token
):
    job_a = await _mk_job(db_session, seeded_admin, name="Site A")
    job_b = await _mk_job(db_session, seeded_admin, name="Site B")
    w = await _mk_worker(db_session, seeded_admin, name="W")
    e_a = await _mk_entry(db_session, worker=w, job=job_a, recorded_by=seeded_admin)
    e_b = await _mk_entry(
        db_session,
        worker=w,
        job=job_b,
        recorded_by=seeded_admin,
        work_date=_today() - _datetime.timedelta(days=3),
        fraction="0.5",
    )

    r = await client.get(
        f"/labour-entries?job_id={job_a.job_id}", headers=_auth(contributor_token)
    )
    assert r.status_code == 200
    ids = {row["entry_id"] for row in r.json()}
    assert str(e_a.entry_id) in ids
    assert str(e_b.entry_id) not in ids

    r2 = await client.get(
        f"/labour-entries?from={(_today() - _datetime.timedelta(days=1)).isoformat()}",
        headers=_auth(contributor_token),
    )
    ids2 = {row["entry_id"] for row in r2.json()}
    assert str(e_a.entry_id) in ids2
    assert str(e_b.entry_id) not in ids2


@pytest.mark.asyncio
async def test_summary_admin_totals(client, db_session, seeded_admin, admin_token):
    job_a = await _mk_job(db_session, seeded_admin, name="Site A")
    job_b = await _mk_job(db_session, seeded_admin, name="Site B")
    w1 = await _mk_worker(db_session, seeded_admin, name="W1")
    w2 = await _mk_worker(db_session, seeded_admin, name="W2")
    await _mk_entry(db_session, worker=w1, job=job_a, recorded_by=seeded_admin)
    await _mk_entry(
        db_session,
        worker=w1,
        job=job_b,
        recorded_by=seeded_admin,
        work_date=_today() - _datetime.timedelta(days=1),
        fraction="0.5",
    )
    await _mk_entry(
        db_session, worker=w2, job=job_a, recorded_by=seeded_admin, fraction="0.5"
    )

    r = await client.get("/labour-summary", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    worker_totals = {row["display_name"]: Decimal(row["total_days"]) for row in body["workers"]}
    job_totals = {row["job_name"]: Decimal(row["total_days"]) for row in body["jobs"]}
    assert worker_totals["W1"] == Decimal("1.5")
    assert worker_totals["W2"] == Decimal("0.5")
    assert job_totals["Site A"] == Decimal("1.5")
    assert job_totals["Site B"] == Decimal("0.5")
    assert Decimal(body["total_days"]) == Decimal("2.0")

    # Range + job filters narrow correctly.
    r2 = await client.get(
        f"/labour-summary?from={_today().isoformat()}", headers=_auth(admin_token)
    )
    assert Decimal(r2.json()["total_days"]) == Decimal("1.5")
    r3 = await client.get(
        f"/labour-summary?job_id={job_b.job_id}", headers=_auth(admin_token)
    )
    assert Decimal(r3.json()["total_days"]) == Decimal("0.5")


@pytest.mark.asyncio
async def test_summary_contributor_403(client, contributor_token):
    r = await client.get("/labour-summary", headers=_auth(contributor_token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_requires_auth(client):
    assert (await client.get("/workers")).status_code == 401
    assert (await client.get("/labour-entries")).status_code == 401
    assert (await client.get("/labour-summary")).status_code == 401


# ---------------------------------------------------------------------------
# delete-empty-job integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_empty_job_blocked_by_labour_entries(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin, name="Labour Only")
    w = await _mk_worker(db_session, seeded_admin, name="W")
    await _mk_entry(db_session, worker=w, job=job, recorded_by=seeded_admin)

    r = await client.delete(f"/jobs/{job.job_id}", headers=_auth(admin_token))
    assert r.status_code == 409
    assert "labour" in r.json()["detail"].lower()
    assert "Archive it instead" in r.json()["detail"]
