from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.enums import EstoqueModo, RotuloAprox
from app.models.inventario import Inventario
from app.models.movimentacao import MovimentacaoEstoque
from app.models.produto import Produto, ProdutoCodigoAlt, ProdutoVariacao


class EstoqueRepository:
    """Queries de leitura de estoque (variações, busca, localização)."""

    def get_variacao(self, db: Session, variacao_id: int) -> ProdutoVariacao | None:
        return db.scalar(
            select(ProdutoVariacao)
            .options(joinedload(ProdutoVariacao.produto))
            .where(ProdutoVariacao.id == variacao_id)
        )

    def listar_variacoes_ativas(self, db: Session) -> list[ProdutoVariacao]:
        stmt = (
            select(ProdutoVariacao)
            .options(joinedload(ProdutoVariacao.produto))
            .where(ProdutoVariacao.ativo.is_(True))
            .order_by(ProdutoVariacao.produto_id, ProdutoVariacao.cor)
        )
        return list(db.scalars(stmt))

    def busca_localizacao(self, db: Session, termo: str, limit: int = 15) -> list[ProdutoVariacao]:
        """Busca para o tablet: casa código, código alternativo, descrição (pg_trgm),
        localização e cor. Retorna variações com o produto carregado."""
        termo = (termo or "").strip()
        if not termo:
            return []

        sub_alt = select(ProdutoCodigoAlt.produto_id).where(
            ProdutoCodigoAlt.codigo_alt.ilike(f"{termo}%")
        )
        stmt = (
            select(ProdutoVariacao)
            .join(Produto, ProdutoVariacao.produto_id == Produto.id)
            .options(joinedload(ProdutoVariacao.produto))
            .where(
                ProdutoVariacao.ativo.is_(True),
                or_(
                    Produto.codigo.ilike(f"{termo}%"),
                    Produto.descricao.op("%")(termo),
                    Produto.descricao.ilike(f"%{termo}%"),
                    Produto.localizacao.ilike(f"%{termo}%"),
                    ProdutoVariacao.cor.ilike(f"%{termo}%"),
                    Produto.id.in_(sub_alt),
                ),
            )
            .order_by(Produto.descricao, ProdutoVariacao.cor)
            .limit(limit)
        )
        return list(db.scalars(stmt))

    def listar_alertas(self, db: Session, limit: int = 200) -> list[ProdutoVariacao]:
        """Variações em atenção: EXATO <= mínimo OU aproximado POUCO/ACABOU."""
        stmt = (
            select(ProdutoVariacao)
            .options(joinedload(ProdutoVariacao.produto))
            .where(
                ProdutoVariacao.ativo.is_(True),
                or_(
                    (ProdutoVariacao.estoque_modo == EstoqueModo.EXATO)
                    & (ProdutoVariacao.estoque_fisico <= ProdutoVariacao.estoque_minimo),
                    ProdutoVariacao.rotulo_aprox.in_([RotuloAprox.POUCO, RotuloAprox.ACABOU]),
                ),
            )
            .limit(limit)
        )
        return list(db.scalars(stmt))


class MovimentacaoRepository:
    def historico(
        self, db: Session, variacao_id: int, limit: int = 100
    ) -> list[MovimentacaoEstoque]:
        stmt = (
            select(MovimentacaoEstoque)
            .where(MovimentacaoEstoque.produto_variacao_id == variacao_id)
            .order_by(MovimentacaoEstoque.criado_em.desc(), MovimentacaoEstoque.id.desc())
            .limit(limit)
        )
        return list(db.scalars(stmt))


class InventarioRepository:
    def get(self, db: Session, inventario_id: int) -> Inventario | None:
        return db.scalar(
            select(Inventario)
            .options(selectinload(Inventario.itens))
            .where(Inventario.id == inventario_id)
        )

    def listar(self, db: Session, limit: int = 50) -> list[Inventario]:
        stmt = select(Inventario).order_by(Inventario.id.desc()).limit(limit)
        return list(db.scalars(stmt))


estoque_repo = EstoqueRepository()
movimentacao_repo = MovimentacaoRepository()
inventario_repo = InventarioRepository()
