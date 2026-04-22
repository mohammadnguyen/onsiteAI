"""add review queue and audit

Revision ID: 9e7c97b85cfd
Revises: 82593095cdf2
Create Date: 2026-04-22 19:39:17.371684

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9e7c97b85cfd"
down_revision: Union[str, Sequence[str], None] = "82593095cdf2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create the two new Postgres ENUM types explicitly up front so the
    # ARRAY column on ``expense_review_queue.review_reasons`` can reference
    # a named type (autogenerate would otherwise try to recreate the
    # enum inline inside the ARRAY which fails on Postgres).
    review_queue_status = postgresql.ENUM(
        "open",
        "resolved",
        "rejected",
        name="review_queue_status",
        create_type=False,
    )
    review_queue_status.create(op.get_bind(), checkfirst=False)

    review_reason_code = postgresql.ENUM(
        "job_uncertain",
        "supplier_uncertain",
        "category_uncertain",
        "amount_uncertain",
        "duplicate_suspected",
        "unsupported_currency",
        name="review_reason_code",
        create_type=False,
    )
    review_reason_code.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "expense_audit_log",
        sa.Column("audit_id", sa.UUID(), nullable=False),
        sa.Column("expense_id", sa.UUID(), nullable=False),
        sa.Column("edited_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "edited_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "changed_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["edited_by_user_id"],
            ["users.user_id"],
        ),
        sa.ForeignKeyConstraint(
            ["expense_id"],
            ["expenses.expense_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_table(
        "expense_review_queue",
        sa.Column("review_id", sa.UUID(), nullable=False),
        sa.Column("expense_id", sa.UUID(), nullable=False),
        sa.Column(
            "review_reasons",
            postgresql.ARRAY(
                postgresql.ENUM(
                    "job_uncertain",
                    "supplier_uncertain",
                    "category_uncertain",
                    "amount_uncertain",
                    "duplicate_suspected",
                    "unsupported_currency",
                    name="review_reason_code",
                    create_type=False,
                )
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "open",
                "resolved",
                "rejected",
                name="review_queue_status",
                create_type=False,
            ),
            server_default="open",
            nullable=False,
        ),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_by_user_id", sa.UUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            # Note: ``array_length(arr, 1)`` returns NULL for empty
            # arrays (not 0), so ``> 0`` would let ``{}`` through.
            # ``cardinality()`` correctly returns 0 for empty arrays.
            "cardinality(review_reasons) > 0",
            name="ck_expense_review_queue_reasons_non_empty",
        ),
        sa.ForeignKeyConstraint(
            ["expense_id"],
            ["expenses.expense_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.user_id"],
        ),
        sa.PrimaryKeyConstraint("review_id"),
        sa.UniqueConstraint(
            "expense_id", name="uq_expense_review_queue_expense_id"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop tables first (their CHECK / UNIQUE / FK constraints go with
    # them automatically on Postgres), then drop the two ENUM types
    # created alongside them. Autogenerate does not emit DROP TYPE, so
    # these two statements are mandatory hand-edits for a clean
    # downgrade + upgrade round-trip.
    op.drop_table("expense_review_queue")
    op.drop_table("expense_audit_log")
    op.execute("DROP TYPE IF EXISTS review_queue_status")
    op.execute("DROP TYPE IF EXISTS review_reason_code")
