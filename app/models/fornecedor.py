from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Fornecedor(Base):
    __tablename__ = "fornecedores"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(160), index=True)
    cnpj: Mapped[str | None] = mapped_column(String(20))
    contato: Mapped[str | None] = mapped_column(String(160))
