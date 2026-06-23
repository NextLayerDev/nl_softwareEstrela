from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

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
    def _senha_minima(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("A senha deve ter ao menos 6 caracteres.")
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
