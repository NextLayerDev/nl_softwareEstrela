from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.controllers.relatorio_controller import relatorio_controller
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.usuario import Usuario

router = APIRouter()

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_VENDAS_ROLES = require_role("admin", "vendedor", "financeiro")
_FINANCEIRO_ROLES = require_role("admin", "financeiro")


def _xlsx(conteudo: bytes, nome: str) -> Response:
    return Response(
        content=conteudo,
        media_type=XLSX_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


def _vendedor_forcado(usuario: Usuario) -> int | None:
    """Vendedor só enxerga os próprios pedidos."""
    return usuario.id if usuario.perfil == "vendedor" else None


# ----------------- Hub -----------------
@router.get("/relatorios", response_class=HTMLResponse)
def hub_relatorios(
    request: Request,
    usuario: Usuario = Depends(_VENDAS_ROLES),
):
    contexto = {
        "user": usuario,
        "titulo": "Relatórios",
        "pode_financeiro": usuario.perfil in ("admin", "financeiro"),
    }
    return templates.TemplateResponse(request, "relatorios/index.html", contexto)


# ----------------- Vendas -----------------
@router.get("/relatorios/vendas", response_class=HTMLResponse)
def relatorio_vendas(
    request: Request,
    de: str = "",
    ate: str = "",
    vendedor_id: str = "",
    cliente_id: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_VENDAS_ROLES),
):
    args = {"de": de, "ate": ate, "vendedor_id": vendedor_id, "cliente_id": cliente_id}
    dados = relatorio_controller.vendas(db, args, _vendedor_forcado(usuario))
    contexto = {
        "user": usuario,
        "titulo": "Relatório de vendas",
        "dados": dados,
        "filtros": args,
    }
    return templates.TemplateResponse(request, "relatorios/vendas.html", contexto)


@router.get("/relatorios/vendas/export")
def export_vendas(
    de: str = "",
    ate: str = "",
    vendedor_id: str = "",
    cliente_id: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_VENDAS_ROLES),
):
    args = {"de": de, "ate": ate, "vendedor_id": vendedor_id, "cliente_id": cliente_id}
    conteudo = relatorio_controller.vendas_xlsx(db, args, _vendedor_forcado(usuario))
    return _xlsx(conteudo, "vendas.xlsx")


# ----------------- Curva ABC (por valor de venda; liberada aos perfis de vendas) -----------------
@router.get("/relatorios/abc", response_class=HTMLResponse)
def relatorio_abc(
    request: Request,
    de: str = "",
    ate: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_VENDAS_ROLES),
):
    args = {"de": de, "ate": ate}
    dados = relatorio_controller.curva_abc(db, args)
    contexto = {
        "user": usuario,
        "titulo": "Curva ABC",
        "dados": dados,
        "filtros": args,
    }
    return templates.TemplateResponse(request, "relatorios/abc.html", contexto)


@router.get("/relatorios/abc/export")
def export_abc(
    de: str = "",
    ate: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_VENDAS_ROLES),
):
    conteudo = relatorio_controller.curva_abc_xlsx(db, {"de": de, "ate": ate})
    return _xlsx(conteudo, "curva_abc.xlsx")


# ----------------- Valorização (SÓ admin/financeiro) -----------------
@router.get("/relatorios/valorizacao", response_class=HTMLResponse)
def relatorio_valorizacao(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_FINANCEIRO_ROLES),
):
    dados = relatorio_controller.valorizacao(db)
    contexto = {
        "user": usuario,
        "titulo": "Valorização de estoque",
        "dados": dados,
    }
    return templates.TemplateResponse(request, "relatorios/valorizacao.html", contexto)


@router.get("/relatorios/valorizacao/export")
def export_valorizacao(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_FINANCEIRO_ROLES),
):
    conteudo = relatorio_controller.valorizacao_xlsx(db)
    return _xlsx(conteudo, "valorizacao_estoque.xlsx")
