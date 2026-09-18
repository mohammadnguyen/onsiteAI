"""Site Log capture service.


Sole writer of the five WP A tables (``site_log_events``,
``site_log_event_revisions``, ``site_log_event_attachments``,
``site_log_event_audit_log``, ``capture_eligibility_transitions``) and
the ONLY code that creates, binds or changes the status of Evidence rows
attached to an event. Thinness tests pin those write sites.

Governing rules baked in:

* **Global lock order** for every multi-row write: event → manifest rows
  (``attachment_id`` asc) → Evidence rows (``evidence_id`` asc). No lock
  is ever held across byte streaming.
* **Evidence governed before bytes** (2.1 §2 row 1): upload Txn A creates
  the Evidence row, binds it (the only NULL→value write of
  ``evidence_id``), sets ``pending`` and increments
  ``upload_attempt_no`` — then commits — before a single byte is read.
* **Attempt-versioned completion** (2.1 §1): Txn B completes only when the
  locked manifest row is still ``pending`` AT the acquired attempt number;
  an obsolete attempt can never complete after an admin reset + retry.
* **No time-based self-heal**: a ``pending`` row answers 409 until an
  admin reset (≥ 15 minutes, reason, audited) moves it to ``failed``.
* **Txn B internal retry**: three total attempts on a fresh session each,
  100 ms / 300 ms backoff, only for connection invalidation or SQLSTATE
  40001 / 40P01 / 55P03. Never for IntegrityError, CAS misses or
  validation.
* **Content-free audit**: every audit write passes
  :func:`validate_audit_detail`; nothing in this module logs body text,
  filenames or payload bytes.
* **Tenant**: copied from the locked parent row (or the single-tenant
  constant at event creation), never from client input; every query
  filters on it.

* **Transaction ownership** (founder ruling B): every locked section runs
  in :func:`_lock_scope` — a SAVEPOINT. A denial, conflict, not-found or
  no-write replay rolls back only that SAVEPOINT (locks release, the
  caller's outer transaction and unrelated pending state are untouched);
  only a positive write path commits.

Services raise domain exceptions only — the API layer maps them.

**Binding for this module (founder ruling D, A2a):** this file is
accepted at its current size only because transaction ownership is
concentrated here and heavily tested. No A2b behaviour may be added to
it. Before A2b implementation or any staging deployment, a separate
behaviour-preserving A2a.1 structural-refactor checkpoint must split it
into cohesive modules while retaining ONE public mutation boundary and
the global lock order.

**Package layout (A2a.1).** The rules above did not change; the code that
carries them now lives in cohesive modules:

* :mod:`errors` — the exception types. One definition site each.
* :mod:`core` — constants, the lock helpers, the savepoint scope, the audit
  builders. The leaf: everything imports it, it imports nothing back.
* :mod:`inline` — inline-text identity and revision 1's expectation, shared
  by declare and upload without depending on either.
* :mod:`views` — access resolution, the view model, the read entry points.
* :mod:`upload` — the two-phase upload, deliberately one module.
* :mod:`admin` — reset, finalize, job attribution.
* :mod:`capture` — declare, and the server-owned inline upload.

Dependencies run one way: ``errors`` → ``core`` → ``inline`` → ``views`` →
``upload``/``admin`` → ``capture``. There are no cycles.

This module re-exports the service's public surface, so callers keep using
``from app.services import site_log as svc``. It is a surface, not a
behaviour: patching a name here does NOT change what a submodule resolves
internally, so fault injection must target the module that performs the
lookup.
"""

from __future__ import annotations

from .admin import (
    assign_job,
    finalize_capture,
    relink_job,
    reset_attachment,
)
from .capture import DeclareResult, declare_capture
from .core import (
    INLINE_TEXT_MIME,
    INLINE_TEXT_NAMESPACE,
    RESET_MIN_AGE,
    TXN_B_ATTEMPTS,
    TXN_B_BACKOFF_SECONDS,
    SessionFactory,
)
from .errors import (
    SiteLogAlreadyAssigned,
    SiteLogAttemptSuperseded,
    SiteLogContentMismatch,
    SiteLogError,
    SiteLogFingerprintMismatch,
    SiteLogForbidden,
    SiteLogInlineReserved,
    SiteLogJobCompleted,
    SiteLogJobNotFound,
    SiteLogMediaMismatch,
    SiteLogNotFound,
    SiteLogNothingToReset,
    SiteLogNotReady,
    SiteLogReasonRequired,
    SiteLogResetNotEligible,
    SiteLogSameJob,
    SiteLogTooLarge,
    SiteLogUploadInProgress,
    SiteLogValidationError,
)
from .inline import inline_attachment_id
from .upload import (
    UploadResult,
    acquire_attachment,
    complete_attachment,
    upload_attachment,
)
from .views import EventView, get_event, list_job_events, list_unassigned

__all__ = [
    "INLINE_TEXT_MIME",
    "INLINE_TEXT_NAMESPACE",
    "RESET_MIN_AGE",
    "TXN_B_ATTEMPTS",
    "TXN_B_BACKOFF_SECONDS",
    "DeclareResult",
    "EventView",
    "SessionFactory",
    "SiteLogAlreadyAssigned",
    "SiteLogAttemptSuperseded",
    "SiteLogContentMismatch",
    "SiteLogError",
    "SiteLogFingerprintMismatch",
    "SiteLogForbidden",
    "SiteLogInlineReserved",
    "SiteLogJobCompleted",
    "SiteLogJobNotFound",
    "SiteLogMediaMismatch",
    "SiteLogNotFound",
    "SiteLogNotReady",
    "SiteLogNothingToReset",
    "SiteLogReasonRequired",
    "SiteLogResetNotEligible",
    "SiteLogSameJob",
    "SiteLogTooLarge",
    "SiteLogUploadInProgress",
    "SiteLogValidationError",
    "UploadResult",
    "acquire_attachment",
    "assign_job",
    "complete_attachment",
    "declare_capture",
    "finalize_capture",
    "get_event",
    "inline_attachment_id",
    "list_job_events",
    "list_unassigned",
    "relink_job",
    "reset_attachment",
    "upload_attachment",
]
