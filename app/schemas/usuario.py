from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.security import senha_fraca
from app.models.enums import Perfil


class UsuarioCreate(BaseModel):
    nome: str
    email: str
    senha: str
    perfil: Perfil
    ativo: bool = True

    @field_validator("nome", "email")
    @classmethod
    def _nao_vazio(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Campo obrigatório.")
        return v

    @field_validator("senha")
    @classmethod
    def _senha_forte(cls, v: str) -> str:
        erro = senha_fraca(v)
        if erro:
            raise ValueError(erro)
        return v


class UsuarioUpdate(BaseModel):
    nome: str | None = None
    email: str | None = None
    perfil: Perfil | None = None
    ativo: bool | None = None


class UsuarioRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    email: str
    perfil: str
    ativo: bool
    criado_em: datetime
