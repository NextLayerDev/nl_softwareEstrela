from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.produto import Produto


class ProdutoRepository:
    def get(self, db: Session, produto_id: int) -> Produto | None:
        return db.get(Produto, produto_id)

    def get_by_codigo(self, db: Session, codigo: str) -> Produto | None:
        return db.scalar(select(Produto).where(Produto.codigo == codigo.strip()))

    def listar(
        self, db: Session, incluir_inativos: bool = False, limit: int = 100
    ) -> list[Produto]:
        stmt = (
            select(Produto)
            .options(selectinload(Produto.variacoes), selectinload(Produto.codigos_alt))
            .order_by(Produto.descricao)
            .limit(limit)
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


produto_repo = ProdutoRepository()
