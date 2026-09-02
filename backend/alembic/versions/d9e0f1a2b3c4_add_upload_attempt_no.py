"""site log: upload_attempt_no on the attachment manifest (WP A A1b)

Minimum foundation for A2's attempt-versioned upload protocol, per the
approved A2 Revision 2 SCHEMA BLOCKER determination:

* ``upload_attempt_no INTEGER NOT NULL server_default 0``, with
  ``CHECK (upload_attempt_no >= 0)``.
* ``0`` means no upload attempt has been acquired. A2's upload Txn A
  will increment it under the manifest-row lock; Txn B completes only
  via CAS on ``(state = 'pending' AND upload_attempt_no = acquired)``,
  so an obsolete attempt can never complete after an admin reset and
  retry — even though the row's state has returned to ``pending``.
* A1b implements NO behaviour: no increment, no reset, no CAS. Column
  and constraint only.

Purely additive; no existing Site Log field or table is modified.

Rollback: dropping the column is structurally safe while the value is
uniformly 0 (no A2 behaviour exists to have written anything else).
After A2 ships, prefer forward migration.

Revision ID: d9e0f1a2b3c4
Revises: c7d8e9f0a1b2
Create Date: 2026-09-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "site_log_event_attachments",
        sa.Column(
            "upload_attempt_no",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "ck_slog_attachment_attempt_nonneg",
        "site_log_event_attachments",
        "upload_attempt_no >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_slog_attachment_attempt_nonneg",
        "site_log_event_attachments",
        type_="check",
    )
    op.drop_column("site_log_event_attachments", "upload_attempt_no")
