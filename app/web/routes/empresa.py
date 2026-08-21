from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.controllers.empresa_controller import empresa_controller
from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.usuario import Usuario
from app.web.routes._flash import redirect_ok

router = APIRouter()


@router.get("/empresa", response_class=HTMLResponse)
def form_empresa(
    request: Request,
    ok: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    contexto = {
        "user": usuario,
        "titulo": "Dados da empresa",
        "empresa": empresa_controller.obter(db),
        "mensagem_ok": ok or None,
    }
    return templates.TemplateResponse(request, "empresa/form.html", contexto)


@router.post("/empresa", response_class=HTMLResponse)
async def salvar_empresa(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    form = dict(await request.form())
    empresa_controller.salvar(db, form)
    return redirect_ok("/empresa", "Dados da empresa salvos com sucesso.")


@router.post("/empresa/logo", response_class=HTMLResponse)
async def enviar_logo(
    arquivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    """Logo da capa do catálogo. Guardado como PNG, preservando transparência.

    Não reusa o helper das fotos de variação de propósito: aquele converte para RGB e
    salva JPEG, o que achataria o fundo transparente do logo em PRETO.
    """
    from app.core.imagens import salvar_logo

    conteudo = await arquivo.read()
    try:
        empresa_controller.salvar_logo(db, salvar_logo(conteudo))
    except ValueError as exc:
        raise RegraNegocioError(str(exc)) from exc
    return redirect_ok("/empresa", "Logo salvo com sucesso.")


@router.post("/empresa/logo/remover", response_class=HTMLResponse)
def remover_logo(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    empresa_controller.salvar_logo(db, None)
    return redirect_ok("/empresa", "Logo removido.")


@router.get("/empresa/logo")
def servir_logo(db: Session = Depends(get_db), usuario: Usuario = Depends(require_role("admin"))):
    """Serve os bytes do logo. O catálogo NÃO usa esta rota — ele embute a imagem."""
    empresa = empresa_controller.obter(db)
    if not empresa or not empresa.logo_dados:
        raise NaoEncontradoError("Logo não cadastrado.")
    return Response(empresa.logo_dados, media_type="image/png")
