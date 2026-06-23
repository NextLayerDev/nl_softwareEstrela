from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import OrigemPedido, StatusPedido

if TYPE_CHECKING:
    from app.models.cliente import Cliente
    from app.models.produto import ProdutoVariacao
    from app.models.usuario import Usuario


def _enum(py_enum, nome: str) -> SAEnum:
    return SAEnum(py_enum, name=nome, values_callable=lambda e: [m.value for m in e])


class Pedido(Base):
    __tablename__ = "pedidos"
    __table_args__ = (
        Index("ix_pedidos_vendedor_criado", "vendedor_id", "criado_em"),
        Index("ix_pedidos_cliente_criado", "cliente_id", "criado_em"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    numero: Mapped[int | None] = mapped_column(
        Integer, unique=True, index=True
    )  # sequence ao confirmar
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), index=True)
    vendedor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    status: Mapped[StatusPedido] = mapped_column(
        _enum(StatusPedido, "status_pedido"), default=StatusPedido.RASCUNHO, index=True
    )
    desconto_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    observacao: Mapped[str | None] = mapped_column(Text)
    origem: Mapped[OrigemPedido] = mapped_column(
        _enum(OrigemPedido, "origem_pedido"), default=OrigemPedido.LOCAL
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    faturado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cliente: Mapped[Cliente] = relationship()
    vendedor: Mapped[Usuario] = relationship()
    itens: Mapped[list[PedidoItem]] = relationship(
        back_populates="pedido", cascade="all, delete-orphan"
    )


class PedidoItem(Base):
    __tablename__ = "pedido_itens"

    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedidos.id"), index=True)
    produto_variacao_id: Mapped[int] = mapped_column(ForeignKey("produto_variacoes.id"), index=True)
    qtd: Mapped[int] = mapped_column(Integer)  # em unidades
    qtd_caixas: Mapped[int | None] = mapped_column(Integer)
    preco_unit: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    desconto: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    separado: Mapped[bool] = mapped_column(default=False)  # conferência na fila de separação

    pedido: Mapped[Pedido] = relationship(back_populates="itens")
    variacao: Mapped[ProdutoVariacao] = relationship()
