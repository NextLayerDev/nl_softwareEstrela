from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.conta_receber import ContaReceber
from app.models.enums import OrigemPedido, StatusPedido
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
        origem: OrigemPedido | None = None,
        limit: int = 100,
    ) -> list[Pedido]:
        stmt = (
            select(Pedido)
            .options(
                joinedload(Pedido.cliente),
                joinedload(Pedido.vendedor),
                # A lista mostra a contagem de itens. Sem o selectinload seria um SELECT
                # por linha — 100 pedidos viravam 101 consultas.
                selectinload(Pedido.itens),
            )
            .order_by(Pedido.criado_em.desc())
            .limit(limit)
        )
        if vendedor_id is not None:
            stmt = stmt.where(Pedido.vendedor_id == vendedor_id)
        if status is not None:
            stmt = stmt.where(Pedido.status == status)
        if origem is not None:
            stmt = stmt.where(Pedido.origem == origem)
        return list(db.scalars(stmt).unique())

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

    def numero_em_uso(self, db: Session, numero: int, exceto_id: int) -> bool:
        stmt = select(Pedido.id).where(Pedido.numero == numero, Pedido.id != exceto_id)
        return db.scalar(stmt) is not None

    def acompanhar_sequencia(self, db: Session, numero: int) -> None:
        """Número digitado à mão acima da sequence: ela pula para ele, senão a próxima
        confirmação tentaria o mesmo número e bateria no unique."""
        db.execute(
            text(
                "SELECT setval('pedido_numero_seq', GREATEST(:n, "
                "(SELECT last_value FROM pedido_numero_seq)))"
            ),
            {"n": numero},
        )

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
