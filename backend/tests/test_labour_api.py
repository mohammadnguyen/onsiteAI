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
    hourly_rate: str | None = None,
) -> Worker:
    worker = Worker(
        worker_id=worker_id or uuid.uuid4(),
        display_name=name,
        is_active=is_active,
        hourly_rate=Decimal(hourly_rate) if hourly_rate is not None else None,
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
    hours: str | None = None,
    rate_snapshot: str | None = None,
) -> LabourEntry:
    entry = LabourEntry(
        entry_id=uuid.uuid4(),
        worker_id=worker.worker_id,
        job_id=job.job_id,
        work_date=work_date or _today(),
        day_fraction=Decimal(fraction),
        hours=Decimal(hours) if hours is not None else None,
        rate_snapshot=Decimal(rate_snapshot) if rate_snapshot is not None else None,
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
async def test_worker_recorded_on_two_jobs_same_day(
    client, db_session, seeded_admin, admin_token
):
    """Operator 2026-07-19: a worker splits a day across sites. A worker
    already recorded 1.0 on job A can ALSO be recorded on job B the same
    date — the old "daily total cannot exceed 1.0" day_fraction cap is
    gone (cost is hours-based per job, so multi-site is correct)."""
    job_a = await _mk_job(db_session, seeded_admin, name="Site A")
    job_b = await _mk_job(db_session, seeded_admin, name="Site B")
    w = await _mk_worker(db_session, seeded_admin, name="Splitter")
    await _mk_entry(
        db_session, worker=w, job=job_a, recorded_by=seeded_admin, fraction="1.0"
    )

    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={
            "job_id": str(job_b.job_id),
            "work_date": _today().isoformat(),
            "entries": [{"worker_id": str(w.worker_id), "day_fraction": "1.0"}],
        },
    )
    assert r.status_code == 201, r.text
    rows = list(
        (
            await db_session.execute(
                select(LabourEntry).where(LabourEntry.worker_id == w.worker_id)
            )
        ).scalars()
    )
    # Two entries now — one per job.
    assert {row.job_id for row in rows} == {job_a.job_id, job_b.job_id}


@pytest.mark.asyncio
async def test_batch_hours_exceeded_across_jobs_rejected(
    client, db_session, seeded_admin, admin_token
):
    """The replacement cap: total recorded HOURS across a worker's jobs
    on one date must be plausible (≤24). 20h on job A + 6h on job B =
    26h → 422."""
    job_a = await _mk_job(db_session, seeded_admin, name="Site A")
    job_b = await _mk_job(db_session, seeded_admin, name="Site B")
    w = await _mk_worker(db_session, seeded_admin, name="Overtime")
    await _mk_entry(
        db_session,
        worker=w,
        job=job_a,
        recorded_by=seeded_admin,
        fraction="1.0",
        hours="20",
    )

    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={
            "job_id": str(job_b.job_id),
            "work_date": _today().isoformat(),
            "entries": [
                {"worker_id": str(w.worker_id), "day_fraction": "1.0", "hours": "6"}
            ],
        },
    )
    assert r.status_code == 422
    assert "24 hours" in r.json()["detail"]


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


# ---------------------------------------------------------------------------
# GET /labour-rollup (L-D1) — contributor-safe per-job rollup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollup_labourers_distinct_not_entries(
    client, db_session, seeded_admin, admin_token
):
    """labourers = COUNT(DISTINCT worker_id): same worker over many days
    counts once; two workers count as two; worker-days and days-on-site
    stay separate numbers (the "4 workers x 1 day" fix)."""
    solo = await _mk_job(db_session, seeded_admin, name="Solo")
    pair = await _mk_job(db_session, seeded_admin, name="Pair")
    w1 = await _mk_worker(db_session, seeded_admin, name="W1")
    w2 = await _mk_worker(db_session, seeded_admin, name="W2")
    # Solo: one worker across 3 distinct dates.
    for d in range(3):
        await _mk_entry(
            db_session,
            worker=w1,
            job=solo,
            recorded_by=seeded_admin,
            work_date=_today() - _datetime.timedelta(days=d),
            fraction="1.0",
        )
    # Pair: two workers on a single (different) date.
    await _mk_entry(
        db_session,
        worker=w1,
        job=pair,
        recorded_by=seeded_admin,
        work_date=_today() - _datetime.timedelta(days=3),
        fraction="1.0",
    )
    await _mk_entry(
        db_session,
        worker=w2,
        job=pair,
        recorded_by=seeded_admin,
        work_date=_today() - _datetime.timedelta(days=3),
        fraction="1.0",
    )

    solo_row = (
        await client.get(
            f"/labour-rollup?job_id={solo.job_id}", headers=_auth(admin_token)
        )
    ).json()[0]
    assert solo_row["labourers"] == 1  # same worker many days -> 1
    assert Decimal(solo_row["worker_days"]) == Decimal("3.0")  # != labourers
    assert solo_row["days_on_site"] == 3  # distinct dates

    pair_row = (
        await client.get(
            f"/labour-rollup?job_id={pair.job_id}", headers=_auth(admin_token)
        )
    ).json()[0]
    assert pair_row["labourers"] == 2  # two workers -> 2
    assert Decimal(pair_row["worker_days"]) == Decimal("2.0")
    assert pair_row["days_on_site"] == 1  # "2 workers x 1 day" -> 1 day


@pytest.mark.asyncio
async def test_rollup_contributor_200_no_money(
    client, db_session, seeded_admin, seeded_contributor, contributor_token
):
    """Contributor gets 200 with the three non-money metrics; hours and
    cost are null and no rate field exists — money stripped server-side,
    not merely hidden in the UI."""
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="W1", hourly_rate="50")
    # Costable entry: cost WOULD be present for an admin caller.
    await _mk_entry(
        db_session,
        worker=w,
        job=job,
        recorded_by=seeded_admin,
        hours="8",
        rate_snapshot="50",
    )

    r = await client.get(
        f"/labour-rollup?job_id={job.job_id}", headers=_auth(contributor_token)
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    # Non-money metrics present.
    assert row["labourers"] == 1
    assert Decimal(row["worker_days"]) == Decimal("1.0")
    assert row["days_on_site"] == 1
    # Money stripped to null for the contributor.
    assert row["total_hours"] is None
    assert row["labour_cost"] is None
    # The shape carries no rate at all, and exactly the expected keys.
    assert "hourly_rate" not in row
    assert set(row.keys()) == {
        "job_id",
        "job_name",
        "labourers",
        "worker_days",
        "days_on_site",
        "total_hours",
        "labour_cost",
    }


@pytest.mark.asyncio
async def test_rollup_admin_includes_hours_and_cost(
    client, db_session, seeded_admin, admin_token
):
    """Same data, admin caller: hours + cost are populated."""
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="W1")
    await _mk_entry(
        db_session,
        worker=w,
        job=job,
        recorded_by=seeded_admin,
        hours="8",
        rate_snapshot="50",
    )
    r = await client.get(
        f"/labour-rollup?job_id={job.job_id}", headers=_auth(admin_token)
    )
    assert r.status_code == 200, r.text
    row = r.json()[0]
    assert Decimal(row["total_hours"]) == Decimal("8")
    assert Decimal(row["labour_cost"]) == Decimal("400")  # 8 * 50


@pytest.mark.asyncio
async def test_rollup_includes_completed_job_history(
    client, db_session, seeded_admin, admin_token
):
    """Archiving a job (status completed) does not hide its labour
    history — _filtered() never filters on Job.status."""
    job = await _mk_job(
        db_session, seeded_admin, name="Old Site", status=JobStatus.completed
    )
    w = await _mk_worker(db_session, seeded_admin, name="W1")
    await _mk_entry(db_session, worker=w, job=job, recorded_by=seeded_admin)
    r = await client.get(
        f"/labour-rollup?job_id={job.job_id}", headers=_auth(admin_token)
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["labourers"] == 1


@pytest.mark.asyncio
async def test_rollup_month_to_date_range(
    client, db_session, seeded_admin, admin_token
):
    """from=<month start> narrows to calendar month-to-date; all-time
    (no from) includes the earlier entry."""
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="W1")
    today = _today()
    month_start = today.replace(day=1)
    await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_admin,
        work_date=today, fraction="1.0",
    )
    # One day before this month's start (last day of the previous month).
    await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_admin,
        work_date=month_start - _datetime.timedelta(days=1), fraction="1.0",
    )

    all_row = (
        await client.get(
            f"/labour-rollup?job_id={job.job_id}", headers=_auth(admin_token)
        )
    ).json()[0]
    assert Decimal(all_row["worker_days"]) == Decimal("2.0")

    mtd_row = (
        await client.get(
            f"/labour-rollup?job_id={job.job_id}&from={month_start.isoformat()}",
            headers=_auth(admin_token),
        )
    ).json()[0]
    assert Decimal(mtd_row["worker_days"]) == Decimal("1.0")
    assert mtd_row["days_on_site"] == 1


@pytest.mark.asyncio
async def test_requires_auth(client):
    assert (await client.get("/workers")).status_code == 401
    assert (await client.get("/labour-entries")).status_code == 401
    assert (await client.get("/labour-summary")).status_code == 401
    assert (await client.get("/labour-rollup")).status_code == 401


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


# ---------------------------------------------------------------------------
# Labour v2 (slice L-C1): rates, hours, labour cost, days-on-site
# ---------------------------------------------------------------------------


async def _get_entry(db_session, worker) -> LabourEntry:
    return (
        await db_session.execute(
            select(LabourEntry).where(LabourEntry.worker_id == worker.worker_id)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_worker_create_with_rate_admin_sees_it(client, admin_token):
    r = await client.post(
        "/workers",
        headers=_auth(admin_token),
        json={"display_name": "Sam", "hourly_rate": "40.00"},
    )
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["hourly_rate"]) == Decimal("40")


@pytest.mark.asyncio
async def test_worker_rate_hidden_from_contributor(
    client, db_session, seeded_admin, admin_token, contributor_token
):
    await _mk_worker(db_session, seeded_admin, name="Sam", hourly_rate="40.00")

    as_admin = await client.get("/workers", headers=_auth(admin_token))
    assert Decimal(as_admin.json()[0]["hourly_rate"]) == Decimal("40")

    as_contrib = await client.get("/workers", headers=_auth(contributor_token))
    assert as_contrib.json()[0]["hourly_rate"] is None


@pytest.mark.asyncio
async def test_worker_patch_rate_set_then_clear(
    client, db_session, seeded_admin, admin_token
):
    w = await _mk_worker(db_session, seeded_admin, name="Sam")

    r1 = await client.patch(
        f"/workers/{w.worker_id}",
        headers=_auth(admin_token),
        json={"hourly_rate": "42.50"},
    )
    assert Decimal(r1.json()["hourly_rate"]) == Decimal("42.5")

    r2 = await client.patch(
        f"/workers/{w.worker_id}",
        headers=_auth(admin_token),
        json={"hourly_rate": None},
    )
    assert r2.json()["hourly_rate"] is None


@pytest.mark.asyncio
async def test_batch_records_hours_and_snapshots_rate(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam", hourly_rate="40.00")

    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={
            "job_id": str(job.job_id),
            "work_date": _today().isoformat(),
            "entries": [
                {"worker_id": str(w.worker_id), "day_fraction": "1.0", "hours": "8"}
            ],
        },
    )
    assert r.status_code == 201, r.text
    assert Decimal(r.json()[0]["hours"]) == Decimal("8")
    # rate_snapshot is server-side only — never in the response.
    assert "rate_snapshot" not in r.json()[0]

    entry = await _get_entry(db_session, w)
    assert entry.hours == Decimal("8")
    assert entry.rate_snapshot == Decimal("40")


@pytest.mark.asyncio
async def test_rate_snapshot_is_write_once(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam", hourly_rate="40.00")
    body = {
        "job_id": str(job.job_id),
        "work_date": _today().isoformat(),
        "entries": [
            {"worker_id": str(w.worker_id), "day_fraction": "1.0", "hours": "8"}
        ],
    }
    await client.post("/labour-entries/batch", headers=_auth(admin_token), json=body)
    entry = await _get_entry(db_session, w)
    assert entry.rate_snapshot == Decimal("40")

    # Raise the worker's current rate, then re-save the same entry.
    await client.patch(
        f"/workers/{w.worker_id}",
        headers=_auth(admin_token),
        json={"hourly_rate": "50.00"},
    )
    body["entries"][0]["hours"] = "9"
    await client.post("/labour-entries/batch", headers=_auth(admin_token), json=body)

    await db_session.refresh(entry)
    assert entry.hours == Decimal("9")  # hours updated
    assert entry.rate_snapshot == Decimal("40")  # snapshot UNCHANGED


@pytest.mark.asyncio
async def test_hours_preserved_when_field_omitted(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam", hourly_rate="40.00")
    base = {"job_id": str(job.job_id), "work_date": _today().isoformat()}

    await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={**base, "entries": [
            {"worker_id": str(w.worker_id), "day_fraction": "1.0", "hours": "8"}
        ]},
    )
    # Re-save WITHOUT the hours field (v1-client shape) — hours preserved.
    await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={**base, "entries": [
            {"worker_id": str(w.worker_id), "day_fraction": "0.5"}
        ]},
    )
    entry = await _get_entry(db_session, w)
    assert entry.day_fraction == Decimal("0.5")
    assert entry.hours == Decimal("8")

    # Re-save WITH explicit null — hours cleared (v2-client clear).
    await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={**base, "entries": [
            {"worker_id": str(w.worker_id), "day_fraction": "1.0", "hours": None}
        ]},
    )
    await db_session.refresh(entry)
    assert entry.hours is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["25", "0", "-1"])
async def test_hours_out_of_range_rejected(
    client, db_session, seeded_admin, admin_token, bad
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam")
    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json={
            "job_id": str(job.job_id),
            "work_date": _today().isoformat(),
            "entries": [
                {"worker_id": str(w.worker_id), "day_fraction": "1.0", "hours": bad}
            ],
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_summary_days_on_site_vs_worker_days(
    client, db_session, seeded_admin, admin_token
):
    # The operator's "4 guys, 1 day" scenario: 4 workers, each a full day,
    # all on the SAME job on the SAME date. Worker-days = 4, but the job
    # ran for only 1 day on site.
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    for i in range(4):
        w = await _mk_worker(db_session, seeded_admin, name=f"W{i}")
        await _mk_entry(db_session, worker=w, job=job, recorded_by=seeded_admin)

    r = await client.get(
        f"/labour-summary?job_id={job.job_id}", headers=_auth(admin_token)
    )
    assert r.status_code == 200, r.text
    job_row = r.json()["jobs"][0]
    assert Decimal(job_row["total_days"]) == Decimal("4.0")  # worker-days
    assert job_row["days_on_site"] == 1  # the job's actual duration


@pytest.mark.asyncio
async def test_summary_labour_cost_and_completeness(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam", hourly_rate="40.00")
    # One costable entry (hours + snapshot) and one without hours.
    await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_admin,
        work_date=_today(), hours="8", rate_snapshot="40.00",
    )
    await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_admin,
        work_date=_today() - _datetime.timedelta(days=1), rate_snapshot="40.00",
    )

    r = await client.get("/labour-summary", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    wr = body["workers"][0]
    assert Decimal(wr["total_hours"]) == Decimal("8")
    assert Decimal(wr["labour_cost"]) == Decimal("320")  # 8 * 40, the no-hours row excluded
    assert wr["entries_total"] == 2
    assert wr["entries_costed"] == 1
    assert Decimal(body["total_labour_cost"]) == Decimal("320")


@pytest.mark.asyncio
async def test_summary_cost_null_when_nothing_costable(
    client, db_session, seeded_admin, admin_token
):
    # Worker has no rate; entry has hours but no snapshot -> not costable.
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam")
    await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_admin, hours="8"
    )

    r = await client.get("/labour-summary", headers=_auth(admin_token))
    body = r.json()
    assert body["workers"][0]["labour_cost"] is None
    assert body["workers"][0]["entries_costed"] == 0
    assert body["total_labour_cost"] is None


# ---------------------------------------------------------------------------
# Labour v2.1 (slice L-C3): start/end time range -> derived hours
# ---------------------------------------------------------------------------


def _batch(job, *, start=None, end=None, hours=None, fraction="1.0", worker=None):
    """Build a one-row batch payload, omitting unset optional fields."""
    entry: dict = {"worker_id": str(worker.worker_id), "day_fraction": fraction}
    if hours is not None:
        entry["hours"] = hours
    if start is not None:
        entry["start_time"] = start
    if end is not None:
        entry["end_time"] = end
    return {
        "job_id": str(job.job_id),
        "work_date": _today().isoformat(),
        "entries": [entry],
    }


@pytest.mark.asyncio
async def test_times_derive_hours_and_appear_in_response(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam", hourly_rate="40.00")

    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json=_batch(job, worker=w, start="07:30:00", end="17:00:00"),
    )
    assert r.status_code == 201, r.text
    row = r.json()[0]
    # 07:30 -> 17:00 is the full 9.5h span (no break deduction).
    assert Decimal(row["hours"]) == Decimal("9.5")
    assert row["start_time"] == "07:30:00"
    assert row["end_time"] == "17:00:00"

    entry = await _get_entry(db_session, w)
    assert entry.hours == Decimal("9.5")
    assert entry.start_time == _datetime.time(7, 30)
    assert entry.end_time == _datetime.time(17, 0)
    # Snapshot still captured at create, exactly as the hours-only path.
    assert entry.rate_snapshot == Decimal("40")


@pytest.mark.asyncio
async def test_minute_only_times_parse(
    client, db_session, seeded_admin, admin_token
):
    # The mobile sends HH:MM (typed) — confirm it parses and derives.
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam")
    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json=_batch(job, worker=w, start="08:00", end="12:15"),
    )
    assert r.status_code == 201, r.text
    assert Decimal(r.json()[0]["hours"]) == Decimal("4.25")


@pytest.mark.asyncio
async def test_times_ignore_client_sent_hours(
    client, db_session, seeded_admin, admin_token
):
    # When both times are present the range is the single source of truth:
    # a disagreeing client ``hours`` is ignored, not stored.
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam")
    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json=_batch(job, worker=w, start="08:00:00", end="16:00:00", hours="3"),
    )
    assert r.status_code == 201, r.text
    assert Decimal(r.json()[0]["hours"]) == Decimal("8")  # derived, not 3
    entry = await _get_entry(db_session, w)
    assert entry.hours == Decimal("8")


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end", [("08:00:00", None), (None, "16:00:00")])
async def test_lone_time_rejected_422(
    client, db_session, seeded_admin, admin_token, start, end
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam")
    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json=_batch(job, worker=w, start=start, end=end),
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end", [("17:00:00", "09:00:00"), ("09:00:00", "09:00:00")])
async def test_end_not_after_start_rejected_422(
    client, db_session, seeded_admin, admin_token, start, end
):
    # Same-day only: end must be strictly after start (overnight and
    # zero-length spans are out of scope).
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam")
    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json=_batch(job, worker=w, start=start, end=end),
    )
    assert r.status_code == 422, r.text
    assert "after start" in r.json()["detail"]


@pytest.mark.asyncio
async def test_hours_only_path_unchanged_no_times(
    client, db_session, seeded_admin, admin_token
):
    # Backward compatibility: a payload with hours and no times behaves
    # exactly as L-C1 — hours stored, times null.
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam")
    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json=_batch(job, worker=w, hours="8"),
    )
    assert r.status_code == 201, r.text
    row = r.json()[0]
    assert Decimal(row["hours"]) == Decimal("8")
    assert row["start_time"] is None
    assert row["end_time"] is None
    entry = await _get_entry(db_session, w)
    assert entry.start_time is None
    assert entry.end_time is None


@pytest.mark.asyncio
async def test_update_with_times_overwrites_hours(
    client, db_session, seeded_admin, admin_token
):
    # Create hours-only, then re-save the same entry with a time range:
    # the derived span overwrites the manual hours and the times persist.
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam")
    await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json=_batch(job, worker=w, hours="5"),
    )
    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json=_batch(job, worker=w, start="08:00:00", end="16:00:00"),
    )
    assert r.status_code == 201, r.text
    entry = await _get_entry(db_session, w)
    assert entry.hours == Decimal("8")  # derived span, not the old 5
    assert entry.start_time == _datetime.time(8, 0)
    assert entry.end_time == _datetime.time(16, 0)


@pytest.mark.asyncio
async def test_update_omitting_times_preserves_them(
    client, db_session, seeded_admin, admin_token
):
    # A v1-shaped re-save (day_fraction only, no hours, no times) must
    # leave an existing range untouched — never silently wipe it.
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam")
    await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json=_batch(job, worker=w, start="08:00:00", end="16:00:00"),
    )
    r = await client.post(
        "/labour-entries/batch",
        headers=_auth(admin_token),
        json=_batch(job, worker=w, fraction="0.5"),
    )
    assert r.status_code == 201, r.text
    entry = await _get_entry(db_session, w)
    assert entry.day_fraction == Decimal("0.5")
    assert entry.hours == Decimal("8")  # preserved
    assert entry.start_time == _datetime.time(8, 0)  # preserved
    assert entry.end_time == _datetime.time(16, 0)
