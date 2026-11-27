"""potential variations: human flags on Site Log entries (DEC-PV-001)

Two new tables, purely additive. No existing table, column or type is
modified, so the previous backend image runs unchanged against this schema.

* ``potential_variations`` — one flag created by an explicit human action
  from a Site Log entry, for the builder to check. ``job_id`` is the flag's
  own job (copied from the entry at creation, never moved by a later entry
  assign or re-link). ``(tenant_id, source_site_log_event_id,
  source_revision_no)`` is the evidence reference, a composite foreign key
  to the revision's unique key ``uq_slog_revision_no``.
  ``create_client_id`` is the device idempotency key, unique per tenant and
  creator. No amount, approver, signature or approval column exists.
* ``potential_variation_audit_log`` — append-only history; the ``created``
  row is the full record of the human confirmation.

Three new enum types: ``pv_review_state``, ``pv_end_reason``, ``pv_origin``.

Rollback:

* The NORMAL rollback is the previous backend image with these tables and
  their data KEPT. The old backend ignores them, and its startup schema-head
  check only logs a mismatch. While rolled back there is no Potential
  Variation feature.
* ``downgrade()`` below drops both tables and all three types: it DELETES
  EVERY FLAG and its history. It is not a rollback step. Running it is a
  separate destructive operation that needs its own approval and a backup
  first.

Revision ID: f3b4c5d6e7a8
Revises: e1f2a3b4c5d6
Create Date: 2026-11-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "f3b4c5d6e7a8"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")

pv_review_state_enum = sa.Enum(
    "to_check", "following_up", "ended", name="pv_review_state"
)
pv_end_reason_enum = sa.Enum(
    "already_in_contract",
    "client_not_proceeding",
    "handled_outside_forey",
    "flagged_in_error",
    "other",
    name="pv_end_reason",
)
pv_origin_enum = sa.Enum("manual", name="pv_origin")


def upgrade() -> None:
    op.create_table(
        "potential_variations",
        sa.Column("potential_variation_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=TENANT_DEFAULT,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.job_id"),
            nullable=True,
        ),
        sa.Column(
            "source_site_log_event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("site_log_events.site_log_event_id"),
            nullable=False,
        ),
        sa.Column("source_revision_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column(
            "review_state",
            pv_review_state_enum,
            nullable=False,
            server_default="to_check",
        ),
        sa.Column("ended_reason", pv_end_reason_enum, nullable=True),
        sa.Column("decision_note", sa.String(500), nullable=True),
        sa.Column(
            "decided_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "origin",
            pv_origin_enum,
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("create_client_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "created_by_user_id",
            "create_client_id",
            name="uq_pv_create_client",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_site_log_event_id", "source_revision_no"],
            [
                "site_log_event_revisions.tenant_id",
                "site_log_event_revisions.site_log_event_id",
                "site_log_event_revisions.revision_no",
            ],
            name="fk_pv_source_revision",
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) >= 1", name="ck_pv_title_not_blank"
        ),
        sa.CheckConstraint(
            "source_revision_no >= 1", name="ck_pv_source_revision_no_ge_1"
        ),
        sa.CheckConstraint(
            "(review_state = 'ended') = (ended_reason IS NOT NULL)",
            name="ck_pv_ended_has_reason",
        ),
        sa.CheckConstraint(
            "ended_reason IS DISTINCT FROM 'other' "
            "OR char_length(btrim(coalesce(decision_note, ''))) >= 1",
            name="ck_pv_other_has_note",
        ),
        sa.CheckConstraint(
            "decision_note IS NULL OR char_length(btrim(decision_note)) >= 1",
            name="ck_pv_note_not_blank",
        ),
        sa.CheckConstraint(
            "(decided_by_user_id IS NULL) = (decided_at IS NULL)",
            name="ck_pv_decided_pair",
        ),
    )
    op.create_index(
        "ix_pv_job_state", "potential_variations", ["job_id", "review_state"]
    )
    op.create_index(
        "ix_pv_state_created",
        "potential_variations",
        ["review_state", "created_at"],
    )
    op.create_index(
        "ix_pv_source_event", "potential_variations", ["source_site_log_event_id"]
    )
    op.create_index(
        "ix_pv_creator_created",
        "potential_variations",
        ["created_by_user_id", "created_at"],
    )

    op.create_table(
        "potential_variation_audit_log",
        sa.Column("audit_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=TENANT_DEFAULT,
        ),
        sa.Column(
            "potential_variation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("potential_variations.potential_variation_id"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("changed_fields", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_pv_audit_pv_created",
        "potential_variation_audit_log",
        ["potential_variation_id", "created_at"],
    )


def downgrade() -> None:
    # DESTRUCTIVE: deletes every flag and its history. Not a rollback step —
    # see the module docstring.
    op.drop_index("ix_pv_audit_pv_created", table_name="potential_variation_audit_log")
    op.drop_table("potential_variation_audit_log")
    op.drop_index("ix_pv_creator_created", table_name="potential_variations")
    op.drop_index("ix_pv_source_event", table_name="potential_variations")
    op.drop_index("ix_pv_state_created", table_name="potential_variations")
    op.drop_index("ix_pv_job_state", table_name="potential_variations")
    op.drop_table("potential_variations")
    # Native enum types outlive their tables unless dropped explicitly; a
    # leftover type would make the next upgrade fail.
    pv_origin_enum.drop(op.get_bind(), checkfirst=True)
    pv_end_reason_enum.drop(op.get_bind(), checkfirst=True)
    pv_review_state_enum.drop(op.get_bind(), checkfirst=True)
