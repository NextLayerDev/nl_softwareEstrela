from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.controllers.usuario_controller import usuario_controller
from app.core.errors import DominioError
from app.core.templates import templates
from app.deps.auth import get_current_user
from app.deps.db import get_db
from app.models.usuario import Usuario

router = APIRouter()


@router.get("/perfil", response_class=HTMLResponse)
def meu_perfil(request: Request, usuario: Usuario = Depends(get_current_user)):
    """ "Meu perfil": quem está logado e as preferências dele."""
    return templates.TemplateResponse(
        request, "perfil/index.html", {"user": usuario, "titulo": "Meu perfil"}
    )


@router.post("/perfil/preferencias")
def salvar_preferencia(
    campo: str = Form(...),
    valor: str = Form(...),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Grava UMA preferência do próprio usuário. Responde JSON (quem chama é o Alpine da
    tela de perfil e o alternador Lista | Planilha) e 200 com `ok: false` na recusa."""
    try:
        usuario_controller.salvar_preferencias(db, usuario, campo, valor)
    except DominioError as exc:
        return JSONResponse({"ok": False, "erro": exc.mensagem})
    return JSONResponse({"ok": True})
