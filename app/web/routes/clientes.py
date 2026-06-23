from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.controllers.cliente_controller import cliente_controller
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.usuario import Usuario

router = APIRouter()


@router.get("/clientes", response_class=HTMLResponse)
def listar_clientes(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "vendedor", "financeiro")),
):
    clientes = cliente_controller.listar(db, q or None)
    contexto = {
        "user": usuario,
        "titulo": "Clientes",
        "clientes": clientes,
        "q": q,
        "pode_editar": usuario.perfil in ("admin", "vendedor"),
    }
    return templates.TemplateResponse(request, "clientes/index.html", contexto)


@router.get("/clientes/busca", response_class=HTMLResponse)
def busca_clientes(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "vendedor", "financeiro")),
):
    clientes = cliente_controller.listar(db, q or None)
    contexto = {
        "user": usuario,
        "clientes": clientes,
        "pode_editar": usuario.perfil in ("admin", "vendedor"),
    }
    return templates.TemplateResponse(request, "clientes/_linhas.html", contexto)


@router.get("/clientes/novo", response_class=HTMLResponse)
def form_novo_cliente(
    request: Request,
    usuario: Usuario = Depends(require_role("admin", "vendedor")),
):
    contexto = {"user": usuario, "titulo": "Novo cliente", "cliente": None}
    return templates.TemplateResponse(request, "clientes/form.html", contexto)


@router.get("/clientes/{cliente_id}/editar", response_class=HTMLResponse)
def form_editar_cliente(
    request: Request,
    cliente_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "vendedor")),
):
    cliente = cliente_controller.obter(db, cliente_id)
    contexto = {"user": usuario, "titulo": f"Editar {cliente.nome}", "cliente": cliente}
    return templates.TemplateResponse(request, "clientes/form.html", contexto)


@router.post("/clientes", response_class=HTMLResponse)
async def criar_cliente(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "vendedor")),
):
    form = dict(await request.form())
    cliente_controller.criar(db, form)
    return RedirectResponse(url="/clientes", status_code=303)


@router.post("/clientes/{cliente_id}", response_class=HTMLResponse)
async def atualizar_cliente(
    request: Request,
    cliente_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "vendedor")),
):
    form = dict(await request.form())
    cliente_controller.atualizar(db, cliente_id, form)
    return RedirectResponse(url="/clientes", status_code=303)


@router.post("/clientes/{cliente_id}/inativar", response_class=HTMLResponse)
async def inativar_cliente(
    request: Request,
    cliente_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "vendedor")),
):
    cliente_controller.inativar(db, cliente_id)
    return RedirectResponse(url="/clientes", status_code=303)
