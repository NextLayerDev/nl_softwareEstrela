from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.conta_receber import ContaReceber
from app.models.enums import StatusPedido
from app.models.pedido import Pedido, PedidoItem


class PedidoRepository:
    def get(self, db: Session, pedido_id: int) -> Pedido | None:
        return db.scalar(
            select(Pedido)
            .options(
                joinedload(Pedido.cliente),
                joinedload(Pedido.vendedor),
                selectinload(Pedido.itens),
            )
            .where(Pedido.id == pedido_id)
        )

    def get_completo(self, db: Session, pedido_id: int) -> Pedido | None:
        """Pedido com cliente, vendedor, itens, variações e produtos carregados."""
        from app.models.produto import ProdutoVariacao

        return db.scalar(
            select(Pedido)
            .options(
                joinedload(Pedido.cliente),
                joinedload(Pedido.vendedor),
                selectinload(Pedido.itens)
                .joinedload(PedidoItem.variacao)
                .joinedload(ProdutoVariacao.produto),
            )
            .where(Pedido.id == pedido_id)
        )

    def listar(
        self,
        db: Session,
        vendedor_id: int | None = None,
        status: StatusPedido | None = None,
        limit: int = 100,
    ) -> list[Pedido]:
        stmt = (
            select(Pedido)
            .options(joinedload(Pedido.cliente), joinedload(Pedido.vendedor))
            .order_by(Pedido.criado_em.desc())
            .limit(limit)
        )
        if vendedor_id is not None:
            stmt = stmt.where(Pedido.vendedor_id == vendedor_id)
        if status is not None:
            stmt = stmt.where(Pedido.status == status)
        return list(db.scalars(stmt))

    def fila_separacao(self, db: Session, limit: int = 100) -> list[Pedido]:
        """Pedidos confirmados / em separação, em ordem de chegada (criado_em ASC)."""
        stmt = (
            select(Pedido)
            .options(joinedload(Pedido.cliente), joinedload(Pedido.vendedor))
            .where(Pedido.status.in_([StatusPedido.CONFIRMADO, StatusPedido.SEPARACAO]))
            .order_by(Pedido.criado_em.asc())
            .limit(limit)
        )
        return list(db.scalars(stmt))

    def get_item(self, db: Session, item_id: int) -> PedidoItem | None:
        return db.get(PedidoItem, item_id)

    def proximo_numero(self, db: Session) -> int:
        """Numeração sem buracos via sequence dedicada do Postgres."""
        return int(db.scalar(select(func.nextval("pedido_numero_seq"))))

    def add(self, db: Session, pedido: Pedido) -> Pedido:
        db.add(pedido)
        db.flush()
        return pedido

    def add_item(self, db: Session, item: PedidoItem) -> PedidoItem:
        db.add(item)
        db.flush()
        return item

    def remover_item(self, db: Session, item: PedidoItem) -> None:
        db.delete(item)
        db.flush()

    def add_conta(self, db: Session, conta: ContaReceber) -> ContaReceber:
        db.add(conta)
        db.flush()
        return conta


pedido_repo = PedidoRepository()
