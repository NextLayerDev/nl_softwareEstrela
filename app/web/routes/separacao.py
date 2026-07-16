from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.controllers.pedido_controller import pedido_controller, progresso_separacao
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.usuario import Usuario

router = APIRouter()

_SEP = ("admin", "funcionario")


# ===================================================================== FILA
@router.get("/separacao", response_class=HTMLResponse)
def fila_separacao(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_SEP)),
):
    pedidos = pedido_controller.fila_separacao(db)
    contexto = {"user": usuario, "titulo": "Fila de separação", "pedidos": pedidos}
    return templates.TemplateResponse(request, "separacao/index.html", contexto)


# Fragmento da fila, re-buscado pelo realtime quando um pedido entra ou sai dela.
# Precisa vir ANTES de /separacao/{pedido_id}: aquela rota casa por ordem de declaração e
# tentaria converter "fila" em int (422).
@router.get("/separacao/fila", response_class=HTMLResponse)
def fragmento_fila(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_SEP)),
):
    pedidos = pedido_controller.fila_separacao(db)
    return templates.TemplateResponse(
        request, "separacao/_fila.html", {"user": usuario, "pedidos": pedidos}
    )


# ===================================================================== CONFERÊNCIA
@router.get("/separacao/{pedido_id}", response_class=HTMLResponse)
def conferencia(
    request: Request,
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_SEP)),
):
    pedido = pedido_controller.get_separacao(db, pedido_id)
    feitos, total = progresso_separacao(pedido)
    contexto = {
        "user": usuario,
        "titulo": f"Separação do pedido #{pedido.numero or pedido.id}",
        "pedido": pedido,
        "feitos": feitos,
        "total": total,
    }
    return templates.TemplateResponse(request, "separacao/conferencia.html", contexto)


@router.get("/separacao/{pedido_id}/progresso", response_class=HTMLResponse)
def fragmento_progresso(
    request: Request,
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_SEP)),
):
    """Progresso da conferência — dois tablets no mesmo pedido veem o tique um do outro."""
    pedido = pedido_controller.get_separacao(db, pedido_id)
    feitos, total = progresso_separacao(pedido)
    contexto = {"user": usuario, "pedido": pedido, "feitos": feitos, "total": total}
    return templates.TemplateResponse(request, "separacao/_progresso.html", contexto)


@router.post("/separacao/{pedido_id}/itens/{item_id}", response_class=HTMLResponse)
def marcar_item(
    request: Request,
    pedido_id: int,
    item_id: int,
    separado: bool = Form(False),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_SEP)),
):
    pedido_controller.marcar_item_separado(db, pedido_id, item_id, separado)
    pedido = pedido_controller.get_separacao(db, pedido_id)
    feitos, total = progresso_separacao(pedido)
    contexto = {"user": usuario, "pedido": pedido, "feitos": feitos, "total": total}
    return templates.TemplateResponse(request, "separacao/_progresso.html", contexto)


@router.post("/separacao/{pedido_id}/concluir")
def concluir(
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_SEP)),
):
    pedido_controller.concluir_separacao(db, pedido_id)
    return RedirectResponse(url="/separacao", status_code=303)


# ===================================================================== IMPRESSÃO
@router.get("/separacao/{pedido_id}/imprimir", response_class=HTMLResponse)
def imprimir_separacao(
    request: Request,
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_SEP)),
):
    pedido = pedido_controller.get_separacao(db, pedido_id)
    contexto = {"user": usuario, "pedido": pedido}
    return templates.TemplateResponse(request, "separacao/impressao_separacao.html", contexto)
