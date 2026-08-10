"""Código do cliente na planilha da loja

A planilha de pedidos do Excel traz, no topo de cada bloco, um número que identifica
o cliente daquele pedido. `clientes.codigo` guarda esse número para que a colagem em
lote amarre cada bloco ao cadastro certo, em vez de criar tudo como CONSUMIDOR.

Indexado mas NÃO único: se o mesmo código aparecer em dois cadastros, a busca devolve
"ambíguo" e o pedido nasce sem cliente — melhor do que carimbar o cliente errado numa
venda de verdade. Nulo é o estado de todo cadastro que ainda não foi mapeado.

Revision ID: a7c3f0d81e64
Revises: c81f4a3e9b52
Create Date: 2026-08-10

"""

import sqlalchemy as sa

from alembic import op

revision = "a7c3f0d81e64"
down_revision = "c81f4a3e9b52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clientes", sa.Column("codigo", sa.String(length=40), nullable=True))
    op.create_index("ix_clientes_codigo", "clientes", ["codigo"])


def downgrade() -> None:
    op.drop_index("ix_clientes_codigo", table_name="clientes")
    op.drop_column("clientes", "codigo")
