"""Tabela de preço por quantidade (faixas de atacado)

Até aqui o produto tinha DOIS preços — varejo e atacado — e um corte único. Serve para
a maior parte do catálogo, mas não para quem negocia por volume: "1 a 9 sai a R$ 10,
10 a 49 a R$ 8, 50+ a R$ 6,50" não cabe em dois campos, e o vendedor acabava digitando
o preço na mão a cada pedido — que é exatamente onde o preço errado entra.

Tabela filha e não uma coluna JSONB: o psycopg desserializa número JSON como `float`, e
`Decimal(8.0)` vira 8.000000000000000444…, que é dinheiro em ponto flutuante — proibido
pelo CLAUDE.md §5. Como tabela, a unicidade de (produto, min_qtd) ainda passa a valer
para quem escreve SQL na mão.

SEM BACKFILL, de propósito. Gerar [[1, preco_pouca], [corte, preco_muita]] para os
produtos que já têm corte seria fixar os preços de hoje num lugar de precedência MAIOR:
a partir daí, editar `preco_pouca_qtd` no formulário não mudaria mais nada, e ninguém
entenderia por quê. Tabela vazia significa "sem tabela" e é, byte a byte, o
comportamento de hoje — o upgrade é inerte em todas as linhas que já existem. Quem
quiser a tabela usa o botão "Gerar a partir de varejo/atacado" no formulário, que
preenche o editor e espera um humano confirmar.

O downgrade APAGA as tabelas de preço cadastradas. Não há para onde levá-las: os dois
campos antigos não comportam N faixas.

Revision ID: 34445cef2991
Revises: e3a9d5c71b48
Create Date: 2026-08-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "34445cef2991"
down_revision: str | None = "e3a9d5c71b48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "produto_faixas_preco",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("produto_id", sa.Integer(), nullable=False),
        sa.Column("min_qtd", sa.Integer(), nullable=False),
        sa.Column("preco", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["produto_id"], ["produtos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("produto_id", "min_qtd", name="uq_faixa_produto_min_qtd"),
    )
    op.create_index(
        op.f("ix_produto_faixas_preco_produto_id"),
        "produto_faixas_preco",
        ["produto_id"],
        unique=False,
    )


def downgrade() -> None:
    # Nenhum ENUM é criado aqui. Se alguém acrescentar um, lembre: `op.drop_table` NÃO
    # derruba o tipo — o downgrade precisa de `sa.Enum(..., name=...).drop(op.get_bind())`.
    op.drop_index(op.f("ix_produto_faixas_preco_produto_id"), table_name="produto_faixas_preco")
    op.drop_table("produto_faixas_preco")
