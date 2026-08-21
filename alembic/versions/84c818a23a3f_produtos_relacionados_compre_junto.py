"""Compre Junto: produtos relacionados

Quem leva a garrafa quase sempre leva a alça, e quem vende sabe disso — mas a sugestão
morria com o vendedor que estava no balcão naquele dia. Aqui ela vira cadastro: até 8
produtos por item, na ordem em que devem aparecer.

Sem loja online, o lugar onde a sugestão se paga é o pedido: ao escolher um produto, a
faixa "Compre Junto" aparece logo abaixo, e um clique lança o acessório. O fragmento
usa o MESMO contrato `data-*` da busca de item, então o `selecionar($el)` que já existe
nas duas telas funciona sem uma linha de JS nova.

A chave primária composta (produto, relacionado) impede duplicata no banco, e não só no
formulário. A relação é de MÃO ÚNICA: capa é acessório de celular, o contrário não.

Revision ID: 84c818a23a3f
Revises: 77a8d42228ef
Create Date: 2026-08-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "84c818a23a3f"
down_revision: str | None = "77a8d42228ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "produto_relacionados",
        sa.Column("produto_id", sa.Integer(), nullable=False),
        sa.Column("relacionado_id", sa.Integer(), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["produto_id"], ["produtos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["relacionado_id"], ["produtos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("produto_id", "relacionado_id"),
    )


def downgrade() -> None:
    op.drop_table("produto_relacionados")
