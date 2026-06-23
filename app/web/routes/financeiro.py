from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.controllers.financeiro_controller import financeiro_controller
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.enums import StatusConta
from app.models.usuario import Usuario
from app.repositories.cliente_repo import cliente_repo
from app.schemas.financeiro import FORMAS_PAGAMENTO

router = APIRouter()

_ACESSO = require_role("admin", "financeiro")


def _ctx_contas(db: Session, args: dict, usuario: Usuario) -> dict:
    contas = financeiro_controller.listar(db, args)
    return {
        "user": usuario,
        "contas": contas,
        "formas": FORMAS_PAGAMENTO,
        "status_opcoes": [s.value for s in StatusConta],
        "filtros": args,
    }


@router.get("/financeiro", response_class=HTMLResponse)
def listar_contas(
    request: Request,
    status: str = "",
    cliente_id: str = "",
    venc_de: str = "",
    venc_ate: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_ACESSO),
):
    args = {
        "status": status,
        "cliente_id": cliente_id,
        "venc_de": venc_de,
        "venc_ate": venc_ate,
    }
    contexto = _ctx_contas(db, args, usuario)
    contexto["titulo"] = "Financeiro"
    contexto["clientes"] = cliente_repo.listar(db, incluir_inativos=True, limit=1000)
    contexto["recebidas_hoje"] = financeiro_controller.recebidas_hoje(db)
    return templates.TemplateResponse(request, "financeiro/index.html", contexto)


@router.get("/financeiro/contas", response_class=HTMLResponse)
def busca_contas(
    request: Request,
    status: str = "",
    cliente_id: str = "",
    venc_de: str = "",
    venc_ate: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_ACESSO),
):
    args = {
        "status": status,
        "cliente_id": cliente_id,
        "venc_de": venc_de,
        "venc_ate": venc_ate,
    }
    return templates.TemplateResponse(
        request, "financeiro/_linhas.html", _ctx_contas(db, args, usuario)
    )


@router.post("/financeiro/contas/{conta_id}/baixar", response_class=HTMLResponse)
async def baixar_conta(
    request: Request,
    conta_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_ACESSO),
):
    form = dict(await request.form())
    financeiro_controller.baixar(db, conta_id, form, usuario.id)
    return RedirectResponse(url="/financeiro", status_code=303)


@router.post("/financeiro/marcar-atrasados", response_class=HTMLResponse)
async def marcar_atrasados(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    financeiro_controller.marcar_atrasados(db)
    return RedirectResponse(url="/financeiro", status_code=303)
