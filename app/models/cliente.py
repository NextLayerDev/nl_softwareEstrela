from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import CategoriaCliente


class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(160), index=True)
    cnpj_cpf: Mapped[str | None] = mapped_column(String(20))
    telefone: Mapped[str | None] = mapped_column(String(40))
    telefone2: Mapped[str | None] = mapped_column(String(40))
    endereco: Mapped[str | None] = mapped_column(Text)
    condicao_pagto_padrao: Mapped[str | None] = mapped_column(
        String(60)
    )  # "À VISTA" ou texto livre ("30 dias", "2x") — ver pedido_service._parse_parcelas
    vendedor: Mapped[str | None] = mapped_column(String(120))
    categoria: Mapped[CategoriaCliente | None] = mapped_column(
        SAEnum(
            CategoriaCliente,
            name="categoriacliente",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    observacao: Mapped[str | None] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
