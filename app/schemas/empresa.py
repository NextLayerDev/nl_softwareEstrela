from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class EmpresaConfigUpdate(BaseModel):
    """Dados do emitente exibidos no comprovante/cupom (todos opcionais)."""

    razao_social: str | None = None
    nome_fantasia: str | None = None
    cnpj: str | None = None
    inscricao_estadual: str | None = None
    telefone: str | None = None
    email: str | None = None
    endereco: str | None = None
    observacao_cupom: str | None = None


class EmpresaConfigRead(EmpresaConfigUpdate):
    model_config = ConfigDict(from_attributes=True)

    id: int
