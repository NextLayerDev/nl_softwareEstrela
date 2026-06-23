from __future__ import annotations

from pydantic import BaseModel, field_validator


class EntradaCreate(BaseModel):
    variacao_id: int
    qtd: int

    @field_validator("qtd")
    @classmethod
    def _qtd_positiva(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("A quantidade deve ser maior que zero.")
        return v


class AjusteCreate(BaseModel):
    variacao_id: int
    novo_saldo: int
    motivo: str

    @field_validator("motivo")
    @classmethod
    def _motivo_obrigatorio(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("O motivo do ajuste é obrigatório.")
        return v

    @field_validator("novo_saldo")
    @classmethod
    def _saldo_nao_negativo(cls, v: int) -> int:
        if v < 0:
            raise ValueError("O novo saldo não pode ser negativo.")
        return v


class InventarioCreate(BaseModel):
    descricao: str | None = None
    variacao_ids: list[int] = []


class ContagemCreate(BaseModel):
    item_id: int
    qtd_contada: int

    @field_validator("qtd_contada")
    @classmethod
    def _nao_negativa(cls, v: int) -> int:
        if v < 0:
            raise ValueError("A quantidade contada não pode ser negativa.")
        return v
