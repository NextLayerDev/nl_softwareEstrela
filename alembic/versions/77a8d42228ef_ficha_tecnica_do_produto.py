"""Ficha técnica do produto

Uma lista ordenada de rótulo/valor — "Altura: 50 cm", "Material: alumínio". O balcão
responde isso o tempo todo no telefone, e hoje a informação mora na cabeça de quem
vende ou na observação do produto, em texto corrido que ninguém consegue procurar.

Aparece no cadastro e no painel de preço do pedido, dentro de um `<details>`: é ali que
ela se paga, respondendo "qual a altura?" sem o vendedor sair do pedido que está
montando.

O índice em `ordem` é NÃO único de propósito — ver o comentário no modelo.

Revision ID: 77a8d42228ef
Revises: 34445cef2991
Create Date: 2026-08-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "77a8d42228ef"
down_revision: str | None = "34445cef2991"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "produto_especificacoes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("produto_id", sa.Integer(), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("rotulo", sa.String(length=40), nullable=False),
        sa.Column("valor", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(["produto_id"], ["produtos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_produto_especificacoes_produto_id"),
        "produto_especificacoes",
        ["produto_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_produto_especificacoes_produto_id"), table_name="produto_especificacoes")
    op.drop_table("produto_especificacoes")
