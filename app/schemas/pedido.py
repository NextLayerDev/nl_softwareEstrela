from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PedidoCreate(BaseModel):
    """Abre um rascunho de pedido.

    Os três campos de cliente são opcionais e se completam: `cliente_id` vem quando o
    vendedor escolhe uma sugestão da busca; nome e telefone são o texto livre do balcão.
    Sem nenhum deles, o pedido é de CONSUMIDOR.
    """

    cliente_id: int | None = None
    cliente_nome: str | None = None
    cliente_telefone: str | None = None
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


class ItemAvulsoAdicionar(BaseModel):
    """Adiciona um item que NÃO está no catálogo.

    É a exceção deliberada ao "preço vem do catálogo": aqui não há catálogo de onde
    tirar preço, então nome e valor são o que o vendedor digitou. Não reserva estoque
    nem entra na baixa — não há saldo a mover.
    """

    nome: str = Field(min_length=1, max_length=200)
    codigo: str = Field(default="", max_length=60)
    detalhe: str = Field(default="", max_length=500)
    qtd: int = Field(ge=1, le=100_000)
    preco_unit: Decimal = Field(ge=0, le=9_999_999)
    desconto: Decimal = Decimal("0")

    @field_validator("desconto")
    @classmethod
    def _desconto_nao_negativo(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("O desconto não pode ser negativo.")
        return v


class ItemCarrinhoCatalogo(BaseModel):
    """Linha do carrinho vinda do catálogo (tela `/pedidos/novo`)."""

    tipo: Literal["catalogo"]
    variacao_id: int
    qtd: int | None = None
    qtd_caixas: int | None = None
    preco_unit: Decimal | None = None
    desconto: Decimal = Decimal("0")


class ItemCarrinhoAvulso(BaseModel):
    """Linha do carrinho sem produto no catálogo."""

    tipo: Literal["avulso"]
    nome: str = Field(min_length=1, max_length=200)
    codigo: str = Field(default="", max_length=60)
    detalhe: str = Field(default="", max_length=500)
    qtd: int = Field(ge=1, le=100_000)
    preco_unit: Decimal = Field(ge=0, le=9_999_999)
    desconto: Decimal = Decimal("0")


ItemCarrinho = Annotated[
    ItemCarrinhoCatalogo | ItemCarrinhoAvulso,
    Field(discriminator="tipo"),
]


class PedidoCompletoCreate(PedidoCreate):
    """Pedido inteiro numa tacada: cliente + itens + desconto.

    O `/pedidos/novo` monta o carrinho no navegador e grava tudo de uma vez, em vez de
    abrir um rascunho vazio e bater no servidor a cada item. O teto de 100 itens existe
    para uma colagem gigante não virar um POST sem fim.
    """

    desconto_total: Decimal = Decimal("0")
    itens: list[ItemCarrinho] = Field(min_length=1, max_length=100)

    @field_validator("desconto_total")
    @classmethod
    def _desconto_nao_negativo(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("O desconto não pode ser negativo.")
        return v


class PedidoItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    produto_variacao_id: int | None
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
