"""Changing a flag: the builder's decision and the flag's job (DEC-PV-001).

One PATCH, one row lock, one audit row per change actually made. Nothing
here touches the source Site Log entry or any task: a task's status never
changes a flag, and a flag never changes a task.

* ``decision`` — review state, end reason and note together, as ONE unit,
  builder only. A decision from anyone else is refused (403) before it is
  compared with the current one: comparing first would let a caller who may
  not read the note probe it with guesses that come back as no-ops.
* ``job_id`` — set while empty by the builder or the flagger; changed once
  set by the builder only; never cleared; never a completed job. A job sent
  with its current value is not a change: it needs no permission and writes
  nothing.

Denial order, as in the task service: not visible → 404; visible but not
allowed → 403; then validation (422, or 404 for an unknown job).
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.models.potential_variation import PvAuditAction, PvEndReason, PvReviewState
from app.models.user import User

from .access import can_change_job, can_decide, can_set_job
from .errors import PvForbidden, PvValidationError
from .rules import audit_row, check_decision, clean_note, str_or_none, target_job
from .views import PvView, build_detail, load_visible_pv

logger = logging.getLogger("app.services.potential_variations")


@dataclass(frozen=True)
class Decision:
    """What the builder decided: all three parts, always sent together."""

    review_state: PvReviewState | None
    ended_reason: PvEndReason | None
    note: str | None


@contextlib.asynccontextmanager
async def _savepoint(db: AsyncSession) -> AsyncIterator[AsyncSessionTransaction]:
    """SAVEPOINT owning the row lock (the Site Log and task rule): a denial
    or a no-op rolls back only this savepoint and leaves the caller's
    transaction untouched; only the positive path commits, afterwards."""
    sp = await db.begin_nested()
    try:
        yield sp
    except BaseException:
        if sp.is_active:
            await sp.rollback()
        raise
    else:
        if sp.is_active:
            await sp.commit()  # RELEASE SAVEPOINT


def _enum_value(value: PvReviewState | PvEndReason | None) -> str | None:
    return value.value if value is not None else None


async def update_pv(
    db: AsyncSession,
    *,
    user: User,
    pv_id: uuid.UUID,
    decision: Decision | None,
    job_id_sent: bool,
    job_id: uuid.UUID | None,
) -> PvView:
    """Apply a decision and/or a job change. Returns the detail view."""
    if decision is None and not job_id_sent:
        raise PvValidationError("nothing to change")

    async with _savepoint(db) as sp:
        pv = await load_visible_pv(db, user, pv_id, lock=True)

        # -- permissions (403) ---------------------------------------------
        if decision is not None and not can_decide(user):
            raise PvForbidden("Admin only")
        job_changes = job_id_sent and job_id != pv.job_id
        if job_changes:
            if pv.job_id is None:
                if not can_set_job(user, pv):
                    raise PvForbidden()
            elif not can_change_job(user, pv):
                raise PvForbidden("Admin only")

        # -- validation (422 / 404) ------------------------------------------
        decision_changes = False
        new_state: PvReviewState | None = None
        new_reason: PvEndReason | None = None
        new_note: str | None = None
        if decision is not None:
            new_note = clean_note(decision.note)
            check_decision(decision.review_state, decision.ended_reason, new_note)
            new_state, new_reason = decision.review_state, decision.ended_reason
            decision_changes = (new_state, new_reason, new_note) != (
                pv.review_state,
                pv.ended_reason,
                pv.decision_note,
            )
        if job_changes:
            if job_id is None:
                raise PvValidationError(
                    "a potential variation's job can be changed but not removed"
                )
            await target_job(db, user, job_id)

        if not decision_changes and not job_changes:
            await sp.rollback()  # a pure no-op: no write, no audit
            return await build_detail(db, user, pv)

        # -- apply, one audit row per change -----------------------------------
        from_state = pv.review_state
        if decision_changes:
            db.add(
                audit_row(
                    pv.potential_variation_id,
                    user.user_id,
                    PvAuditAction.decided,
                    {
                        "from": {
                            "review_state": _enum_value(pv.review_state),
                            "ended_reason": _enum_value(pv.ended_reason),
                            "note": pv.decision_note,
                        },
                        "to": {
                            "review_state": _enum_value(new_state),
                            "ended_reason": _enum_value(new_reason),
                            "note": new_note,
                        },
                    },
                )
            )
            pv.review_state = new_state
            pv.ended_reason = new_reason
            pv.decision_note = new_note
            pv.decided_by_user_id = user.user_id
            pv.decided_at = func.now()
        if job_changes:
            db.add(
                audit_row(
                    pv.potential_variation_id,
                    user.user_id,
                    PvAuditAction.job_set,
                    {"from": str_or_none(pv.job_id), "to": str(job_id)},
                )
            )
            pv.job_id = job_id

    await db.commit()
    # decided_at and updated_at are set by the database: read them back.
    await db.refresh(pv)
    # Ids and states only: never the title, the entry text or the note.
    logger.info(
        "potential variation updated pv_id=%s decided=%s from_state=%s to_state=%s "
        "ended_reason=%s has_note=%s job_set=%s",
        pv.potential_variation_id,
        decision_changes,
        _enum_value(from_state),
        _enum_value(pv.review_state),
        _enum_value(pv.ended_reason),
        pv.decision_note is not None,
        job_changes,
    )
    return await build_detail(db, user, pv)
