"""Potential Variation service (DEC-PV-001: a human flag on a Site Log
entry, for the builder to check).

Sole writer of ``potential_variations`` and ``potential_variation_audit_log``.
Reads Site Log entries, their revisions and attachments; never writes to any
Site Log or task table, so a flag can never change the record it came from,
and a task can never change a flag (or the reverse).

Modules, dependencies one way: ``errors`` → ``access`` → ``rules`` →
``views`` → ``create`` / ``update``.

* :mod:`errors` — the exception types; the API maps them to status codes.
* :mod:`access` — who may see, decide and set the job. Pure.
* :mod:`rules` — title and note cleaning, the decision rule, the create
  fingerprint, audit rows, the job check.
* :mod:`views` — access resolution, source protection, the view model, the
  reads.
* :mod:`create` — flag an entry, idempotent per creator and key, pinned to
  the version the person confirmed.
* :mod:`update` — the builder's decision and the job, one audit row each.

Callers use ``from app.services import potential_variations as pv_svc``.
"""

from __future__ import annotations

from .create import CreateResult, create_pv
from .errors import (
    PvError,
    PvFingerprintMismatch,
    PvForbidden,
    PvJobCompleted,
    PvJobNotFound,
    PvNotFound,
    PvSourceChanged,
    PvSourceNotFound,
    PvSourceWithdrawn,
    PvValidationError,
)
from .update import Decision, update_pv
from .views import (
    PV_PAGE_DEFAULT,
    PV_PAGE_MAX,
    AttachmentSummary,
    PinnedRevision,
    PvPermissions,
    PvView,
    SourceDetail,
    get_pv,
    list_event_pvs,
    list_pvs,
)

__all__ = [
    "PV_PAGE_DEFAULT",
    "PV_PAGE_MAX",
    "AttachmentSummary",
    "CreateResult",
    "Decision",
    "PinnedRevision",
    "PvError",
    "PvFingerprintMismatch",
    "PvForbidden",
    "PvJobCompleted",
    "PvJobNotFound",
    "PvNotFound",
    "PvPermissions",
    "PvSourceChanged",
    "PvSourceNotFound",
    "PvSourceWithdrawn",
    "PvValidationError",
    "PvView",
    "SourceDetail",
    "create_pv",
    "get_pv",
    "list_event_pvs",
    "list_pvs",
    "update_pv",
]
