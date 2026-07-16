from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.controllers.usuario_controller import usuario_controller
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.enums import Perfil
from app.models.usuario import Usuario
from app.web.routes._flash import redirect_ok

router = APIRouter()

_PERFIS = [p.value for p in Perfil]


@router.get("/usuarios", response_class=HTMLResponse)
def listar_usuarios(
    request: Request,
    ok: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    usuarios = usuario_controller.listar(db)
    contexto = {
        "user": usuario,
        "titulo": "Usuários",
        "usuarios": usuarios,
        "mensagem_ok": ok or None,
    }
    return templates.TemplateResponse(request, "usuarios/index.html", contexto)


# Antes de qualquer rota /usuarios/{usuario_id}: senão "lista" seria lido como id (422).
@router.get("/usuarios/lista", response_class=HTMLResponse)
def fragmento_lista_usuarios(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    """Corpo da tabela, re-buscado pelo realtime quando um usuário é criado ou alterado."""
    contexto = {"user": usuario, "usuarios": usuario_controller.listar(db)}
    return templates.TemplateResponse(request, "usuarios/_linhas.html", contexto)


@router.get("/usuarios/novo", response_class=HTMLResponse)
def form_novo_usuario(
    request: Request,
    usuario: Usuario = Depends(require_role("admin")),
):
    contexto = {
        "user": usuario,
        "titulo": "Novo usuário",
        "usuario_edit": None,
        "perfis": _PERFIS,
    }
    return templates.TemplateResponse(request, "usuarios/form.html", contexto)


@router.get("/usuarios/{usuario_id}/editar", response_class=HTMLResponse)
def form_editar_usuario(
    request: Request,
    usuario_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    alvo = usuario_controller.obter(db, usuario_id)
    contexto = {
        "user": usuario,
        "titulo": f"Editar {alvo.nome}",
        "usuario_edit": alvo,
        "perfis": _PERFIS,
    }
    return templates.TemplateResponse(request, "usuarios/form.html", contexto)


@router.post("/usuarios", response_class=HTMLResponse)
async def criar_usuario(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    form = dict(await request.form())
    usuario_controller.criar(db, form)
    return redirect_ok("/usuarios", "Usuário cadastrado com sucesso.")


@router.post("/usuarios/{usuario_id}", response_class=HTMLResponse)
async def atualizar_usuario(
    request: Request,
    usuario_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    form = dict(await request.form())
    usuario_controller.atualizar(db, usuario_id, form)
    return redirect_ok("/usuarios", "Usuário atualizado com sucesso.")


@router.post("/usuarios/{usuario_id}/reset-senha", response_class=HTMLResponse)
def resetar_senha_usuario(
    request: Request,
    usuario_id: int,
    nova_senha: str = Form(...),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    usuario_controller.resetar_senha(db, usuario_id, nova_senha)
    return redirect_ok("/usuarios", "Senha redefinida com sucesso.")
