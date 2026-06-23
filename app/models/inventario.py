from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import StatusInventario

if TYPE_CHECKING:
    from app.models.produto import ProdutoVariacao


def _enum(py_enum, nome: str) -> SAEnum:
    return SAEnum(py_enum, name=nome, values_callable=lambda e: [m.value for m in e])


class Inventario(Base):
    __tablename__ = "inventarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    descricao: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[StatusInventario] = mapped_column(
        _enum(StatusInventario, "status_inventario"), default=StatusInventario.ABERTO, index=True
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    criado_por: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    aplicado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aplicado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))

    itens: Mapped[list[InventarioItem]] = relationship(
        back_populates="inventario", cascade="all, delete-orphan"
    )


class InventarioItem(Base):
    __tablename__ = "inventario_itens"

    id: Mapped[int] = mapped_column(primary_key=True)
    inventario_id: Mapped[int] = mapped_column(ForeignKey("inventarios.id"), index=True)
    produto_variacao_id: Mapped[int] = mapped_column(ForeignKey("produto_variacoes.id"))
    qtd_sistema: Mapped[int] = mapped_column(Integer, default=0)
    qtd_contada: Mapped[int | None] = mapped_column(Integer)

    inventario: Mapped[Inventario] = relationship(back_populates="itens")
    variacao: Mapped[ProdutoVariacao] = relationship()
