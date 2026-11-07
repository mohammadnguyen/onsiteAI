"""Potential Variations flagged from Site Log entries (DEC-PV-001).

Two tables, both additive:

* ``potential_variations`` — one flag: a person marked an existing Site Log
  entry as a possible scope or commercial change for the builder to check.
  Truth written by explicit human action (DEC-TRUTH-001); no AI path writes
  here (DEC-AI-BOUNDARY-001). The entry itself is never modified: the flag
  carries its own copy of what it needs (title, job) and a reference to the
  exact entry revision the person confirmed when flagging.
* ``potential_variation_audit_log`` — append-only history. The ``created``
  row is the complete record of the human confirmation (DEC-EVIDENCE-001):
  evidence ref (entry + revision), the server-derived proposed title,
  whether it was edited, the final values, who and when. Later rows record
  each builder decision and each job change.

Boundary notes:

* A flag is not client approval, a price, entitlement to charge or
  authority to start work. There is no amount, approver, signature or
  approval column, by design; tests pin the response key sets.
* ``review_state`` / ``ended_reason`` / ``decision_note`` form ONE decision,
  made only by the builder (admin in v1). The database keeps the three
  consistent (an ended flag always has a reason; "other" always has a note).
* The evidence ref is a composite foreign key to the revision's unique key
  ``(tenant_id, site_log_event_id, revision_no)``, so the pin can never name
  a revision that does not exist.
* ``job_id`` is the FLAG's job, copied from the entry at creation and never
  moved by a later assign or re-link of the entry; changing it is an
  explicit, audited action.
* The audit holds titles and the builder's note (no endpoint returns it in
  v1). It must never hold the entry's body text:
  :func:`validate_pv_audit_detail` is the writer-side guard.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SqlaEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Single-tenant default shared with the Site Log and Task tables.
_TENANT_DEFAULT = text("'00000000-0000-0000-0000-000000000001'::uuid")

TITLE_MAX_CHARS = 200
NOTE_MAX_CHARS = 500


class PvReviewState(str, enum.Enum):
    """The three review states of DEC-PV-001. Any state may move to any
    other; none of them means approved, priced or authorised."""

    to_check = "to_check"
    following_up = "following_up"
    ended = "ended"


class PvEndReason(str, enum.Enum):
    """Why follow-up ended. Kept on the row while it stays ended; every
    earlier value stays in the audit."""

    already_in_contract = "already_in_contract"
    client_not_proceeding = "client_not_proceeding"
    handled_outside_forey = "handled_outside_forey"
    flagged_in_error = "flagged_in_error"
    other = "other"


class PvOrigin(str, enum.Enum):
    """How the flag came to exist. v1 has one way: a person made it."""

    manual = "manual"


class PvAuditAction(str, enum.Enum):
    """Application-validated audit vocabulary (String column, like the
    task and Site Log audit precedents)."""

    created = "created"
    decided = "decided"
    job_set = "job_set"


# Keys that would carry the source entry's text. Titles and the builder's
# note are allowed here on purpose; the entry body never is.
FORBIDDEN_PV_AUDIT_KEYS: frozenset[str] = frozenset(
    {
        "body_text",
        "body",
        "raw",
        "raw_text",
        "transcript",
        "payload",
        "bytes",
        "content",
        "snapshot",
    }
)


def validate_pv_audit_detail(changed_fields: dict) -> dict:
    """Reject keys that would put the entry's body into the flag's audit.

    Raises ``ValueError`` on a forbidden key at any nesting depth.
    """

    def _walk(obj: object, path: str) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if str(key).lower() in FORBIDDEN_PV_AUDIT_KEYS:
                    raise ValueError(
                        f"potential variation audit must not carry entry content: "
                        f"forbidden key {key!r} at {path or '<root>'}"
                    )
                _walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(obj, list):
            for i, value in enumerate(obj):
                _walk(value, f"{path}[{i}]")

    _walk(changed_fields, "")
    return changed_fields


class PotentialVariation(Base, TimestampMixin):
    """One flag made from a Site Log entry, for the builder to check."""

    __tablename__ = "potential_variations"

    potential_variation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=_TENANT_DEFAULT
    )

    # The flag's own job: copied from the entry at creation (it may be
    # NULL), then changed only by an explicit, audited action. NO ACTION,
    # like every Site Log FK: a referenced Job cannot be hard-deleted.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.job_id"), nullable=True
    )

    # Evidence ref: the entry and the exact revision the person confirmed.
    # The composite FK in __table_args__ ties the pair to a real revision.
    source_site_log_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("site_log_events.site_log_event_id"),
        nullable=False,
    )
    source_revision_no: Mapped[int] = mapped_column(Integer, nullable=False)

    # Fixed after save: confirmed (or edited) by the person who flagged.
    title: Mapped[str] = mapped_column(String(TITLE_MAX_CHARS), nullable=False)

    review_state: Mapped[PvReviewState] = mapped_column(
        SqlaEnum(
            PvReviewState,
            name="pv_review_state",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=PvReviewState.to_check,
        server_default=PvReviewState.to_check.value,
    )

    ended_reason: Mapped[PvEndReason | None] = mapped_column(
        SqlaEnum(
            PvEndReason,
            name="pv_end_reason",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )

    # The builder's note on the latest decision. Builder-only to read.
    decision_note: Mapped[str | None] = mapped_column(
        String(NOTE_MAX_CHARS), nullable=True
    )

    # Who made the latest decision, and when (both NULL until the first).
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    origin: Mapped[PvOrigin] = mapped_column(
        SqlaEnum(
            PvOrigin,
            name="pv_origin",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=PvOrigin.manual,
        server_default=PvOrigin.manual.value,
    )

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )

    # Device-generated idempotency key: a replay of the same create returns
    # the existing flag instead of making a second one.
    create_client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "created_by_user_id",
            "create_client_id",
            name="uq_pv_create_client",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_site_log_event_id", "source_revision_no"],
            [
                "site_log_event_revisions.tenant_id",
                "site_log_event_revisions.site_log_event_id",
                "site_log_event_revisions.revision_no",
            ],
            name="fk_pv_source_revision",
        ),
        CheckConstraint(
            "char_length(btrim(title)) >= 1", name="ck_pv_title_not_blank"
        ),
        CheckConstraint(
            "source_revision_no >= 1", name="ck_pv_source_revision_no_ge_1"
        ),
        # Ended if and only if a reason is kept.
        CheckConstraint(
            "(review_state = 'ended') = (ended_reason IS NOT NULL)",
            name="ck_pv_ended_has_reason",
        ),
        # "Other" is only meaningful with a note.
        CheckConstraint(
            "ended_reason IS DISTINCT FROM 'other' "
            "OR char_length(btrim(coalesce(decision_note, ''))) >= 1",
            name="ck_pv_other_has_note",
        ),
        CheckConstraint(
            "decision_note IS NULL OR char_length(btrim(decision_note)) >= 1",
            name="ck_pv_note_not_blank",
        ),
        CheckConstraint(
            "(decided_by_user_id IS NULL) = (decided_at IS NULL)",
            name="ck_pv_decided_pair",
        ),
        Index("ix_pv_job_state", "job_id", "review_state"),
        Index("ix_pv_state_created", "review_state", "created_at"),
        Index("ix_pv_source_event", "source_site_log_event_id"),
        Index("ix_pv_creator_created", "created_by_user_id", "created_at"),
    )


class PotentialVariationAuditLog(Base):
    """Append-only history of a flag. Never updated, never deleted."""

    __tablename__ = "potential_variation_audit_log"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=_TENANT_DEFAULT
    )

    potential_variation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("potential_variations.potential_variation_id"),
        nullable=False,
    )

    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )

    # Application-validated against PvAuditAction.
    action: Mapped[str] = mapped_column(String(32), nullable=False)

    changed_fields: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_pv_audit_pv_created", "potential_variation_id", "created_at"),
    )
