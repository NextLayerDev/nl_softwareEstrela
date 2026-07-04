from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class DadosFiscais(BaseModel):
    """Campos usados para emitir NF-e via eNotas (todos opcionais no cadastro)."""

    email: str | None = None
    inscricao_estadual: str | None = None
    contribuinte_icms: bool = False
    fisc_cep: str | None = None
    fisc_logradouro: str | None = None
    fisc_numero: str | None = None
    fisc_complemento: str | None = None
    fisc_bairro: str | None = None
    fisc_cidade: str | None = None
    fisc_uf: str | None = None


class ClienteCreate(DadosFiscais):
    nome: str
    cnpj_cpf: str | None = None
    telefone: str | None = None
    telefone2: str | None = None
    endereco: str | None = None
    condicao_pagto_padrao: str | None = None
    vendedor: str | None = None
    categoria: str | None = None
    observacao: str | None = None
    ativo: bool = True

    @field_validator("nome")
    @classmethod
    def _nome_nao_vazio(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Nome é obrigatório.")
        return v


class ClienteUpdate(DadosFiscais):
    nome: str | None = None
    cnpj_cpf: str | None = None
    telefone: str | None = None
    telefone2: str | None = None
    endereco: str | None = None
    condicao_pagto_padrao: str | None = None
    vendedor: str | None = None
    categoria: str | None = None
    observacao: str | None = None
    ativo: bool | None = None


class ClienteRead(DadosFiscais):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    cnpj_cpf: str | None
    telefone: str | None
    telefone2: str | None
    endereco: str | None
    condicao_pagto_padrao: str | None
    vendedor: str | None
    categoria: str | None
    observacao: str | None
    ativo: bool
