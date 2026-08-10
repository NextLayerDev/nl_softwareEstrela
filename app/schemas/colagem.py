from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class SugestaoProduto(BaseModel):
    """Candidato oferecido quando a linha ficou em dúvida.

    Vira um botão de um clique na tela de pendências: já carrega a variação, a
    quantidade e o preço da linha colada, e reaproveita o `POST /pedidos/{id}/itens`
    que já existe — resolver a dúvida não passa por caminho de escrita novo.
    """

    variacao_id: int
    codigo: str
    descricao: str
    cor: str | None = None


class ItemAplicado(BaseModel):
    linha: int
    variacao_id: int
    codigo: str
    descricao: str
    cor: str | None = None
    qtd: int
    preco_unit: Decimal
    tipo_match: str  # codigo_exato | codigo_alt | codigo_normalizado | descricao:0.71


class Pendencia(BaseModel):
    """Linha que não virou item. O `motivo` é texto pronto para a tela."""

    linha: int
    bruto: str
    codigo: str
    descricao: str
    qtd: int | None = None
    preco_unit: Decimal | None = None
    motivo: str
    sugestoes: list[SugestaoProduto] = []


class LinhaIgnoradaOut(BaseModel):
    linha: int
    bruto: str
    motivo: str


class ResultadoColagem(BaseModel):
    pedido_id: int
    linhas_lidas: int
    aplicados: list[ItemAplicado] = []
    pendencias: list[Pendencia] = []
    ignoradas: list[LinhaIgnoradaOut] = []

    @property
    def tudo_casou(self) -> bool:
        """Sem pendência e com pelo menos um item: dá para mandar o vendedor direto ao pedido."""
        return bool(self.aplicados) and not self.pendencias


class PedidoColado(BaseModel):
    """Um pedido criado a partir de um bloco da planilha do dia."""

    pedido_id: int
    rotulo: str  # "#123" — número do pedido, ou o id enquanto ele é rascunho
    cliente: str
    cliente_vinculado: bool = False  # achou o cadastro pelo código do bloco
    codigo_cliente: str | None = None
    data_planilha: str | None = None
    total: Decimal
    resultado: ResultadoColagem


class ResultadoLote(BaseModel):
    """Resultado de uma planilha inteira: vários blocos, vários pedidos."""

    pedidos: list[PedidoColado] = []
    ignoradas: list[LinhaIgnoradaOut] = []  # só quando nenhum bloco tinha produto

    @property
    def total_aplicados(self) -> int:
        return sum(len(p.resultado.aplicados) for p in self.pedidos)

    @property
    def total_pendencias(self) -> int:
        return sum(len(p.resultado.pendencias) for p in self.pedidos)

    @property
    def tudo_casou(self) -> bool:
        return bool(self.pedidos) and not self.total_pendencias
