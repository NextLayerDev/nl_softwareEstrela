from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.controllers.empresa_controller import empresa_controller
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
