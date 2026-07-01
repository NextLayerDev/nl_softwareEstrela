from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.enums import PERFIS_SEM_CUSTO, EstoqueModo, RotuloAprox


class VariacaoCreate(BaseModel):
    cor: str = ""
    estoque_modo: EstoqueModo = EstoqueModo.APROXIMADO
    estoque_fisico: int = 0
    rotulo_aprox: RotuloAprox | None = None
    estoque_minimo: int = 0
    ativo: bool = True


class VariacaoCorUpdate(BaseModel):
    cor: str = ""

    @field_validator("cor")
    @classmethod
    def _trim(cls, v: str) -> str:
        return v.strip()


class VariacaoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cor: str
    estoque_modo: EstoqueModo
    estoque_fisico: int
    estoque_reservado: int
    rotulo_aprox: RotuloAprox | None
    estoque_minimo: int
    ativo: bool
    imagem_url: str | None = None


class CodigoAltCreate(BaseModel):
    codigo_alt: str
    fornecedor_id: int | None = None


class CodigoAltRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo_alt: str
    fornecedor_id: int | None


class ProdutoCreate(BaseModel):
    codigo: str
    descricao: str
    categoria_id: int | None = None
    unidades_por_caixa: int | None = None
    localizacao: str | None = None
    preco_pouca_qtd: Decimal = Decimal("0")
    preco_muita_qtd: Decimal = Decimal("0")
    preco_promocional: Decimal | None = None
    qtd_corte_atacado: int | None = None
    preco_custo: Decimal = Decimal("0")
    observacao: str | None = None
    ativo: bool = True
    publicar_catalogo: bool = False
    variacoes: list[VariacaoCreate] = []
    codigos_alt: list[CodigoAltCreate] = []

    @field_validator("codigo", "descricao")
    @classmethod
    def _nao_vazio(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Campo obrigatório.")
        return v


class ProdutoUpdate(BaseModel):
    descricao: str | None = None
    categoria_id: int | None = None
    unidades_por_caixa: int | None = None
    localizacao: str | None = None
    preco_pouca_qtd: Decimal | None = None
    preco_muita_qtd: Decimal | None = None
    preco_promocional: Decimal | None = None
    qtd_corte_atacado: int | None = None
    preco_custo: Decimal | None = None
    observacao: str | None = None
    ativo: bool | None = None
    publicar_catalogo: bool | None = None


class ProdutoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo: str
    descricao: str
    categoria_id: int | None
    unidades_por_caixa: int | None
    localizacao: str | None
    preco_pouca_qtd: Decimal
    preco_muita_qtd: Decimal
    preco_promocional: Decimal | None
    qtd_corte_atacado: int | None
    preco_custo: Decimal
    observacao: str | None
    ativo: bool
    publicar_catalogo: bool
    variacoes: list[VariacaoRead] = []
    codigos_alt: list[CodigoAltRead] = []


def produto_para_dict(produto: Any, perfil: str) -> dict[str, Any]:
    """Serializa um Produto ocultando preco_custo para perfis sem permissão (doc §7)."""
    dados = ProdutoRead.model_validate(produto).model_dump()
    if perfil in PERFIS_SEM_CUSTO:
        dados.pop("preco_custo", None)
    return dados


def pode_ver_custo(perfil: str) -> bool:
    return perfil not in PERFIS_SEM_CUSTO
