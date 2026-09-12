"""sync_outbox_erro_atualizado_em

Revision ID: c3f8a19d5e2b
Revises: 417902adb707
Create Date: 2026-08-03 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f8a19d5e2b"
down_revision: str | None = "417902adb707"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sync_outbox", sa.Column("erro", sa.Text(), nullable=True))
    op.add_column(
        "sync_outbox",
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sync_outbox", "atualizado_em")
    op.drop_column("sync_outbox", "erro")
