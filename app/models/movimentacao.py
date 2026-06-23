from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import OrigemMov, TipoMov

if TYPE_CHECKING:
    from app.models.produto import ProdutoVariacao


def _enum(py_enum, nome: str) -> SAEnum:
    return SAEnum(py_enum, name=nome, values_callable=lambda e: [m.value for m in e])


class MovimentacaoEstoque(Base):
    """Append-only. Nunca sofre UPDATE/DELETE — é o livro-razão do estoque."""

    __tablename__ = "movimentacoes_estoque"

    id: Mapped[int] = mapped_column(primary_key=True)
    produto_variacao_id: Mapped[int] = mapped_column(ForeignKey("produto_variacoes.id"), index=True)
    tipo: Mapped[TipoMov] = mapped_column(_enum(TipoMov, "tipo_mov"))
    qtd: Mapped[int] = mapped_column(Integer)
    origem: Mapped[OrigemMov] = mapped_column(_enum(OrigemMov, "origem_mov"))
    ref_id: Mapped[int | None] = mapped_column(Integer)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    saldo_apos: Mapped[int] = mapped_column(Integer)
    motivo: Mapped[str | None] = mapped_column(String(255))
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    variacao: Mapped[ProdutoVariacao] = relationship()
