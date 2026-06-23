from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(160), index=True)
    cnpj_cpf: Mapped[str | None] = mapped_column(String(20))
    telefone: Mapped[str | None] = mapped_column(String(40))
    endereco: Mapped[str | None] = mapped_column(Text)
    condicao_pagto_padrao: Mapped[str | None] = mapped_column(
        String(60)
    )  # "à vista", "30 dias", "2x"
    limite_credito: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
