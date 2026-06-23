"""Importa todos os models para que o Alembic autogenerate enxergue o metadata completo."""

from app.models.auditoria import Auditoria
from app.models.categoria import Categoria
from app.models.cliente import Cliente
from app.models.conta_receber import ContaReceber
from app.models.fornecedor import Fornecedor
from app.models.inventario import Inventario, InventarioItem
from app.models.movimentacao import MovimentacaoEstoque
from app.models.pedido import Pedido, PedidoItem
from app.models.produto import Produto, ProdutoCodigoAlt, ProdutoVariacao
from app.models.sync_outbox import SyncOutbox
from app.models.usuario import Usuario

__all__ = [
    "Auditoria",
    "Categoria",
    "Cliente",
    "ContaReceber",
    "Fornecedor",
    "Inventario",
    "InventarioItem",
    "MovimentacaoEstoque",
    "Pedido",
    "PedidoItem",
    "Produto",
    "ProdutoCodigoAlt",
    "ProdutoVariacao",
    "SyncOutbox",
    "Usuario",
]
