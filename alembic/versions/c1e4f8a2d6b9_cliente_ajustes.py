"""cliente: telefone2, observacao, vendedor, categoria; remove limite_credito

Revision ID: c1e4f8a2d6b9
Revises: b7d2e9a1c3f5
Create Date: 2026-07-03 16:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1e4f8a2d6b9"
down_revision: str | None = "b7d2e9a1c3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CATEGORIA = sa.Enum("muito_bom", "bom", "ruim", name="categoriacliente")


def upgrade() -> None:
    _CATEGORIA.create(op.get_bind(), checkfirst=True)
    op.add_column("clientes", sa.Column("telefone2", sa.String(length=40), nullable=True))
    op.add_column("clientes", sa.Column("vendedor", sa.String(length=120), nullable=True))
    op.add_column("clientes", sa.Column("categoria", _CATEGORIA, nullable=True))
    op.add_column("clientes", sa.Column("observacao", sa.Text(), nullable=True))
    op.drop_column("clientes", "limite_credito")


def downgrade() -> None:
    op.add_column(
        "clientes",
        sa.Column("limite_credito", sa.Numeric(precision=12, scale=2), nullable=True),
    )
    op.drop_column("clientes", "observacao")
    op.drop_column("clientes", "categoria")
    op.drop_column("clientes", "vendedor")
    op.drop_column("clientes", "telefone2")
    op.execute("DROP TYPE IF EXISTS categoriacliente")
