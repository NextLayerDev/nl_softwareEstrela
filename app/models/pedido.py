from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import OrigemPedido, StatusPedido

if TYPE_CHECKING:
    from app.models.cliente import Cliente
    from app.models.produto import ProdutoVariacao
    from app.models.usuario import Usuario


def _enum(py_enum, nome: str) -> SAEnum:
    return SAEnum(py_enum, name=nome, values_callable=lambda e: [m.value for m in e])


class Pedido(Base):
    __tablename__ = "pedidos"
    __table_args__ = (
        Index("ix_pedidos_vendedor_criado", "vendedor_id", "criado_em"),
        Index("ix_pedidos_cliente_criado", "cliente_id", "criado_em"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    numero: Mapped[int | None] = mapped_column(
        Integer, unique=True, index=True
    )  # sequence ao confirmar
    # No balcão o cliente quase nunca está cadastrado — e parar a venda para cadastrar
    # é o que fazia o vendedor abandonar o sistema. Por isso o vínculo é opcional: ou o
    # pedido aponta para um Cliente, ou guarda nome/telefone soltos (ou nada, e vira
    # CONSUMIDOR). Leia sempre pelas propriedades abaixo, nunca por `pedido.cliente`.
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id"), index=True)
    cliente_nome: Mapped[str | None] = mapped_column(String(160))
    cliente_telefone: Mapped[str | None] = mapped_column(String(40))
    vendedor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    status: Mapped[StatusPedido] = mapped_column(
        _enum(StatusPedido, "status_pedido"), default=StatusPedido.RASCUNHO, index=True
    )
    desconto_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    observacao: Mapped[str | None] = mapped_column(Text)
    origem: Mapped[OrigemPedido] = mapped_column(
        _enum(OrigemPedido, "origem_pedido"), default=OrigemPedido.LOCAL
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    faturado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cliente: Mapped[Cliente | None] = relationship()
    vendedor: Mapped[Usuario] = relationship()
    itens: Mapped[list[PedidoItem]] = relationship(
        back_populates="pedido", cascade="all, delete-orphan"
    )

    # ---------------------------------------------------------------- exibição
    # Templates, impressão, cupom e eventos leem daqui. `pedido.cliente.nome` volta a
    # ser None-unsafe no instante em que alguém vende para um cliente não cadastrado.
    @property
    def nome_cliente(self) -> str:
        if self.cliente is not None:
            return self.cliente.nome
        return self.cliente_nome or "CONSUMIDOR"

    @property
    def telefone_cliente(self) -> str | None:
        if self.cliente is not None:
            return self.cliente.telefone or self.cliente.telefone2 or self.cliente_telefone
        return self.cliente_telefone

    @property
    def condicao_pagto(self) -> str:
        """Condição de pagamento do pedido, com o padrão do balcão.

        Sem cliente cadastrado não há condição negociada: quem passa no balcão paga
        à vista. É esta string que o `_parse_parcelas` do faturamento interpreta.
        """
        if self.cliente is not None and self.cliente.condicao_pagto_padrao:
            return self.cliente.condicao_pagto_padrao
        return "À VISTA"


class PedidoItem(Base):
    __tablename__ = "pedido_itens"

    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedidos.id"), index=True)
    # NULL = item avulso: o vendedor lançou algo que não está no catálogo (fora de
    # estoque, produto novo, serviço). Sem variação não há saldo para reservar nem
    # baixar — o estoque continua mudando só por movimentação, porque não existe
    # movimentação a fazer. Leia sempre pelas propriedades de exibição abaixo.
    produto_variacao_id: Mapped[int | None] = mapped_column(
        ForeignKey("produto_variacoes.id"), index=True
    )
    # Snapshot do que foi vendido, gravado na hora do lançamento. O pedido é documento:
    # renomear ou recodificar um produto no catálogo não pode reescrever o que o cliente
    # comprou mês passado. Para o item avulso, é o único lugar onde o nome existe.
    descricao: Mapped[str] = mapped_column(String(200))
    codigo: Mapped[str | None] = mapped_column(String(60))
    detalhe: Mapped[str | None] = mapped_column(Text)  # observação livre do item avulso
    qtd: Mapped[int] = mapped_column(Integer)  # em unidades
    qtd_caixas: Mapped[int | None] = mapped_column(Integer)
    preco_unit: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    desconto: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    separado: Mapped[bool] = mapped_column(default=False)  # conferência na fila de separação

    pedido: Mapped[Pedido] = relationship(back_populates="itens")
    variacao: Mapped[ProdutoVariacao | None] = relationship()

    # ---------------------------------------------------------------- exibição
    # Mesmo motivo das propriedades do Pedido: `item.variacao.produto.descricao` volta a
    # ser None-unsafe no instante em que alguém vende um item avulso. Telas, impressão,
    # cupom e separação leem daqui.
    @property
    def e_avulso(self) -> bool:
        return self.produto_variacao_id is None

    @property
    def descricao_exibida(self) -> str:
        if self.descricao:
            return self.descricao
        if self.variacao is not None:
            return self.variacao.produto.descricao
        return "ITEM AVULSO"

    @property
    def codigo_exibido(self) -> str | None:
        if self.codigo:
            return self.codigo
        if self.variacao is not None:
            return self.variacao.produto.codigo
        return None

    @property
    def cor_exibida(self) -> str | None:
        if self.variacao is not None:
            return self.variacao.cor or None
        return None

    @property
    def imagem_exibida(self) -> str | None:
        if self.variacao is not None:
            return self.variacao.imagem_url
        return None

    @property
    def unidades_por_caixa(self) -> int | None:
        """Só para a linha "N cx × M = X un" — item avulso não tem caixa no catálogo."""
        if self.variacao is not None:
            return self.variacao.produto.unidades_por_caixa
        return None
