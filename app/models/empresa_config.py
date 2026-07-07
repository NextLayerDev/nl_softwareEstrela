from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmpresaConfig(Base):
    """Dados da própria empresa (emitente), usados no cabeçalho do comprovante/cupom.

    Registro único: sempre `id == 1`. Todos os campos opcionais — o cupom cai para
    "Estrela Gestão" enquanto não estiver preenchido.
    """

    __tablename__ = "empresa_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    razao_social: Mapped[str | None] = mapped_column(String(160))
    nome_fantasia: Mapped[str | None] = mapped_column(String(160))
    cnpj: Mapped[str | None] = mapped_column(String(20))
    inscricao_estadual: Mapped[str | None] = mapped_column(String(20))
    telefone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(180))
    endereco: Mapped[str | None] = mapped_column(Text)
    observacao_cupom: Mapped[str | None] = mapped_column(Text)
