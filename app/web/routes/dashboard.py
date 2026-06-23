from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.templates import templates
from app.deps.auth import get_current_user
from app.deps.db import get_db
from app.models.enums import EstoqueModo, RotuloAprox
from app.models.produto import Produto, ProdutoVariacao
from app.models.usuario import Usuario

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    total_produtos = db.scalar(select(func.count(Produto.id))) or 0

    # Alertas de estoque: exatos no/abaixo do mínimo OU aproximados POUCO/ACABOU.
    alertas = (
        db.scalar(
            select(func.count(ProdutoVariacao.id)).where(
                or_(
                    (ProdutoVariacao.estoque_modo == EstoqueModo.EXATO)
                    & (ProdutoVariacao.estoque_fisico <= ProdutoVariacao.estoque_minimo),
                    ProdutoVariacao.rotulo_aprox.in_([RotuloAprox.POUCO, RotuloAprox.ACABOU]),
                )
            )
        )
        or 0
    )

    contexto = {
        "request": request,
        "user": usuario,
        "titulo": "Painel",
        "kpis": {
            "total_produtos": total_produtos,
            "alertas_estoque": alertas,
        },
    }
    return templates.TemplateResponse(request, "dashboard.html", contexto)
