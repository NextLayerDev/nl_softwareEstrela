from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import StatusConta

if TYPE_CHECKING:
    from app.models.pedido import Pedido


def _enum(py_enum, nome: str) -> SAEnum:
    return SAEnum(py_enum, name=nome, values_callable=lambda e: [m.value for m in e])


class ContaReceber(Base):
    __tablename__ = "contas_receber"

    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedidos.id"), index=True)
    parcela: Mapped[int] = mapped_column(Integer, default=1)
    valor: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    vencimento: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[StatusConta] = mapped_column(
        _enum(StatusConta, "status_conta"), default=StatusConta.PENDENTE, index=True
    )
    forma_pagamento: Mapped[str | None] = mapped_column(String(40))  # pix/boleto/dinheiro
    baixado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    baixado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))

    pedido: Mapped[Pedido] = relationship()
