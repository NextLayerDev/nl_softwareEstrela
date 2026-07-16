"""A regra única de "variação em atenção" (abaixo do mínimo).

A regra existia duplicada no repositório de estoque e no dashboard. Como o realtime precisa
dela numa terceira forma (detectar a *transição* para abaixo do mínimo e só então avisar),
ela passa a morar aqui, nas duas formas necessárias: predicado Python (objeto já carregado)
e cláusula SQL (contagens/listagens).

Módulo folha de propósito: importa só model e enums, para nenhum consumidor criar ciclo.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, or_

from app.models.enums import EstoqueModo, RotuloAprox
from app.models.produto import ProdutoVariacao


def abaixo_minimo(variacao: ProdutoVariacao) -> bool:
    """Predicado em Python — usado para detectar a transição e disparar o alerta."""
    if variacao.estoque_modo == EstoqueModo.EXATO:
        return variacao.estoque_fisico <= variacao.estoque_minimo
    return variacao.rotulo_aprox in (RotuloAprox.POUCO, RotuloAprox.ACABOU)


def clausula_abaixo_minimo() -> ColumnElement[bool]:
    """A mesma regra como cláusula SQL — usada nas queries de alerta/KPI."""
    return or_(
        (ProdutoVariacao.estoque_modo == EstoqueModo.EXATO)
        & (ProdutoVariacao.estoque_fisico <= ProdutoVariacao.estoque_minimo),
        ProdutoVariacao.rotulo_aprox.in_([RotuloAprox.POUCO, RotuloAprox.ACABOU]),
    )
