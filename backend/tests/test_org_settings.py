"""Org-settings endpoints + labour day-entry cost derivation.

Founder decision 2026-08-24: an hours-less labour entry derives cost
from ``day_fraction * org default_day_hours * rate_snapshot`` at READ
time. Days stay days — entries are never rewritten into hours — and
changing the setting deliberately re-prices historical hours-less
entries (pricing rule, not a per-entry fact).
"""

import datetime as _datetime
import uuid
from decimal import Decimal

import pytest

from app.models import Job, JobStatus, LabourEntry, Worker


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _today() -> _datetime.date:
    return _datetime.datetime.now(_datetime.UTC).date()


async def _mk_job(db_session, admin, *, name: str) -> Job:
    job = Job(
        job_id=uuid.uuid4(),
        job_name=name,
        status=JobStatus.active,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


async def _mk_worker(db_session, admin, *, name: str, hourly_rate=None) -> Worker:
    worker = Worker(
        worker_id=uuid.uuid4(),
        display_name=name,
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
# Endpoint auth + shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_org_settings_defaults_to_ten(client, admin_token):
    r = await client.get("/org-settings", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["default_day_hours"]) == Decimal("10.00")


@pytest.mark.asyncio
async def test_org_settings_admin_only(client, contributor_token):
    # Costing parameter — conservative money visibility: contributors
    # get 403 on BOTH verbs (mirrors the /workers hourly_rate posture).
    r = await client.get("/org-settings", headers=_auth(contributor_token))
    assert r.status_code == 403
    r = await client.patch(
        "/org-settings",
        json={"default_day_hours": "8"},
        headers=_auth(contributor_token),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_patch_org_settings_updates_and_persists(client, admin_token):
    r = await client.patch(
        "/org-settings",
        json={"default_day_hours": "8.50"},
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["default_day_hours"]) == Decimal("8.50")
    r = await client.get("/org-settings", headers=_auth(admin_token))
    assert Decimal(r.json()["default_day_hours"]) == Decimal("8.50")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["0", "-1", "24.01", "999"])
async def test_patch_org_settings_bounds(client, admin_token, bad):
    r = await client.patch(
        "/org-settings",
        json={"default_day_hours": bad},
        headers=_auth(admin_token),
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Cost derivation for hours-less entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_day_only_entry_derives_cost(
    client, db_session, seeded_admin, admin_token
):
    """1.0 day, no hours, rate 50 -> 1.0 * 10 * 50 = 500."""
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam", hourly_rate="50")
    await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_admin,
        rate_snapshot="50",
    )

    r = await client.get("/labour-summary", headers=_auth(admin_token))
    body = r.json()
    wr = body["workers"][0]
    assert Decimal(wr["labour_cost"]) == Decimal("500")
    # Days stay days: no recorded hours means total_hours stays null.
    assert wr["total_hours"] is None
    assert wr["entries_costed"] == 1
    assert Decimal(body["total_labour_cost"]) == Decimal("500")


@pytest.mark.asyncio
async def test_half_day_entry_derives_half_cost(
    client, db_session, seeded_admin, admin_token
):
    """0.5 day, no hours, rate 50 -> 0.5 * 10 * 50 = 250."""
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam", hourly_rate="50")
    await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_admin,
        fraction="0.5", rate_snapshot="50",
    )

    r = await client.get("/labour-summary", headers=_auth(admin_token))
    assert Decimal(r.json()["total_labour_cost"]) == Decimal("250")


@pytest.mark.asyncio
async def test_recorded_hours_beat_the_day_derivation(
    client, db_session, seeded_admin, admin_token
):
    """An entry WITH hours ignores the parameter entirely: 7.5h * 40 =
    300, not day_fraction * 10 * 40."""
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam", hourly_rate="40")
    await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_admin,
        hours="7.5", rate_snapshot="40",
    )

    r = await client.get("/labour-summary", headers=_auth(admin_token))
    assert Decimal(r.json()["total_labour_cost"]) == Decimal("300")


@pytest.mark.asyncio
async def test_setting_change_reprices_history(
    client, db_session, seeded_admin, admin_token
):
    """The founder's chosen semantics: the parameter is retroactive for
    hours-less entries (500 at 10h/day -> 400 at 8h/day), while entries
    with recorded hours never move."""
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam", hourly_rate="50")
    yesterday = _today() - _datetime.timedelta(days=1)
    await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_admin,
        work_date=yesterday, rate_snapshot="50",
    )
    await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_admin,
        hours="8", rate_snapshot="50",
    )

    r = await client.get("/labour-summary", headers=_auth(admin_token))
    assert Decimal(r.json()["total_labour_cost"]) == Decimal("900")  # 500 + 400

    r = await client.patch(
        "/org-settings",
        json={"default_day_hours": "8"},
        headers=_auth(admin_token),
    )
    assert r.status_code == 200

    r = await client.get("/labour-summary", headers=_auth(admin_token))
    assert Decimal(r.json()["total_labour_cost"]) == Decimal("800")  # 400 + 400


@pytest.mark.asyncio
async def test_rateless_day_entry_still_uncosted(
    client, db_session, seeded_admin, admin_token
):
    """No rate snapshot -> no guessed cost, derivation or not."""
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam")
    await _mk_entry(db_session, worker=w, job=job, recorded_by=seeded_admin)

    r = await client.get("/labour-summary", headers=_auth(admin_token))
    body = r.json()
    assert body["workers"][0]["labour_cost"] is None
    assert body["workers"][0]["entries_costed"] == 0
    assert body["total_labour_cost"] is None


@pytest.mark.asyncio
async def test_contributor_rollup_still_money_free(
    client, db_session, seeded_admin, contributor_token
):
    """Derived cost is stripped for contributors exactly like recorded
    cost — the rollup's money-free contract is unchanged."""
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    w = await _mk_worker(db_session, seeded_admin, name="Sam", hourly_rate="50")
    await _mk_entry(
        db_session, worker=w, job=job, recorded_by=seeded_admin,
        rate_snapshot="50",
    )

    r = await client.get(
        f"/labour-rollup?job_id={job.job_id}", headers=_auth(contributor_token)
    )
    assert r.status_code == 200, r.text
    row = r.json()[0]
    assert row["labour_cost"] is None
    assert row["total_hours"] is None
    assert Decimal(row["worker_days"]) == Decimal("1.0")
