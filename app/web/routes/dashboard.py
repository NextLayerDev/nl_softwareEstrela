from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.estoque_alertas import clausula_abaixo_minimo
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.conta_receber import ContaReceber
from app.models.enums import StatusConta, StatusPedido, tem_perfil
from app.models.pedido import Pedido
from app.models.produto import Produto, ProdutoVariacao
from app.models.usuario import Usuario

router = APIRouter()

_PERFIS = ("admin", "vendedor", "financeiro", "funcionario")


def _montar_contexto(request: Request, db: Session, usuario: Usuario) -> dict:
    """Monta os KPIs do painel. Compartilhado pela página e pelo fragmento do realtime."""
    hoje = date.today()
    so_proprios = usuario.perfil == "vendedor"

    def filtro_vendedor(stmt):
        return stmt.where(Pedido.vendedor_id == usuario.id) if so_proprios else stmt

    total_produtos = db.scalar(select(func.count(Produto.id))) or 0

    # Fila de separação (KPI relevante para o funcionário, no lugar da receita).
    pedidos_separar = (
        db.scalar(
            select(func.count(Pedido.id)).where(
                Pedido.status.in_([StatusPedido.CONFIRMADO, StatusPedido.SEPARACAO])
            )
        )
        or 0
    )

    alertas = db.scalar(select(func.count(ProdutoVariacao.id)).where(clausula_abaixo_minimo())) or 0

    pedidos_hoje = (
        db.scalar(
            filtro_vendedor(select(func.count(Pedido.id))).where(
                func.date(Pedido.criado_em) == hoje
            )
        )
        or 0
    )
    vendas_hoje = db.scalar(
        filtro_vendedor(select(func.coalesce(func.sum(Pedido.total), 0)))
        .where(Pedido.status.in_([StatusPedido.FATURADO, StatusPedido.ENTREGUE]))
        .where(func.date(Pedido.faturado_em) == hoje)
    ) or Decimal("0")

    # Financeiro só para admin/financeiro.
    contas_pendentes = contas_atrasadas = 0
    if tem_perfil(usuario.perfil, "admin", "financeiro"):
        contas_pendentes = (
            db.scalar(
                select(func.count(ContaReceber.id)).where(
                    ContaReceber.status == StatusConta.PENDENTE
                )
            )
            or 0
        )
        contas_atrasadas = (
            db.scalar(
                select(func.count(ContaReceber.id)).where(
                    ContaReceber.status == StatusConta.ATRASADO
                )
            )
            or 0
        )

    ultimos = list(
        db.scalars(filtro_vendedor(select(Pedido)).order_by(Pedido.criado_em.desc()).limit(5))
    )

    # Série de vendas faturadas dos últimos 7 dias (para gráfico simples).
    inicio = hoje - timedelta(days=6)
    linhas = db.execute(
        filtro_vendedor(
            select(func.date(Pedido.faturado_em), func.coalesce(func.sum(Pedido.total), 0))
        )
        .where(Pedido.status.in_([StatusPedido.FATURADO, StatusPedido.ENTREGUE]))
        .where(func.date(Pedido.faturado_em) >= inicio)
        .group_by(func.date(Pedido.faturado_em))
    ).all()
    por_dia = {str(d): float(v) for d, v in linhas if d is not None}
    serie = [
        {
            "dia": (inicio + timedelta(days=i)).strftime("%d/%m"),
            "valor": por_dia.get(str(inicio + timedelta(days=i)), 0.0),
        }
        for i in range(7)
    ]
    max_serie = max((s["valor"] for s in serie), default=0.0) or 1.0

    return {
        "user": usuario,
        "titulo": "Painel",
        "kpis": {
            "total_produtos": total_produtos,
            "alertas_estoque": alertas,
            "pedidos_hoje": pedidos_hoje,
            "vendas_hoje": vendas_hoje,
            "contas_pendentes": contas_pendentes,
            "contas_atrasadas": contas_atrasadas,
            "pedidos_separar": pedidos_separar,
        },
        "ultimos": ultimos,
        "serie": serie,
        "max_serie": max_serie,
        "mostra_financeiro": tem_perfil(usuario.perfil, "admin", "financeiro"),
        # Funcionário (estoque) não vê receita — mostra a fila de separação no lugar.
        "mostra_vendas": usuario.perfil != "funcionario",
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_PERFIS)),
):
    return templates.TemplateResponse(
        request, "dashboard.html", _montar_contexto(request, db, usuario)
    )


@router.get("/painel/cartoes", response_class=HTMLResponse)
def cartoes_painel(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_PERFIS)),
):
    """Fragmento dos KPIs + gráfico + últimos pedidos, re-buscado pelo realtime.

    Reusa o mesmo contexto da página, então o RBAC (receita/financeiro por perfil) vale igual.
    """
    return templates.TemplateResponse(
        request, "dashboard/_kpis.html", _montar_contexto(request, db, usuario)
    )
