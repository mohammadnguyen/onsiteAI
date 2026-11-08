"""Who may see and change a Potential Variation (DEC-PV-001). Pure helpers:
no IO, no writes. Built only on mechanisms that already exist — the role
and the Site Log read rules.

* Flag: anyone who may read the source entry (``can_read_event``); checked
  by the create service against the entry itself.
* View: the builder and the person who flagged it. Nobody else — narrower
  than Task on purpose, because a flag is a commercial opinion.
* Decide (state, end reason, note — one unit) and read the builder's note:
  the builder only.
* Job: set it while it is empty — builder or flagger; change it once set —
  builder only.

v1 has two roles, admin and contributor; the builder is the admin role.
Before release the founder confirms the real accounts' roles match this
(DEC-PV-001 binding section).

Viewing a flag never grants the source entry or its files: the entry's
text, the title derived from it and its attachments stay behind
``can_read_event`` (see ``views``).
"""

from __future__ import annotations

from sqlalchemy.sql.elements import ColumnElement

from app.models.potential_variation import PotentialVariation
from app.models.user import User, UserRole


def is_builder(user: User) -> bool:
    return user.role == UserRole.admin


def _is_flagger(user: User, pv: PotentialVariation) -> bool:
    return pv.created_by_user_id == user.user_id


def can_view_pv(user: User, pv: PotentialVariation) -> bool:
    return is_builder(user) or _is_flagger(user, pv)


def visible_pvs_clause(user: User) -> ColumnElement[bool] | None:
    """SQL form of :func:`can_view_pv` for list queries; None = no filter.

    Exact by construction (the same two branches); a test still compares
    the two over a matrix of flags and users.
    """
    if is_builder(user):
        return None
    return PotentialVariation.created_by_user_id == user.user_id


def can_decide(user: User) -> bool:
    return is_builder(user)


def can_read_note(user: User) -> bool:
    return is_builder(user)


def can_set_job(user: User, pv: PotentialVariation) -> bool:
    """First set, from no job."""
    return pv.job_id is None and (is_builder(user) or _is_flagger(user, pv))


def can_change_job(user: User, pv: PotentialVariation) -> bool:
    """Move a flag that already has a job: builder only."""
    return pv.job_id is not None and is_builder(user)
