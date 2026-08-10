"""Pedido aceita cliente livre (nome + telefone) em vez de exigir cadastro

No balcão o cliente quase nunca está cadastrado, e parar a venda para preencher um
cadastro completo é o que fazia o vendedor abandonar o sistema. `cliente_id` passa a
ser opcional e o pedido ganha `cliente_nome`/`cliente_telefone` para o texto livre.
Quando o vendedor escolhe uma sugestão da busca (ou o telefone bate exatamente com um
cadastro), o `cliente_id` continua sendo preenchido e nada muda para o financeiro.

Revision ID: b6d9f21c47ae
Revises: d4b1e7a95c30
Create Date: 2026-08-09

"""

import sqlalchemy as sa

from alembic import op

revision = "b6d9f21c47ae"
down_revision = "d4b1e7a95c30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("pedidos", "cliente_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("pedidos", sa.Column("cliente_nome", sa.String(length=160), nullable=True))
    op.add_column("pedidos", sa.Column("cliente_telefone", sa.String(length=40), nullable=True))


def downgrade() -> None:
    """Volta a exigir cliente cadastrado.

    Pedidos de balcão (sem `cliente_id`) impediriam o NOT NULL, então eles são apagados
    antes — junto com os itens e as contas a receber que dependem deles. Por isso a
    volta só faz sentido logo depois do upgrade, antes de qualquer venda de balcão.
    """
    op.execute(
        "DELETE FROM contas_receber WHERE pedido_id IN "
        "(SELECT id FROM pedidos WHERE cliente_id IS NULL)"
    )
    op.execute(
        "DELETE FROM pedido_itens WHERE pedido_id IN "
        "(SELECT id FROM pedidos WHERE cliente_id IS NULL)"
    )
    op.execute("DELETE FROM pedidos WHERE cliente_id IS NULL")
    op.drop_column("pedidos", "cliente_telefone")
    op.drop_column("pedidos", "cliente_nome")
    op.alter_column("pedidos", "cliente_id", existing_type=sa.Integer(), nullable=False)
