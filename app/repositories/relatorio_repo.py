from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.cliente import Cliente
from app.models.enums import StatusPedido
from app.models.pedido import Pedido, PedidoItem
from app.models.produto import Produto, ProdutoVariacao
from app.models.usuario import Usuario

# Status considerados "venda concretizada" para relatórios.
STATUS_FATURADOS = (StatusPedido.FATURADO, StatusPedido.ENTREGUE)


def _intervalo(de: date | None, ate: date | None) -> tuple[datetime | None, datetime | None]:
    ini = datetime.combine(de, time.min) if de else None
    fim = datetime.combine(ate, time.max) if ate else None
    return ini, fim


class RelatorioRepository:
    def vendas(
        self,
        db: Session,
        de: date | None = None,
        ate: date | None = None,
        vendedor_id: int | None = None,
        cliente_id: int | None = None,
    ) -> list[dict]:
        """Pedidos faturados/entregues no período, com vendedor e cliente."""
        ini, fim = _intervalo(de, ate)
        stmt = (
            select(
                Pedido.id,
                Pedido.numero,
                Pedido.criado_em,
                Pedido.faturado_em,
                Pedido.total,
                Pedido.desconto_total,
                Cliente.nome.label("cliente"),
                Usuario.nome.label("vendedor"),
                Pedido.vendedor_id,
            )
            .join(Cliente, Pedido.cliente_id == Cliente.id)
            .join(Usuario, Pedido.vendedor_id == Usuario.id)
            .where(Pedido.status.in_(STATUS_FATURADOS))
            .order_by(Pedido.faturado_em.desc().nulls_last(), Pedido.id.desc())
        )
        if ini is not None:
            stmt = stmt.where(Pedido.criado_em >= ini)
        if fim is not None:
            stmt = stmt.where(Pedido.criado_em <= fim)
        if vendedor_id is not None:
            stmt = stmt.where(Pedido.vendedor_id == vendedor_id)
        if cliente_id is not None:
            stmt = stmt.where(Pedido.cliente_id == cliente_id)
        return [dict(row._mapping) for row in db.execute(stmt)]

    def abc_produtos(
        self,
        db: Session,
        de: date | None = None,
        ate: date | None = None,
    ) -> list[dict]:
        """Soma de subtotal por produto em pedidos faturados (base da curva ABC)."""
        ini, fim = _intervalo(de, ate)
        stmt = (
            select(
                Produto.id.label("produto_id"),
                Produto.codigo,
                Produto.descricao,
                func.coalesce(func.sum(PedidoItem.subtotal), 0).label("valor"),
                func.coalesce(func.sum(PedidoItem.qtd), 0).label("qtd"),
            )
            .join(ProdutoVariacao, PedidoItem.produto_variacao_id == ProdutoVariacao.id)
            .join(Produto, ProdutoVariacao.produto_id == Produto.id)
            .join(Pedido, PedidoItem.pedido_id == Pedido.id)
            .where(Pedido.status.in_(STATUS_FATURADOS))
            .group_by(Produto.id, Produto.codigo, Produto.descricao)
            .order_by(func.sum(PedidoItem.subtotal).desc())
        )
        if ini is not None:
            stmt = stmt.where(Pedido.criado_em >= ini)
        if fim is not None:
            stmt = stmt.where(Pedido.criado_em <= fim)
        return [dict(row._mapping) for row in db.execute(stmt)]

    def valorizacao_estoque(self, db: Session) -> list[dict]:
        """Σ por produto de estoque_fisico × preco_custo (variações modo EXATO)."""
        stmt = (
            select(
                Produto.id.label("produto_id"),
                Produto.codigo,
                Produto.descricao,
                Produto.preco_custo,
                func.coalesce(func.sum(ProdutoVariacao.estoque_fisico), 0).label("fisico"),
            )
            .join(ProdutoVariacao, ProdutoVariacao.produto_id == Produto.id)
            .group_by(Produto.id, Produto.codigo, Produto.descricao, Produto.preco_custo)
            .order_by(Produto.codigo)
        )
        linhas: list[dict] = []
        for row in db.execute(stmt):
            m = dict(row._mapping)
            custo = Decimal(m["preco_custo"] or 0)
            fisico = int(m["fisico"] or 0)
            m["valor"] = (custo * fisico).quantize(Decimal("0.01"))
            linhas.append(m)
        return linhas


relatorio_repo = RelatorioRepository()
