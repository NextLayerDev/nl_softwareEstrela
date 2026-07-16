"""Aba "Status do Deploy" — admin.

Read-only por enquanto: mostra o que está rodando, a saúde do servidor, o histórico e o
estado do CI. Os botões de atualizar/reverter entram junto com o agente do host.

Nenhuma rota aqui fala com a internet: o card de CI lê o cache no Postgres, preenchido
pelo job do APScheduler.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.controllers.deploy_controller import deploy_controller
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.usuario import Usuario

router = APIRouter()


@router.get("/deploy", response_class=HTMLResponse)
def pagina_deploy(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("dev")),
):
    contexto = {
        "user": usuario,
        "titulo": "Status do Deploy",
        **deploy_controller.pagina(db),
    }
    return templates.TemplateResponse(request, "deploy/index.html", contexto)


@router.get("/deploy/status", response_class=HTMLResponse)
def fragmento_status(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("dev")),
):
    """Bloco do deploy em andamento. Repicado de perto (3s) durante uma atualização."""
    contexto = {"user": usuario, **deploy_controller.status(db)}
    return templates.TemplateResponse(request, "deploy/_status.html", contexto)


@router.get("/deploy/saude", response_class=HTMLResponse)
def fragmento_saude(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("dev")),
):
    """Cadência própria (30s): as sondas de disco e backup são caras demais para 3s."""
    contexto = {"user": usuario, **deploy_controller.saude(db)}
    return templates.TemplateResponse(request, "deploy/_saude.html", contexto)


@router.get("/deploy/historico", response_class=HTMLResponse)
def fragmento_historico(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("dev")),
):
    contexto = {"user": usuario, **deploy_controller.historico(db)}
    return templates.TemplateResponse(request, "deploy/_historico.html", contexto)


@router.get("/deploy/ci", response_class=HTMLResponse)
def fragmento_ci(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("dev")),
):
    """Só lê o cache: nunca espera a rede (ver app/integracoes/github.py)."""
    contexto = {"user": usuario, **deploy_controller.ci(db)}
    return templates.TemplateResponse(request, "deploy/_ci.html", contexto)


@router.get("/deploy/{deploy_id}/log", response_class=HTMLResponse)
def fragmento_log(
    request: Request,
    deploy_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("dev")),
):
    from app.repositories.deploy_repo import deploy_repo

    contexto = {"user": usuario, "deploy": deploy_repo.get(db, deploy_id)}
    return templates.TemplateResponse(request, "deploy/_log.html", contexto)
