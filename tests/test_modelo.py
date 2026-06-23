"""Testes do modelo de dados: relacionamentos navegáveis e saldo derivado."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.categoria import Categoria
from app.models.enums import EstoqueModo
from app.models.produto import Produto, ProdutoVariacao


def test_produto_variacoes_navegavel(db: Session) -> None:
    cat = Categoria(nome="Teste Canetas")
    db.add(cat)
    db.flush()
    produto = Produto(
        codigo="TST-1",
        descricao="CANETA TESTE",
        categoria_id=cat.id,
        preco_pouca_qtd=Decimal("1.50"),
        preco_muita_qtd=Decimal("1.20"),
    )
    produto.variacoes.append(
        ProdutoVariacao(cor="AZUL", estoque_modo=EstoqueModo.EXATO, estoque_fisico=100)
    )
    produto.variacoes.append(
        ProdutoVariacao(cor="VERDE", estoque_modo=EstoqueModo.EXATO, estoque_fisico=50)
    )
    db.add(produto)
    db.flush()

    recarregado = db.get(Produto, produto.id)
    assert recarregado is not None
    assert {v.cor for v in recarregado.variacoes} == {"AZUL", "VERDE"}
    assert recarregado.categoria.nome == "Teste Canetas"


def test_disponivel_e_derivado_de_fisico_menos_reservado(db: Session) -> None:
    variacao = ProdutoVariacao(
        cor="", estoque_modo=EstoqueModo.EXATO, estoque_fisico=200, estoque_reservado=30
    )
    assert variacao.disponivel == 170
