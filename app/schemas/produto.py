from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import PERFIS_SEM_CUSTO, EstoqueModo, RotuloAprox


class FaixaPrecoCreate(BaseModel):
    """Uma linha da tabela de atacado, como veio do formulário."""

    min_qtd: int = Field(ge=1, le=1_000_000)
    preco: Decimal = Field(ge=0, le=9_999_999)


class FaixaPrecoRead(FaixaPrecoCreate):
    # `from_attributes` é obrigatório: `produto_para_dict` faz
    # `ProdutoRead.model_validate(produto)`, e sem isto a validação estoura no objeto
    # ORM aninhado.
    model_config = ConfigDict(from_attributes=True)


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
    preco_minimo: Decimal = Decimal("0")
    observacao: str | None = None
    ativo: bool = True
    publicar_catalogo: bool = False
    variacoes: list[VariacaoCreate] = []
    codigos_alt: list[CodigoAltCreate] = []
    faixas: list[FaixaPrecoCreate] = []

    @field_validator("codigo", "descricao")
    @classmethod
    def _nao_vazio(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Campo obrigatório.")
        return v


class ProdutoUpdate(BaseModel):
    codigo: str | None = None
    descricao: str | None = None
    categoria_id: int | None = None
    unidades_por_caixa: int | None = None
    localizacao: str | None = None
    preco_pouca_qtd: Decimal | None = None
    preco_muita_qtd: Decimal | None = None
    preco_promocional: Decimal | None = None
    qtd_corte_atacado: int | None = None
    preco_custo: Decimal | None = None
    preco_minimo: Decimal | None = None
    observacao: str | None = None
    ativo: bool | None = None
    publicar_catalogo: bool | None = None
    codigos_alt: list[CodigoAltCreate] = []
    # `None` é "não mexe" e lista vazia é "apagar a tabela" — os dois são pedidos
    # legítimos, então a diferença tem que sobreviver até o service.
    faixas: list[FaixaPrecoCreate] | None = None

    @field_validator("codigo")
    @classmethod
    def _codigo_nao_vazio(cls, v: str | None) -> str | None:
        # None = "não veio no form"; só valida quando vier um valor.
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Campo obrigatório.")
        return v


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
    preco_minimo: Decimal
    observacao: str | None
    ativo: bool
    publicar_catalogo: bool
    variacoes: list[VariacaoRead] = []
    codigos_alt: list[CodigoAltRead] = []
    faixas: list[FaixaPrecoRead] = []


def produto_para_dict(produto: Any, perfil: str) -> dict[str, Any]:
    """Serializa um Produto ocultando os campos restritos ao admin (doc §7).

    `preco_minimo` sai junto com `preco_custo`: é o piso que o vendedor não pode furar,
    e entregá-lo é entregar exatamente onde a margem acaba.
    """
    dados = ProdutoRead.model_validate(produto).model_dump()
    if perfil in PERFIS_SEM_CUSTO:
        dados.pop("preco_custo", None)
        dados.pop("preco_minimo", None)
    return dados


def pode_ver_custo(perfil: str) -> bool:
    return perfil not in PERFIS_SEM_CUSTO


def pode_definir_minimo(perfil: str) -> bool:
    """Só quem define o piso pode alterá-lo — é a trava do preço, não um dado a mais."""
    return perfil not in PERFIS_SEM_CUSTO
