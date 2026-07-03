"""token_version em usuarios (revogação de sessão)

Revision ID: b7d2e9a1c3f5
Revises: a3c9d1e7f2b4
Create Date: 2026-07-03 10:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2e9a1c3f5"
down_revision: str | None = "a3c9d1e7f2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Coluna usada para invalidar tokens JWT já emitidos (reset de senha / desativação de usuário).
    op.add_column(
        "usuarios",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("usuarios", "token_version")
