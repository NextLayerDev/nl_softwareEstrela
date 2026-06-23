from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator


class PedidoCreate(BaseModel):
    """Abre um rascunho de pedido para um cliente."""

    cliente_id: int
    observacao: str | None = None


class ItemAdicionar(BaseModel):
    """Adiciona um item ao pedido (rascunho).

    A quantidade pode vir em unidades (`qtd`) ou em caixas (`qtd_caixas`);
    quando vier em caixas, o service converte para unidades pelo
    `produto.unidades_por_caixa`. `preco_unit` é editável pelo vendedor;
    se vier vazio, o service sugere pela faixa de preço.
    """

    variacao_id: int
    qtd: int | None = None
    qtd_caixas: int | None = None
    preco_unit: Decimal | None = None
    desconto: Decimal = Decimal("0")

    @field_validator("qtd_caixas")
    @classmethod
    def _caixas_inteiras(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("A quantidade de caixas deve ser maior que zero.")
        return v

    @field_validator("desconto")
    @classmethod
    def _desconto_nao_negativo(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("O desconto não pode ser negativo.")
        return v


class PedidoItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    produto_variacao_id: int
    qtd: int
    qtd_caixas: int | None
    preco_unit: Decimal
    desconto: Decimal
    subtotal: Decimal
    separado: bool


class SugestaoPreco(BaseModel):
    """Preço sugerido por faixa para uma quantidade informada."""

    preco_sugerido: Decimal
    faixa: str  # "atacado" | "varejo"
    preco_pouca_qtd: Decimal
    preco_muita_qtd: Decimal
    preco_promocional: Decimal | None
    qtd_corte_atacado: int | None
