from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.schemas.produto import FaixaPrecoRead


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
    """Preço sugerido para uma quantidade informada.

    Os campos novos têm default de propósito: quem constrói isto parcialmente (teste,
    fixture) continua funcionando, e a mudança para tabela de faixas fica aditiva.
    """

    preco_sugerido: Decimal
    # Chave de máquina — comparada em código e em teste. "varejo" | "atacado" | "tabela".
    faixa: str
    # Texto para a tela: "10 a 49 un". Fica separado da chave porque `faixa == "tabela"`
    # não diz nada a quem está olhando o pedido.
    faixa_rotulo: str = ""
    preco_pouca_qtd: Decimal
    preco_muita_qtd: Decimal
    preco_promocional: Decimal | None
    qtd_corte_atacado: int | None
    faixas: list[FaixaPrecoRead] = []
    proxima_faixa: FaixaPrecoRead | None = None


class _ResumoBase(BaseModel):
    """Sai em camelCase para o JS.

    O `resumo_pedido.js` desenha o mesmo objeto que o carrinho de `/pedidos/novo` monta
    no navegador, e lá as chaves nascem em camelCase. Traduzir aqui, no serializador, é
    o que deixa o desenho ter UM contrato só em vez de dois dialetos.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ResumoItem(_ResumoBase):
    """Uma linha da planilha do resumo. Dinheiro em CENTAVOS inteiros."""

    codigo: str = ""
    descricao: str
    qtd: int
    preco_centavos: int
    subtotal_centavos: int


class ResumoPedidoOut(_ResumoBase):
    """O resumo do pedido em imagem, pronto para o `<canvas>` desenhar.

    O mesmo formato serve as duas telas: o carrinho de `/pedidos/novo` monta isto no
    navegador, o detalhe recebe daqui. Centavos inteiros do começo ao fim — o desenho
    não recalcula dinheiro, só formata.
    """

    titulo: str = "Pedido"
    cliente: str
    data: str
    numero: str
    itens: list[ResumoItem] = []
    desconto_centavos: int = 0
    total_centavos: int = 0


# ============================================================= planilha editável
class LinhaPlanilha(BaseModel):
    """Uma linha da planilha editável (criar pedido ou editar rascunho).

    `variacao_id` vem quando o vendedor escolheu o produto (pelo código ou pela busca);
    sem ele, o servidor ainda tenta casar pelo código antes de gravar como avulso.
    `item_id` + `identidade_mudou=False` é a linha que já existia no rascunho e só teve
    quantidade ou valor mexidos: ela é atualizada no lugar, sem perder caixas/desconto.
    """

    item_id: int | None = None
    identidade_mudou: bool = True
    variacao_id: int | None = None
    codigo: str = Field(default="", max_length=60)
    descricao: str = Field(default="", max_length=200)
    qtd: int = Field(ge=1, le=100_000)
    preco_unit: Decimal = Field(ge=0, le=9_999_999)


class PedidoPlanilhaSalvar(BaseModel):
    """A planilha inteira. Cliente é opcional (sem ele, vira CONSUMIDOR)."""

    cliente_id: int | None = None
    cliente_nome: str | None = Field(default=None, max_length=160)
    itens: list[LinhaPlanilha] = Field(min_length=1, max_length=100)
    confirmar: bool = False


class ConsultaPlanilha(BaseModel):
    """O que a célula CODIGO (ou a troca de quantidade) pergunta ao servidor."""

    codigo: str = Field(default="", max_length=60)
    # Texto da célula DESCRICAO: é dele que sai a COR quando o código tem várias
    # ("GARRAFA PRETA"), a mesma leitura que a colagem da planilha do dia faz.
    descricao: str = Field(default="", max_length=200)
    variacao_id: int | None = None
    qtd: int = Field(default=1, ge=1, le=100_000)


class OpcaoPlanilha(BaseModel):
    variacao_id: int
    codigo: str
    descricao: str
    cor: str | None = None


class ResolucaoPlanilha(BaseModel):
    """Resposta por linha consultada.

    - `ok`: casou com UMA variação — já vem com o preço da faixa para a quantidade.
    - `duvida`: o código é de um produto com várias cores; `opcoes` vira a lista de escolha.
    - `nada`: não casou; a linha segue como item avulso (`motivo` explica).
    """

    situacao: Literal["ok", "duvida", "nada"]
    variacao_id: int | None = None
    codigo: str = ""
    descricao: str = ""
    cor: str | None = None
    preco_centavos: int | None = None
    motivo: str = ""
    opcoes: list[OpcaoPlanilha] = []


class ItemPlanilhaOut(_ResumoBase):
    """Linha do rascunho carregada na planilha. Dinheiro em centavos, como o resumo."""

    item_id: int
    variacao_id: int | None = None
    codigo: str = ""
    descricao: str
    cor: str | None = None
    qtd: int
    preco_centavos: int
    desconto_centavos: int = 0


class PlanilhaPedidoOut(_ResumoBase):
    """Estado inicial do editor de planilha de um pedido existente."""

    pedido_id: int
    numero: str
    status: str
    editavel: bool
    data: str
    cliente: str
    cliente_vinculado: bool = False
    desconto_centavos: int = 0
    itens: list[ItemPlanilhaOut] = []


class PlanilhaSalvaOut(BaseModel):
    ok: bool = True
    pedido_id: int
    numero: int | None = None
    aviso: str | None = None
