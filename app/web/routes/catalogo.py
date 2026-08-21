"""Catálogo Inteligente: o documento A4 que a loja imprime ou manda em PDF."""

from __future__ import annotations

import base64
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.controllers.catalogo_controller import catalogo_controller
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.usuario import Usuario
from app.services.empresa_service import empresa_service

router = APIRouter()

_VE = ("admin", "vendedor")


_MESES = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def _render(request: Request, db: Session, usuario: Usuario, categoria_id: int | None) -> str:
    doc = catalogo_controller.montar(db, categoria_id)
    empresa = empresa_service.obter(db)
    hoje = date.today()
    contexto = {
        "user": usuario,
        "titulo": "Catálogo",
        "doc": doc,
        "empresa": empresa,
        # O logo vai EMBUTIDO nos dois caminhos: o WeasyPrint não conseguiria buscá-lo
        # (rota relativa e autenticada), e embutir também no HTML mantém uma renderização
        # só — o que se vê na tela é exatamente o que sai no PDF.
        "logo": _logo_embutido(empresa),
        "hoje_por_extenso": f"{hoje.day} de {_MESES[hoje.month - 1]} de {hoje.year}",
    }
    return templates.get_template("catalogo/imprimir.html").render(request=request, **contexto)


@router.get("/catalogo/imprimir", response_class=HTMLResponse)
def imprimir_catalogo(
    request: Request,
    categoria_id: int | None = None,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_VE)),
):
    """O catálogo na tela, pronto para o Ctrl+P.

    HTML com `@media print` e não PDF gerado no servidor: é o padrão que as três telas
    de impressão do sistema já usam, e prévia e impressão passam a ser a MESMA página —
    o que o operador vê é o que sai.
    """
    return HTMLResponse(_render(request, db, usuario, categoria_id))


@router.get("/catalogo/imprimir.pdf")
def catalogo_pdf(
    request: Request,
    categoria_id: int | None = None,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_VE)),
):
    """O mesmo documento como arquivo, para mandar no WhatsApp.

    O import do WeasyPrint é PREGUIÇOSO de propósito: ele está no pyproject mas nunca
    foi importado por `app/` — só por `scripts/`. Promovê-lo ao topo faria o app inteiro
    depender de Pango/Cairo estarem no lugar certo numa máquina offline que ninguém
    monitora. Aqui, uma biblioteca nativa faltando derruba UMA rota, não o sistema.
    """
    html = _render(request, db, usuario, categoria_id)
    try:
        from weasyprint import HTML  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return HTMLResponse(
            "<p>Geração de PDF indisponível nesta máquina. "
            'Use <a href="/catalogo/imprimir">Imprimir</a> e salve como PDF.</p>',
            status_code=503,
        )
    pdf = HTML(string=html, base_url=str(request.base_url)).write_pdf()
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="catalogo.pdf"'},
    )


def _logo_embutido(empresa) -> str | None:
    if not getattr(empresa, "logo_dados", None):
        return None
    return "data:image/png;base64," + base64.b64encode(empresa.logo_dados).decode()
