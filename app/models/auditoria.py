from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Auditoria(Base):
    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    entidade: Mapped[str] = mapped_column(String(60), index=True)
    entidade_id: Mapped[int | None] = mapped_column(Integer)
    acao: Mapped[str] = mapped_column(String(40))  # criar/editar/cancelar/baixar...
    antes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    depois: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
