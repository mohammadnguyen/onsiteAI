"""Domain exceptions of the Potential Variation service. The API layer maps
them to status codes; services never raise HTTP errors themselves.

Own types, not the task service's: a ``TaskError`` reaching this router
would be answered as a 500.
"""

from __future__ import annotations


class PvError(Exception):
    """Base class. ``message`` is safe to return to the caller."""

    message = "Potential variation error"

    def __init__(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        super().__init__(self.message)


class PvNotFound(PvError):
    """Unknown flag, or one the caller may not view (existence hidden)."""

    message = "Potential variation not found"


class PvSourceNotFound(PvError):
    """Unknown Site Log entry, or one the caller may not read.

    Same wording as the Site Log and Task APIs on purpose: the app tells this
    deliberate 404 apart from a server without these routes by it.
    """

    message = "Site log event not found"


class PvSourceWithdrawn(PvError):
    message = "The source entry is withdrawn"


class PvSourceChanged(PvError):
    """The entry has a newer version than the one the person confirmed.

    The ``source_changed:`` prefix is the machine-readable part (the Site Log
    API's ``inline_text_reserved:`` precedent): the app reloads the entry and
    asks the person to confirm again.
    """

    message = (
        "source_changed: the entry has changed since it was opened; "
        "check it and confirm again"
    )


class PvFingerprintMismatch(PvError):
    message = "create_client_id reused with a different potential variation"


class PvForbidden(PvError):
    """The caller can view the flag but may not make this change."""

    message = "Not allowed to change this potential variation"


class PvValidationError(PvError):
    message = "Invalid potential variation"


class PvJobNotFound(PvError):
    message = "Job not found"


class PvJobCompleted(PvError):
    message = "Job is completed"
