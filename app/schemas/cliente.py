from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator


class ClienteCreate(BaseModel):
    nome: str
    cnpj_cpf: str | None = None
    telefone: str | None = None
    endereco: str | None = None
    condicao_pagto_padrao: str | None = None
    limite_credito: Decimal | None = None
    ativo: bool = True

    @field_validator("nome")
    @classmethod
    def _nome_nao_vazio(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Nome é obrigatório.")
        return v


class ClienteUpdate(BaseModel):
    nome: str | None = None
    cnpj_cpf: str | None = None
    telefone: str | None = None
    endereco: str | None = None
    condicao_pagto_padrao: str | None = None
    limite_credito: Decimal | None = None
    ativo: bool | None = None


class ClienteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    cnpj_cpf: str | None
    telefone: str | None
    endereco: str | None
    condicao_pagto_padrao: str | None
    limite_credito: Decimal | None
    ativo: bool
