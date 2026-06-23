from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.conta_receber import ContaReceber
from app.models.enums import StatusConta
from app.models.pedido import Pedido


class ContaReceberRepository:
    def get(self, db: Session, conta_id: int) -> ContaReceber | None:
        return db.get(ContaReceber, conta_id)

    def listar(
        self,
        db: Session,
        status: StatusConta | None = None,
        cliente_id: int | None = None,
        venc_de: date | None = None,
        venc_ate: date | None = None,
        limit: int = 500,
    ) -> list[ContaReceber]:
        stmt = (
            select(ContaReceber)
            .options(joinedload(ContaReceber.pedido).joinedload(Pedido.cliente))
            .order_by(ContaReceber.vencimento, ContaReceber.id)
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(ContaReceber.status == status)
        if cliente_id is not None:
            stmt = stmt.join(Pedido, ContaReceber.pedido_id == Pedido.id).where(
                Pedido.cliente_id == cliente_id
            )
        if venc_de is not None:
            stmt = stmt.where(ContaReceber.vencimento >= venc_de)
        if venc_ate is not None:
            stmt = stmt.where(ContaReceber.vencimento <= venc_ate)
        return list(db.scalars(stmt).unique())

    def pendentes_vencidas(self, db: Session, hoje: date) -> list[ContaReceber]:
        stmt = select(ContaReceber).where(
            ContaReceber.status == StatusConta.PENDENTE,
            ContaReceber.vencimento < hoje,
        )
        return list(db.scalars(stmt))

    def recebidas_no_dia(self, db: Session, dia: date) -> list[ContaReceber]:
        """Contas baixadas (PAGO) cuja baixa caiu no dia informado."""
        stmt = (
            select(ContaReceber)
            .options(joinedload(ContaReceber.pedido).joinedload(Pedido.cliente))
            .where(ContaReceber.status == StatusConta.PAGO)
            .order_by(ContaReceber.baixado_em)
        )
        contas = list(db.scalars(stmt).unique())
        return [c for c in contas if c.baixado_em and c.baixado_em.date() == dia]

    def add(self, db: Session, conta: ContaReceber) -> ContaReceber:
        db.add(conta)
        db.flush()
        return conta


conta_repo = ContaReceberRepository()
