"""Labour v1 business logic (slice L-A).

HTTP-agnostic, mirroring the house service pattern: typed inputs,
persisted rows or domain exceptions out; the HTTP layer maps
exceptions to status codes.

Core rules enforced here (operator decisions 1-10, L-A plan):

* Workers are roster records — created/updated by admins only,
  deactivated never deleted (no delete function exists).
* Attendance is recorded in day fractions ∈ {0.5, 1.0} against
  ACTIVE jobs and (for new entries) ACTIVE workers only.
* A worker CAN be recorded on MULTIPLE jobs the same ``work_date`` —
  they split their day across sites (operator 2026-07-19). Labour COST
  is hours-based (``hours * rate_snapshot``) and stays correct per job;
  ``day_fraction`` is only the attendance marker. The former "total
  day_fraction across jobs ≤ 1.0" cap is REPLACED by a plausibility cap
  on total HOURS across all the worker's jobs that date (≤ 24). This
  cross-row rule cannot be a DB CHECK; it is enforced inside the write
  transaction with ``SELECT … FOR UPDATE``
  row locks on the worker's existing rows for that date (workers are
  processed in sorted order to avoid deadlocks between concurrent
  batches).
* Batch saves are ALL-OR-NOTHING: the first violation raises and the
  request-scoped transaction rolls back everything.
* Edit/delete permissions (OD-1): admins always; contributors only
  their OWN entries whose ``work_date`` is today or later (the
  "later" allowance covers the same +1-day clock-skew tolerance used
  at creation — a Sydney morning is UTC "tomorrow").
* No payroll concepts anywhere.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobStatus, LabourEntry, User, UserRole, Worker


def _day_lock_key(worker_id: uuid.UUID, work_date: date) -> int:
    """Stable signed 64-bit key for a per-(worker, date) advisory lock.

    Postgres advisory-lock keys are bigints (signed 64-bit); we hash the
    ``worker_id + work_date`` pair into that domain so the key is stable
    across processes and collision-resistant.
    """
    digest = hashlib.blake2b(
        f"{worker_id}:{work_date.isoformat()}".encode(), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big", signed=True)

# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class WorkerNotFound(Exception):
    """Raised when a worker_id doesn't resolve."""

    def __init__(self, worker_id: uuid.UUID):
        self.worker_id = worker_id
        super().__init__(f"Worker {worker_id} not found")


class LabourEntryNotFound(Exception):
    """Raised when an entry_id doesn't resolve."""

    def __init__(self, entry_id: uuid.UUID):
        self.entry_id = entry_id
        super().__init__(f"Labour entry {entry_id} not found")


class LabourValidationError(Exception):
    """Raised on save-time validation errors (maps to 422)."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class LabourEditForbidden(Exception):
    """Raised when the caller may not modify an entry (maps to 403)."""

    def __init__(self, detail: str = "You can only change your own entries for today"):
        self.detail = detail
        super().__init__(detail)


# Mirrors the expense-side tolerances (CHP-4/CHP-5 reasoning): one day
# of clock skew forward; five years back.
_FUTURE_TOLERANCE_DAYS = 1
_MAX_PAST_YEARS = 5

# A worker's recorded hours across ALL jobs on one date must stay
# plausible. Replaces the old day_fraction ≤1.0 cap so a worker can be
# recorded on multiple sites the same day (hours drive cost, per job).
_MAX_DAY_HOURS = Decimal("24")


def _is_admin(user: User) -> bool:
    return user.role == UserRole.admin


def _validate_work_date(work_date: date) -> None:
    today = date.today()
    if work_date > today + timedelta(days=_FUTURE_TOLERANCE_DAYS):
        raise LabourValidationError("work_date cannot be in the future")
    if work_date < today - timedelta(days=365 * _MAX_PAST_YEARS):
        raise LabourValidationError("work_date is too far in the past")


def _can_modify(user: User, entry: LabourEntry) -> bool:
    """OD-1: admin always; contributor = own entry, work_date today+.

    ``work_date >= today`` (server UTC) deliberately includes the
    +1-day skew window: a Sydney-morning entry carries UTC-tomorrow's
    date and must remain editable by its recorder that same morning.
    """
    if _is_admin(user):
        return True
    return (
        entry.recorded_by_user_id == user.user_id
        and entry.work_date >= date.today()
    )


# ---------------------------------------------------------------------------
# Hours-from-times derivation (L-C3)
# ---------------------------------------------------------------------------

# Two-decimal quantum matching labour_entries.hours NUMERIC(4,2).
_HOURS_QUANTUM = Decimal("0.01")
_SECONDS_PER_HOUR = Decimal("3600")


def _hours_between(start: time, end: time) -> Decimal:
    """Derive labour hours from a same-day ``start``->``end`` span.

    The FULL span — NO break deduction (operator decision). Same-day
    only: these are TIME-of-day values, not timestamps, so the caller
    must ensure ``end > start`` (overnight is out of scope). Quantised to
    two decimals to fit ``labour_entries.hours`` NUMERIC(4,2).
    """
    start_secs = start.hour * 3600 + start.minute * 60 + start.second
    end_secs = end.hour * 3600 + end.minute * 60 + end.second
    return (Decimal(end_secs - start_secs) / _SECONDS_PER_HOUR).quantize(
        _HOURS_QUANTUM
    )


def _derive_times(item) -> tuple[Decimal, time, time] | None:
    """Resolve a batch item's time range into derived hours, or ``None``.

    Three L-C3 cases (operator-locked):

    * BOTH times present -> returns ``(hours, start, end)`` with hours
      DERIVED from ``end - start``; any client-sent ``hours`` is IGNORED
      so the range is the single source of truth. ``end`` must be after
      ``start`` (same-day) or it raises.
    * EXACTLY ONE present -> raises (the schema guards this too; the
      service re-checks so the rule holds even on a direct call).
    * NEITHER present -> returns ``None`` so the caller keeps the
      unchanged hours-only path.
    """
    start = item.start_time
    end = item.end_time
    if start is None and end is None:
        return None
    if start is None or end is None:
        raise LabourValidationError(
            "Provide both a start time and an end time, or neither"
        )
    if end <= start:
        raise LabourValidationError(
            "End time must be after start time on the same day"
        )
    hours = _hours_between(start, end)
    if hours <= 0:
        raise LabourValidationError(
            "The time range is too short to record any hours"
        )
    return hours, start, end


# ---------------------------------------------------------------------------
# Workers (roster)
# ---------------------------------------------------------------------------


async def list_workers(
    db: AsyncSession, *, include_inactive: bool = False
) -> list[Worker]:
    stmt = select(Worker).order_by(Worker.display_name, Worker.created_at)
    if not include_inactive:
        stmt = stmt.where(Worker.is_active.is_(True))
    return list((await db.execute(stmt)).scalars().all())


async def create_worker(
    db: AsyncSession,
    *,
    created_by: User,
    display_name: str,
    note: str | None = None,
    hourly_rate: Decimal | None = None,
) -> Worker:
    worker = Worker(
        worker_id=uuid.uuid4(),
        display_name=display_name.strip(),
        note=note,
        hourly_rate=hourly_rate,
        created_by=created_by.user_id,
    )
    db.add(worker)
    await db.flush()
    return worker


async def update_worker(db: AsyncSession, worker_id: uuid.UUID, **fields) -> Worker:
    """Partial update; caller passes only the fields the client set.

    ``hourly_rate`` may be set to a value or to explicit null (clear).
    Changing it sets the worker's CURRENT rate only — it never touches
    the ``rate_snapshot`` on existing entries (those are write-once).
    """
    worker = await db.get(Worker, worker_id)
    if worker is None:
        raise WorkerNotFound(worker_id)
    if "display_name" in fields and fields["display_name"] is not None:
        worker.display_name = fields["display_name"].strip()
    if "note" in fields:
        worker.note = fields["note"]
    if "is_active" in fields and fields["is_active"] is not None:
        worker.is_active = fields["is_active"]
    if "hourly_rate" in fields:
        worker.hourly_rate = fields["hourly_rate"]
    await db.flush()
    return worker


# ---------------------------------------------------------------------------
# Attendance entries
# ---------------------------------------------------------------------------


async def batch_upsert_entries(
    db: AsyncSession,
    *,
    current_user: User,
    job_id: uuid.UUID,
    work_date: date,
    items: list,  # list[LabourBatchItem]-shaped (worker_id, day_fraction)
) -> list[LabourEntry]:
    """The tick-screen save: create-or-update one row per (worker, job, date).

    ALL-OR-NOTHING — any violation raises and nothing persists.
    Existing rows update ``day_fraction`` only (``recorded_by`` stays
    the original recorder); updates are permission-checked per OD-1.
    New rows require an ACTIVE worker; updates to existing rows are
    allowed even if the worker was deactivated since (corrections of
    history must stay possible).
    """
    _validate_work_date(work_date)

    job = await db.get(Job, job_id)
    if job is None:
        raise LabourValidationError("Job not found")
    if job.status != JobStatus.active:
        raise LabourValidationError(
            "Job is archived — attendance can only be recorded against active jobs"
        )

    worker_ids = [item.worker_id for item in items]
    if len(set(worker_ids)) != len(worker_ids):
        raise LabourValidationError("Duplicate worker in batch")

    workers_by_id: dict[uuid.UUID, Worker] = {
        w.worker_id: w
        for w in (
            await db.execute(select(Worker).where(Worker.worker_id.in_(worker_ids)))
        ).scalars()
    }
    missing = [str(wid) for wid in worker_ids if wid not in workers_by_id]
    if missing:
        raise LabourValidationError(f"Worker not found: {', '.join(missing)}")

    result: list[LabourEntry] = []
    # Sorted worker order keeps lock acquisition deterministic so two
    # concurrent batches touching the same workers cannot deadlock.
    for worker_id in sorted(worker_ids, key=str):
        item = next(i for i in items if i.worker_id == worker_id)
        worker = workers_by_id[worker_id]
        fraction = Decimal(item.day_fraction)

        # Take a per-(worker, date) transaction-scoped advisory lock BEFORE
        # reading the day's rows (audit D-2). The row-level FOR UPDATE below
        # locks nothing when the worker has no rows for this date yet, so two
        # concurrent FIRST inserts into DIFFERENT jobs would both read
        # other_total=0 and both commit -> a 2.0-day total. The advisory lock
        # covers the not-yet-existing rows, forcing the second batch to block
        # until the first commits and then recompute against the new row.
        # Acquired in the same sorted-worker order as the row locks, so two
        # batches touching overlapping workers cannot deadlock.
        await db.execute(select(func.pg_advisory_xact_lock(_day_lock_key(worker_id, work_date))))

        # Lock every existing row this worker has on this date — across
        # ALL jobs — so the <=1.0 total is computed against stable rows.
        locked_rows = list(
            (
                await db.execute(
                    select(LabourEntry)
                    .where(
                        LabourEntry.worker_id == worker_id,
                        LabourEntry.work_date == work_date,
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        existing_here = next((r for r in locked_rows if r.job_id == job_id), None)

        if existing_here is None and not worker.is_active:
            raise LabourValidationError(
                f"{worker.display_name} is deactivated — reactivate the worker "
                "to record new attendance"
            )
        if existing_here is not None and not _can_modify(current_user, existing_here):
            raise LabourEditForbidden(
                f"{worker.display_name}'s entry for {work_date.isoformat()} was "
                "recorded by someone else — only admins can change it"
                if existing_here.recorded_by_user_id != current_user.user_id
                else "You can only change your own entries for today"
            )

        # L-C3: if a start/end range is supplied, hours is DERIVED from it
        # (and the times stored); otherwise None and the hours-only path
        # below applies. Raises on a lone time or end<=start.
        derived = _derive_times(item)

        # Cross-job HOURS plausibility cap (replaces the old day_fraction
        # ≤1.0 cap — operator 2026-07-19). A worker splits a day across
        # sites; cost is hours-based per job and stays correct, so only
        # the TOTAL recorded hours across their jobs that date is
        # bounded (≤24). Day-fraction-only entries carry no hours and
        # are not capped (multi-site attendance without hours is fine).
        if derived is not None:
            new_hours = derived[0]
        elif "hours" in item.model_fields_set:
            new_hours = item.hours
        elif existing_here is not None:
            new_hours = existing_here.hours
        else:
            new_hours = None
        if new_hours is not None:
            other_hours = sum(
                (
                    r.hours
                    for r in locked_rows
                    if r.job_id != job_id and r.hours is not None
                ),
                Decimal("0"),
            )
            if other_hours + new_hours > _MAX_DAY_HOURS:
                raise LabourValidationError(
                    f"{worker.display_name} already has {other_hours}h "
                    f"recorded on {work_date.isoformat()} — the daily total "
                    "across sites cannot exceed 24 hours"
                )

        if existing_here is not None:
            existing_here.day_fraction = fraction
            if derived is not None:
                # Time range is the single source of truth: store the
                # times and overwrite hours with the derived span,
                # ignoring any client-sent hours.
                (
                    existing_here.hours,
                    existing_here.start_time,
                    existing_here.end_time,
                ) = derived
            else:
                # No time range supplied — keep the L-C1 hours-only
                # semantics. Update hours only when the client explicitly
                # sent the field (a v2 client always sends it, so null
                # clears it; a v1 client never sends it, so existing hours
                # are preserved). rate_snapshot is write-once — never
                # modified on edit, so a later rate change can't rewrite
                # historical labour cost.
                if "hours" in item.model_fields_set:
                    existing_here.hours = item.hours
                # Only an explicit null on the time fields clears stored
                # times; a client that omits them (v1 shape) leaves them
                # untouched.
                if (
                    "start_time" in item.model_fields_set
                    or "end_time" in item.model_fields_set
                ):
                    existing_here.start_time = None
                    existing_here.end_time = None
            result.append(existing_here)
        else:
            if derived is not None:
                entry_hours, start_time_val, end_time_val = derived
            else:
                entry_hours, start_time_val, end_time_val = item.hours, None, None
            entry = LabourEntry(
                entry_id=uuid.uuid4(),
                worker_id=worker_id,
                job_id=job_id,
                work_date=work_date,
                day_fraction=fraction,
                hours=entry_hours,
                start_time=start_time_val,
                end_time=end_time_val,
                # Snapshot the worker's CURRENT rate at create time (may be
                # None). Write-once: never refreshed afterwards.
                rate_snapshot=worker.hourly_rate,
                recorded_by_user_id=current_user.user_id,
            )
            db.add(entry)
            result.append(entry)

    await db.flush()
    # UPDATEd rows have ``updated_at`` expired by the server-side
    # onupdate default; refresh inside the async context so response
    # serialization never triggers a lazy (sync) attribute load.
    for entry in result:
        await db.refresh(entry)
    return result


async def delete_entry(
    db: AsyncSession, *, current_user: User, entry_id: uuid.UUID
) -> None:
    entry = await db.get(LabourEntry, entry_id)
    if entry is None:
        raise LabourEntryNotFound(entry_id)
    if not _can_modify(current_user, entry):
        raise LabourEditForbidden()
    await db.delete(entry)
    await db.flush()


async def list_entries(
    db: AsyncSession,
    *,
    job_id: uuid.UUID | None = None,
    worker_id: uuid.UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = 200,
) -> list[LabourEntry]:
    stmt = select(LabourEntry).order_by(
        LabourEntry.work_date.desc(), LabourEntry.created_at.desc()
    )
    if job_id is not None:
        stmt = stmt.where(LabourEntry.job_id == job_id)
    if worker_id is not None:
        stmt = stmt.where(LabourEntry.worker_id == worker_id)
    if from_date is not None:
        stmt = stmt.where(LabourEntry.work_date >= from_date)
    if to_date is not None:
        stmt = stmt.where(LabourEntry.work_date <= to_date)
    stmt = stmt.limit(min(limit, 500))
    return list((await db.execute(stmt)).scalars().all())


async def summarize(
    db: AsyncSession,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
    job_id: uuid.UUID | None = None,
) -> dict:
    """Per-worker and per-job labour totals for the filtered range.

    Returns a dict the route validates into ``LabourSummary``. Archived
    jobs' historical entries are INCLUDED by design — history survives
    archiving.

    Each grouping carries (v2):
    * ``total_days`` — worker-days (sum of day fractions).
    * ``total_hours`` — sum of recorded hours (null when none recorded).
    * ``labour_cost`` — sum of ``hours * rate_snapshot``; null when no
      entry in the group is costable. Computed on read, never stored.
    * ``entries_total`` / ``entries_costed`` — lets the client flag an
      incomplete cost (some entries miss hours or a rate snapshot).
    Per job additionally: ``days_on_site`` — distinct dates anyone was
    on the job (the job's DURATION, vs worker-days which is INPUT) —
    and ``labourers`` — distinct workers on the job (its HEADCOUNT, vs
    worker-days which sums fractions). The three are deliberately
    separate so "4 workers x 1 day" reads as 4 labourers / 4
    worker-days / 1 day on site, not "4 days".

    Cost math relies on SQL: ``hours * rate_snapshot`` is null when
    either operand is null, and ``sum``/``count(...) filter`` ignore
    nulls — so incomplete rows neither corrupt nor are guessed into the
    total.
    """

    def _filtered(stmt):
        if from_date is not None:
            stmt = stmt.where(LabourEntry.work_date >= from_date)
        if to_date is not None:
            stmt = stmt.where(LabourEntry.work_date <= to_date)
        if job_id is not None:
            stmt = stmt.where(LabourEntry.job_id == job_id)
        return stmt

    _cost = func.sum(LabourEntry.hours * LabourEntry.rate_snapshot)
    _hours = func.sum(LabourEntry.hours)
    _total_entries = func.count(LabourEntry.entry_id)
    _costed_entries = func.count(LabourEntry.entry_id).filter(
        LabourEntry.hours.isnot(None),
        LabourEntry.rate_snapshot.isnot(None),
    )

    worker_rows = (
        await db.execute(
            _filtered(
                select(
                    Worker.worker_id,
                    Worker.display_name,
                    func.sum(LabourEntry.day_fraction).label("total_days"),
                    _hours.label("total_hours"),
                    _cost.label("labour_cost"),
                    _total_entries.label("entries_total"),
                    _costed_entries.label("entries_costed"),
                )
                .join(LabourEntry, LabourEntry.worker_id == Worker.worker_id)
                .group_by(Worker.worker_id, Worker.display_name)
                .order_by(func.sum(LabourEntry.day_fraction).desc())
            )
        )
    ).all()

    job_rows = (
        await db.execute(
            _filtered(
                select(
                    Job.job_id,
                    Job.job_name,
                    func.sum(LabourEntry.day_fraction).label("total_days"),
                    func.count(func.distinct(LabourEntry.work_date)).label(
                        "days_on_site"
                    ),
                    func.count(func.distinct(LabourEntry.worker_id)).label(
                        "labourers"
                    ),
                    _hours.label("total_hours"),
                    _cost.label("labour_cost"),
                    _total_entries.label("entries_total"),
                    _costed_entries.label("entries_costed"),
                )
                .join(LabourEntry, LabourEntry.job_id == Job.job_id)
                .group_by(Job.job_id, Job.job_name)
                .order_by(func.sum(LabourEntry.day_fraction).desc())
            )
        )
    ).all()

    totals = (
        await db.execute(
            _filtered(
                select(
                    func.coalesce(func.sum(LabourEntry.day_fraction), 0).label(
                        "total_days"
                    ),
                    _hours.label("total_hours"),
                    _cost.label("total_labour_cost"),
                    _total_entries.label("entries_total"),
                    _costed_entries.label("entries_costed"),
                )
            )
        )
    ).one()

    return {
        "workers": [
            {
                "worker_id": r.worker_id,
                "display_name": r.display_name,
                "total_days": r.total_days,
                "total_hours": r.total_hours,
                "labour_cost": r.labour_cost,
                "entries_total": r.entries_total,
                "entries_costed": r.entries_costed,
            }
            for r in worker_rows
        ],
        "jobs": [
            {
                "job_id": r.job_id,
                "job_name": r.job_name,
                "total_days": r.total_days,
                "days_on_site": r.days_on_site,
                "labourers": r.labourers,
                "total_hours": r.total_hours,
                "labour_cost": r.labour_cost,
                "entries_total": r.entries_total,
                "entries_costed": r.entries_costed,
            }
            for r in job_rows
        ],
        "total_days": Decimal(totals.total_days or 0),
        "total_hours": totals.total_hours,
        "total_labour_cost": totals.total_labour_cost,
        "entries_total": totals.entries_total or 0,
        "entries_costed": totals.entries_costed or 0,
    }
