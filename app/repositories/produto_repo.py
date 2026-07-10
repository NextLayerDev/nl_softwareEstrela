from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.inventario import InventarioItem
from app.models.movimentacao import MovimentacaoEstoque
from app.models.pedido import PedidoItem
from app.models.produto import Produto, ProdutoVariacao


class ProdutoRepository:
    def get(self, db: Session, produto_id: int) -> Produto | None:
        return db.get(Produto, produto_id)

    def get_variacao(self, db: Session, variacao_id: int) -> ProdutoVariacao | None:
        return db.get(ProdutoVariacao, variacao_id)

    def get_by_codigo(self, db: Session, codigo: str) -> Produto | None:
        return db.scalar(select(Produto).where(Produto.codigo == codigo.strip()))

    def listar(
        self,
        db: Session,
        incluir_inativos: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Produto]:
        stmt = (
            select(Produto)
            .options(selectinload(Produto.variacoes), selectinload(Produto.codigos_alt))
            .order_by(Produto.descricao)
            .limit(limit)
            .offset(offset)
        )
        if not incluir_inativos:
            stmt = stmt.where(Produto.ativo.is_(True))
        return list(db.scalars(stmt))

    def busca_rapida(self, db: Session, termo: str, limit: int = 20) -> list[Produto]:
        """Busca por pg_trgm na descrição + match de prefixo no código."""
        termo = termo.strip()
        stmt = (
            select(Produto)
            .options(selectinload(Produto.variacoes), selectinload(Produto.codigos_alt))
            .where(
                or_(
                    Produto.descricao.op("%")(termo),
                    Produto.codigo.ilike(f"{termo}%"),
                )
            )
            .order_by(func.similarity(Produto.descricao, termo).desc())
            .limit(limit)
        )
        return list(db.scalars(stmt))

    def add(self, db: Session, produto: Produto) -> Produto:
        db.add(produto)
        db.flush()
        return produto

    def variacao_por_cor(self, db: Session, produto_id: int, cor: str) -> ProdutoVariacao | None:
        """Busca a variação ativa de um produto pelo nome da cor (idempotência ao adicionar)."""
        return db.scalar(
            select(ProdutoVariacao).where(
                ProdutoVariacao.produto_id == produto_id,
                ProdutoVariacao.cor == cor,
                ProdutoVariacao.ativo.is_(True),
            )
        )

    def variacao_tem_historico(self, db: Session, variacao_id: int) -> bool:
        """True se a variação já foi usada em movimentações, pedidos ou inventário.

        As FKs de `movimentacoes_estoque`, `pedido_itens` e `inventario_itens` para
        `produto_variacoes` são RESTRICT (sem ondelete), então um hard-delete de uma
        variação com histórico seria barrado pelo Postgres. Esta checagem permite
        dar uma mensagem amigável e, em vez de deletar, inativar a variação.
        """
        existe_mov = db.scalar(
            select(func.count())
            .select_from(MovimentacaoEstoque)
            .where(MovimentacaoEstoque.produto_variacao_id == variacao_id)
        )
        if existe_mov:
            return True
        existe_pedido = db.scalar(
            select(func.count())
            .select_from(PedidoItem)
            .where(PedidoItem.produto_variacao_id == variacao_id)
        )
        if existe_pedido:
            return True
        existe_inv = db.scalar(
            select(func.count())
            .select_from(InventarioItem)
            .where(InventarioItem.produto_variacao_id == variacao_id)
        )
        return bool(existe_inv)


produto_repo = ProdutoRepository()
