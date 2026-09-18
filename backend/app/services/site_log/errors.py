"""Exception types for the Site Log service.

One definition site per class: the API maps them by ``isinstance`` and its
table is walked top-down, so both class identity and subclass ordering are
load-bearing. Nothing here imports the rest of the package.
"""

from __future__ import annotations


class SiteLogError(Exception):
    """Base for every Site Log domain error."""


class SiteLogNotFound(SiteLogError):
    """Event/attachment missing, cross-tenant, or not readable — 404."""


class SiteLogJobNotFound(SiteLogError):
    pass


class SiteLogJobCompleted(SiteLogError):
    """Target Job is completed; no new capture/assignment into it — 422."""


class SiteLogValidationError(SiteLogError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SiteLogFingerprintMismatch(SiteLogError):
    """capture_client_id replayed with a different declaration — 409."""


class SiteLogUploadInProgress(SiteLogError):
    """Manifest row is pending; only an admin reset can move it — 409."""


class SiteLogAttemptSuperseded(SiteLogError):
    """Txn B for an obsolete attempt (CAS miss) — 409."""


class SiteLogMediaMismatch(SiteLogError):
    """Actual MIME class disagrees with the declared media type — 422."""


class SiteLogTooLarge(SiteLogError):
    pass


class SiteLogNotReady(SiteLogError):
    def __init__(self, states: dict[str, str]):
        super().__init__("attachments still in flight")
        self.states = states


class SiteLogResetNotEligible(SiteLogError):
    """Pending attempt younger than RESET_MIN_AGE — 409."""


class SiteLogNothingToReset(SiteLogError):
    """Manifest row is not pending — 409."""


class SiteLogForbidden(SiteLogError):
    """Admin-only action attempted by a non-admin — 403 (repo convention)."""


class SiteLogReasonRequired(SiteLogError):
    pass


class SiteLogSameJob(SiteLogError):
    pass


class SiteLogInlineReserved(SiteLogError):
    """A client tried to upload to the server-owned inline-text row.

    The inline row exists to hold revision 1's own words. Only the server
    path may supply its bytes; a client upload to it would restate what was
    captured without appending a revision, which is the correction
    mechanism that exists to make such a change visible and reasoned.
    """


class SiteLogContentMismatch(SiteLogError):
    """A stored inline object does not match revision 1's text.

    Raised after the completion CAS and before the row is marked stored, so
    the attempt fails and the Evidence row is never bound to bytes that
    disagree with the words the event says were captured.
    """


class SiteLogAlreadyAssigned(SiteLogError):
    """assign-job on an event that already has a Job — use relink — 409."""
