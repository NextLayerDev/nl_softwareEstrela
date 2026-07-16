from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.controllers.cliente_controller import cliente_controller
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.enums import CATEGORIA_CLIENTE_INFO, e_admin, tem_perfil
from app.models.usuario import Usuario
from app.web.routes._flash import redirect_ok

router = APIRouter()


@router.get("/clientes", response_class=HTMLResponse)
def listar_clientes(
    request: Request,
    q: str = "",
    ok: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "vendedor", "financeiro")),
):
    clientes = cliente_controller.listar(db, q or None)
    contexto = {
        "user": usuario,
        "titulo": "Clientes",
        "clientes": clientes,
        "q": q,
        "pode_editar": tem_perfil(usuario.perfil, "admin", "vendedor"),
        "pode_excluir": e_admin(usuario.perfil),
        "categorias": CATEGORIA_CLIENTE_INFO,
        "mensagem_ok": ok or None,
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
        "pode_editar": tem_perfil(usuario.perfil, "admin", "vendedor"),
        "pode_excluir": e_admin(usuario.perfil),
        "categorias": CATEGORIA_CLIENTE_INFO,
    }
    return templates.TemplateResponse(request, "clientes/_linhas.html", contexto)


@router.get("/clientes/novo", response_class=HTMLResponse)
def form_novo_cliente(
    request: Request,
    usuario: Usuario = Depends(require_role("admin", "vendedor")),
):
    contexto = {
        "user": usuario,
        "titulo": "Novo cliente",
        "cliente": None,
        "categorias": CATEGORIA_CLIENTE_INFO,
    }
    return templates.TemplateResponse(request, "clientes/form.html", contexto)


@router.get("/clientes/{cliente_id}/editar", response_class=HTMLResponse)
def form_editar_cliente(
    request: Request,
    cliente_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "vendedor")),
):
    cliente = cliente_controller.obter(db, cliente_id)
    contexto = {
        "user": usuario,
        "titulo": f"Editar {cliente.nome}",
        "cliente": cliente,
        "categorias": CATEGORIA_CLIENTE_INFO,
    }
    return templates.TemplateResponse(request, "clientes/form.html", contexto)


@router.post("/clientes", response_class=HTMLResponse)
async def criar_cliente(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "vendedor")),
):
    form = dict(await request.form())
    cliente_controller.criar(db, form)
    return redirect_ok("/clientes", "Cliente cadastrado com sucesso.")


@router.post("/clientes/{cliente_id}", response_class=HTMLResponse)
async def atualizar_cliente(
    request: Request,
    cliente_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "vendedor")),
):
    form = dict(await request.form())
    cliente_controller.atualizar(db, cliente_id, form)
    return redirect_ok("/clientes", "Cliente atualizado com sucesso.")


@router.post("/clientes/{cliente_id}/inativar", response_class=HTMLResponse)
async def inativar_cliente(
    request: Request,
    cliente_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin", "vendedor")),
):
    cliente_controller.inativar(db, cliente_id)
    return redirect_ok("/clientes", "Cliente inativado.")


@router.post("/clientes/{cliente_id}/excluir", response_class=HTMLResponse)
async def excluir_cliente(
    request: Request,
    cliente_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    cliente_controller.excluir(db, cliente_id)
    return redirect_ok("/clientes", "Cliente excluído.")
