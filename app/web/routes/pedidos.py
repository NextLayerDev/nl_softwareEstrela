from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.controllers.pedido_controller import pedido_controller
from app.core.errors import NaoEncontradoError
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.enums import StatusPedido
from app.models.usuario import Usuario
from app.repositories.cliente_repo import cliente_repo
from app.repositories.estoque_repo import estoque_repo
from app.schemas.pedido import ItemAdicionar, PedidoCreate
from app.services.pedido_service import pedido_service

router = APIRouter()

_CRIA = ("admin", "vendedor")


def _to_decimal(valor: str | None) -> Decimal:
    if valor is None or str(valor).strip() == "":
        return Decimal("0")
    try:
        return Decimal(str(valor).replace(",", "."))
    except InvalidOperation:
        return Decimal("0")


# ===================================================================== LISTAR
@router.get("/pedidos", response_class=HTMLResponse)
def index_pedidos(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    pedidos = pedido_controller.listar(db, usuario)
    contexto = {"user": usuario, "titulo": "Pedidos", "pedidos": pedidos}
    return templates.TemplateResponse(request, "pedidos/index.html", contexto)


# ===================================================================== NOVO
@router.get("/pedidos/novo", response_class=HTMLResponse)
def novo_pedido(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    clientes = cliente_repo.listar(db)
    contexto = {"user": usuario, "titulo": "Novo pedido", "clientes": clientes}
    return templates.TemplateResponse(request, "pedidos/novo.html", contexto)


@router.post("/pedidos")
def criar_pedido(
    cliente_id: int = Form(...),
    observacao: str = Form(""),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    dados = PedidoCreate(cliente_id=cliente_id, observacao=observacao or None)
    pedido = pedido_controller.criar(db, dados, usuario)
    return RedirectResponse(url=f"/pedidos/{pedido.id}", status_code=303)


# ===================================================================== DETALHE
@router.get("/pedidos/{pedido_id}", response_class=HTMLResponse)
def detalhe_pedido(
    request: Request,
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {
        "user": usuario,
        "titulo": f"Pedido #{pedido.numero or pedido.id}",
        "pedido": pedido,
        "editavel": pedido.status == StatusPedido.RASCUNHO,
    }
    return templates.TemplateResponse(request, "pedidos/detalhe.html", contexto)


# ===================================================================== SALDO (HTMX)
@router.get("/pedidos/saldo/{variacao_id}", response_class=HTMLResponse)
def saldo_variacao(
    request: Request,
    variacao_id: int,
    qtd: int = 1,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Mostra disponível/selo + sugestão de preço por faixa ao escolher variação."""
    variacao = estoque_repo.get_variacao(db, variacao_id)
    if variacao is None:
        raise NaoEncontradoError("Variação não encontrada.")
    sugestao = pedido_service.sugerir_preco(variacao.produto, max(qtd, 1))
    contexto = {
        "user": usuario,
        "variacao": variacao,
        "produto": variacao.produto,
        "sugestao": sugestao,
        "qtd": qtd,
    }
    return templates.TemplateResponse(request, "pedidos/_saldo.html", contexto)


# ===================================================================== ITENS
@router.post("/pedidos/{pedido_id}/itens", response_class=HTMLResponse)
def adicionar_item(
    request: Request,
    pedido_id: int,
    variacao_id: int = Form(...),
    qtd: int | None = Form(None),
    qtd_caixas: int | None = Form(None),
    preco_unit: str | None = Form(None),
    desconto: str | None = Form(None),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    dados = ItemAdicionar(
        variacao_id=variacao_id,
        qtd=qtd or None,
        qtd_caixas=qtd_caixas or None,
        preco_unit=_to_decimal(preco_unit) if preco_unit else None,
        desconto=_to_decimal(desconto),
    )
    pedido_controller.adicionar_item(db, pedido_id, dados, usuario)
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {"user": usuario, "pedido": pedido, "editavel": True}
    return templates.TemplateResponse(request, "pedidos/_itens.html", contexto)


@router.delete("/pedidos/{pedido_id}/itens/{item_id}", response_class=HTMLResponse)
def remover_item(
    request: Request,
    pedido_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    pedido_controller.remover_item(db, pedido_id, item_id, usuario)
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {"user": usuario, "pedido": pedido, "editavel": True}
    return templates.TemplateResponse(request, "pedidos/_itens.html", contexto)


@router.post("/pedidos/{pedido_id}/desconto", response_class=HTMLResponse)
def aplicar_desconto(
    request: Request,
    pedido_id: int,
    desconto: str = Form("0"),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    pedido_controller.aplicar_desconto_total(db, pedido_id, _to_decimal(desconto), usuario)
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {"user": usuario, "pedido": pedido, "editavel": True}
    return templates.TemplateResponse(request, "pedidos/_itens.html", contexto)


# ===================================================================== CICLO
@router.post("/pedidos/{pedido_id}/confirmar")
def confirmar_pedido(
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    pedido_controller.confirmar(db, pedido_id, usuario)
    return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)


@router.post("/pedidos/{pedido_id}/cancelar")
def cancelar_pedido(
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    pedido_controller.cancelar(db, pedido_id, usuario)
    return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)


@router.post("/pedidos/{pedido_id}/faturar")
def faturar_pedido(
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "financeiro")),
):
    pedido_controller.faturar(db, pedido_id, usuario)
    return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)


# ===================================================================== IMPRESSÃO
@router.get("/pedidos/{pedido_id}/imprimir", response_class=HTMLResponse)
def imprimir_pedido(
    request: Request,
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "vendedor", "financeiro")),
):
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {"user": usuario, "pedido": pedido}
    return templates.TemplateResponse(request, "pedidos/impressao_pedido.html", contexto)
