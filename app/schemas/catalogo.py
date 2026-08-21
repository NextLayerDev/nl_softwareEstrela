"""Formas do Catálogo Inteligente — o documento A4 que vira PDF."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class CatalogoCartao(BaseModel):
    """Um produto no catálogo: foto, nome, descrição curta, cores e preço."""

    codigo: str
    descricao: str
    descricao_curta: str
    # `data:` URI já pronta. O WeasyPrint não pode buscar a foto por HTTP: a rota é
    # relativa e autenticada, e seria uma requisição DENTRO de outra — num uvicorn de um
    # worker só, isso trava.
    foto: str | None = None
    cores: list[str] = []
    cores_ocultas: int = 0
    preco: Decimal = Decimal("0")


class CatalogoSecao(BaseModel):
    """Uma categoria com seus produtos."""

    nome: str
    cartoes: list[CatalogoCartao] = []


class CatalogoDoc(BaseModel):
    """O documento inteiro, pronto para o template."""

    secoes: list[CatalogoSecao] = []
    total: int = 0
    truncado: bool = False

    @property
    def vazio(self) -> bool:
        return self.total == 0
