from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(255))
    perfil: Mapped[str] = mapped_column(String(20), index=True)  # ver app.models.enums.Perfil
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    # Versão da sessão: incrementada em reset de senha / desativação para invalidar tokens
    # JWT já emitidos (o token carrega o "tv" e get_current_user compara). Ver app/deps/auth.py.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
