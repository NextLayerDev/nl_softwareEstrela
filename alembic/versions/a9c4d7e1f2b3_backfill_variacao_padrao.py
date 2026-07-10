"""backfill de variação padrão (cor vazia) em produtos sem variação

Revision ID: a9c4d7e1f2b3
Revises: 7b1cc57111e9
Create Date: 2026-07-10

Todo produto precisa de ao menos uma ProdutoVariacao, pois é nela que vivem a
imagem e o saldo de estoque. Produtos antigos cadastrados sem nenhuma variação
ficavam sem foto e sem saldo. Esta migration cria uma variação padrão (cor="")
para cada produto ativo que ainda não tem nenhuma.

Não muda o schema — apenas dados. Idempotente via WHERE NOT EXISTS.
"""

from __future__ import annotations

from alembic import op

revision: str = "a9c4d7e1f2b3"
down_revision: str | None = "7b1cc57111e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO produto_variacoes
            (produto_id, cor, estoque_modo, estoque_fisico, estoque_reservado,
             estoque_minimo, ativo, imagem_url)
        SELECT p.id, '', 'APROXIMADO'::estoque_modo, 0, 0, 0, true, NULL
        FROM produtos p
        WHERE p.ativo
          AND NOT EXISTS (
              SELECT 1 FROM produto_variacoes pv WHERE pv.produto_id = p.id
          )
        """
    )


def downgrade() -> None:
    # Remove apenas as variações padrão criadas por esta migration (cor='' sem
    # imagem, sem saldo e sem histórico). Seguro porque o service também só
    # hard-deleta variações limpas; uma variação com histórico/saldo é inativada,
    # nunca removida por aqui.
    op.execute(
        """
        DELETE FROM produto_variacoes pv
        WHERE pv.cor = ''
          AND pv.imagem_url IS NULL
          AND pv.estoque_fisico = 0
          AND pv.estoque_reservado = 0
          AND NOT EXISTS (
              SELECT 1 FROM movimentacoes_estoque m WHERE m.produto_variacao_id = pv.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM pedido_itens pi WHERE pi.produto_variacao_id = pv.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM inventario_itens ii WHERE ii.produto_variacao_id = pv.id
          )
        """
    )
