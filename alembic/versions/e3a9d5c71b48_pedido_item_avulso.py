"""Item avulso no pedido e snapshot do que foi vendido

Duas mudanças que andam juntas.

A primeira: `pedido_itens.produto_variacao_id` passa a aceitar NULL. O balcão vende
coisa que não está no catálogo — produto que acabou, item novo que ninguém cadastrou,
um serviço — e até aqui o vendedor precisava parar a venda para cadastrar o produto
antes de conseguir lançar a linha. Item avulso não tem saldo, então não entra em
reserva nem em baixa de estoque: a regra "estoque só muda por movimentação" continua
inteira, simplesmente não há movimentação a fazer.

A segunda: `descricao` e `codigo` viram snapshot gravado no lançamento. O pedido é
documento — renomear ou recodificar um produto no catálogo estava reescrevendo o que o
cliente comprou meses atrás, porque a tela lia tudo pela FK. Para o item avulso, esse
snapshot é o único lugar onde o nome existe. `detalhe` é a observação livre da linha.

O backfill preenche os itens que já estão gravados a partir do catálogo ANTES de
`descricao` virar NOT NULL — sem ele a migration quebra em qualquer banco com pedidos.

Revision ID: e3a9d5c71b48
Revises: f2b8c1d47a09
Create Date: 2026-08-19

"""

import sqlalchemy as sa

from alembic import op

revision = "e3a9d5c71b48"
down_revision = "f2b8c1d47a09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pedido_itens", sa.Column("descricao", sa.String(length=200), nullable=True))
    op.add_column("pedido_itens", sa.Column("codigo", sa.String(length=60), nullable=True))
    op.add_column("pedido_itens", sa.Column("detalhe", sa.Text(), nullable=True))

    # Backfill: todo item existente aponta para uma variação, então o snapshot sai do
    # catálogo atual. É a melhor verdade disponível — daqui para a frente ele é gravado
    # no momento da venda e para de depender do catálogo.
    op.execute(
        """
        UPDATE pedido_itens AS pi
           SET descricao = LEFT(p.descricao, 200),
               codigo    = LEFT(p.codigo, 60)
          FROM produto_variacoes AS pv
          JOIN produtos AS p ON p.id = pv.produto_id
         WHERE pv.id = pi.produto_variacao_id
        """
    )
    # Cinto de segurança: item órfão (FK apontando para variação apagada) não pode
    # segurar o NOT NULL abaixo e derrubar o deploy.
    op.execute("UPDATE pedido_itens SET descricao = 'ITEM' WHERE descricao IS NULL")

    op.alter_column(
        "pedido_itens", "descricao", existing_type=sa.String(length=200), nullable=False
    )
    op.alter_column(
        "pedido_itens", "produto_variacao_id", existing_type=sa.Integer(), nullable=True
    )


def downgrade() -> None:
    # Itens avulsos não têm para onde voltar: sem variação, a coluna NOT NULL não os
    # aceita de volta. Some com eles antes de reapertar a FK, senão o downgrade estoura.
    op.execute("DELETE FROM pedido_itens WHERE produto_variacao_id IS NULL")
    op.alter_column(
        "pedido_itens", "produto_variacao_id", existing_type=sa.Integer(), nullable=False
    )
    op.drop_column("pedido_itens", "detalhe")
    op.drop_column("pedido_itens", "codigo")
    op.drop_column("pedido_itens", "descricao")
