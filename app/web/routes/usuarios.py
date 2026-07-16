from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.controllers.usuario_controller import usuario_controller
from app.core.errors import NaoEncontradoError, PermissaoNegadaError
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.enums import PERFIS_ATRIBUIVEIS, Perfil, e_dev
from app.models.usuario import Usuario
from app.web.routes._flash import redirect_ok

router = APIRouter()

# Sem o `dev`: se ele aparecesse no select, o admin da empresa criaria um usuário dev e
# se auto-promoveria para a tela de manutenção. Ver PERFIS_ATRIBUIVEIS em models/enums.py.
_PERFIS = PERFIS_ATRIBUIVEIS


def _visiveis(db: Session, solicitante: Usuario) -> list[Usuario]:
    """Usuários dev não aparecem para quem não é dev — nem na lista, nem no realtime."""
    usuarios = usuario_controller.listar(db)
    if e_dev(solicitante.perfil):
        return usuarios
    return [u for u in usuarios if not e_dev(u.perfil)]


@router.get("/usuarios", response_class=HTMLResponse)
def listar_usuarios(
    request: Request,
    ok: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    contexto = {
        "user": usuario,
        "titulo": "Usuários",
        "usuarios": _visiveis(db, usuario),
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
    contexto = {"user": usuario, "usuarios": _visiveis(db, usuario)}
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
    _barra_alvo_dev(db, usuario_id, usuario)
    alvo = usuario_controller.obter(db, usuario_id)
    contexto = {
        "user": usuario,
        "titulo": f"Editar {alvo.nome}",
        "usuario_edit": alvo,
        # Um dev editando alguém pode atribuir qualquer perfil, inclusive dev.
        "perfis": [p.value for p in Perfil] if e_dev(usuario.perfil) else _PERFIS,
    }
    return templates.TemplateResponse(request, "usuarios/form.html", contexto)


def _barra_escalacao(form: dict, solicitante: Usuario) -> None:
    """Só um dev cria/promove outro dev.

    Tirar o `dev` do <select> é cosmético: o form é um POST, e um admin com curl
    mandaria perfil=dev e se promoveria para a tela que reinicia o sistema.
    """
    if form.get("perfil") == Perfil.DEV.value and not e_dev(solicitante.perfil):
        raise PermissaoNegadaError("Perfil inválido.")


def _barra_alvo_dev(db: Session, usuario_id: int, solicitante: Usuario) -> None:
    """Um usuário dev só é mexido por outro dev.

    Sem isto, o admin resetaria a senha do dev e entraria como ele: a tela de
    manutenção cairia no colo de quem opera a empresa.
    """
    if e_dev(solicitante.perfil):
        return
    alvo = usuario_controller.obter(db, usuario_id)
    if alvo is not None and e_dev(alvo.perfil):
        raise NaoEncontradoError("Usuário não encontrado.")


@router.post("/usuarios", response_class=HTMLResponse)
async def criar_usuario(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    form = dict(await request.form())
    _barra_escalacao(form, usuario)
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
    _barra_escalacao(form, usuario)
    _barra_alvo_dev(db, usuario_id, usuario)
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
    # Sem esta linha, o admin resetaria a senha do dev e entraria como ele — o caminho
    # mais curto para a tela de manutenção.
    _barra_alvo_dev(db, usuario_id, usuario)
    usuario_controller.resetar_senha(db, usuario_id, nova_senha)
    return redirect_ok("/usuarios", "Senha redefinida com sucesso.")
