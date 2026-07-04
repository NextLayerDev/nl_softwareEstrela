"""cliente: dados fiscais para NF-e (eNotas)

Revision ID: d2f6b8c4a1e7
Revises: c1e4f8a2d6b9
Create Date: 2026-07-04 10:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2f6b8c4a1e7"
down_revision: str | None = "c1e4f8a2d6b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUNAS = [
    sa.Column("email", sa.String(length=180), nullable=True),
    sa.Column("inscricao_estadual", sa.String(length=20), nullable=True),
    sa.Column(
        "contribuinte_icms", sa.Boolean(), nullable=False, server_default=sa.false()
    ),
    sa.Column("fisc_cep", sa.String(length=9), nullable=True),
    sa.Column("fisc_logradouro", sa.String(length=160), nullable=True),
    sa.Column("fisc_numero", sa.String(length=20), nullable=True),
    sa.Column("fisc_complemento", sa.String(length=80), nullable=True),
    sa.Column("fisc_bairro", sa.String(length=80), nullable=True),
    sa.Column("fisc_cidade", sa.String(length=80), nullable=True),
    sa.Column("fisc_uf", sa.String(length=2), nullable=True),
]


def upgrade() -> None:
    for coluna in _COLUNAS:
        op.add_column("clientes", coluna)


def downgrade() -> None:
    for coluna in reversed(_COLUNAS):
        op.drop_column("clientes", coluna.name)
