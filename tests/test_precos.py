"""Testes da tabela de preço por quantidade (`app/core/precos.py`).

Puros: sem banco, sem app. É a regra que decide quanto o cliente paga, então ela é
testada isolada — quando um pedido sai com o preço errado, é aqui que se olha primeiro.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import RegraNegocioError
from app.core.precos import (
    MAX_FAIXAS,
    Faixa,
    avisos_faixas,
    faixa_para,
    normalizar_faixas,
    proxima_faixa,
    rotulo_faixa,
    rotulo_para,
    validar_faixas,
)


def _f(min_qtd: int, preco: str) -> Faixa:
    return Faixa(min_qtd=min_qtd, preco=Decimal(preco))


TABELA = [_f(1, "10.00"), _f(10, "8.00"), _f(50, "6.50")]


# --------------------------------------------------------------- normalizar
def test_normalizar_ordena_por_quantidade():
    faixas = normalizar_faixas([(50, "6.50"), (1, "10.00"), (10, "8.00")])
    assert [f.min_qtd for f in faixas] == [1, 10, 50]


def test_normalizar_aceita_par_objeto_e_faixa_pronta():
    """Vem do formulário (par), do ORM (objeto) e de dentro de casa (Faixa)."""

    class DoOrm:
        min_qtd = 10
        preco = Decimal("8.00")

    faixas = normalizar_faixas([(1, "10.00"), DoOrm(), _f(50, "6.50")])
    assert [(f.min_qtd, f.preco) for f in faixas] == [
        (1, Decimal("10.00")),
        (10, Decimal("8.00")),
        (50, Decimal("6.50")),
    ]


def test_normalizar_preco_nao_vira_float():
    """`Decimal(8.0)` de um float é 8.000000000000000444… — dinheiro não passa por float."""
    (faixa,) = normalizar_faixas([(1, 8.0)])
    assert faixa.preco == Decimal("8")
    assert str(faixa.preco) == "8.0"


def test_normalizar_descarta_lixo():
    faixas = normalizar_faixas(
        [(1, "10.00"), (0, "5.00"), (-3, "5.00"), (5, "-1.00"), ("x", "y"), None]
    )
    assert [f.min_qtd for f in faixas] == [1]


def test_normalizar_quantidade_repetida_vence_a_primeira():
    """O resultado não pode depender da ordem em que a lista chegou."""
    faixas = normalizar_faixas([(10, "8.00"), (10, "7.00")])
    assert [(f.min_qtd, f.preco) for f in faixas] == [(10, Decimal("8.00"))]


# --------------------------------------------------------------- faixa_para
@pytest.mark.parametrize(
    ("qtd", "esperado"),
    [
        (1, "10.00"),  # exatamente na primeira
        (9, "10.00"),
        (10, "8.00"),  # a fronteira PERTENCE à faixa
        (49, "8.00"),
        (50, "6.50"),
        (5000, "6.50"),  # acima da última, vale a última
    ],
)
def test_faixa_para_escolhe_a_de_maior_min_qtd(qtd, esperado):
    assert faixa_para(TABELA, qtd).preco == Decimal(esperado)


def test_faixa_para_abaixo_da_primeira_devolve_none():
    """Tabela começando em 10: quem leva 3 cai na regra de varejo/atacado."""
    assert faixa_para([_f(10, "8.00")], 3) is None


def test_faixa_para_sem_tabela_devolve_none():
    assert faixa_para([], 100) is None


# --------------------------------------------------------------- proxima_faixa
def test_proxima_faixa_e_o_empurraozinho():
    assert proxima_faixa(TABELA, 5).min_qtd == 10
    assert proxima_faixa(TABELA, 10).min_qtd == 50
    assert proxima_faixa(TABELA, 50) is None


# --------------------------------------------------------------- rótulos
def test_rotulo_da_faixa():
    assert rotulo_faixa(TABELA, 0) == "1 a 9 un"
    assert rotulo_faixa(TABELA, 1) == "10 a 49 un"
    assert rotulo_faixa(TABELA, 2) == "50+ un"
    assert rotulo_faixa(TABELA, 99) == ""


def test_rotulo_para_a_quantidade():
    assert rotulo_para(TABELA, 12) == "10 a 49 un"
    assert rotulo_para([_f(10, "8.00")], 3) == ""


# --------------------------------------------------------------- validação
def test_validar_aceita_tabela_vazia():
    """Vazia é "sem tabela" — o estado de todo produto que ninguém revisou."""
    validar_faixas([])


def test_validar_recusa_quantidade_repetida():
    with pytest.raises(RegraNegocioError) as exc:
        validar_faixas([_f(1, "10.00"), _f(10, "8.00"), _f(10, "7.00")])
    assert "duas faixas começando em 10" in str(exc.value)


def test_validar_exige_a_faixa_de_uma_unidade():
    """Sem ela a tabela tem buraco: ninguém sabe o preço de quem leva 1."""
    with pytest.raises(RegraNegocioError) as exc:
        validar_faixas([_f(10, "8.00"), _f(50, "6.50")])
    assert "1 un" in str(exc.value)


def test_validar_recusa_acima_do_teto():
    faixas = [_f(i or 1, "5.00") for i in range(MAX_FAIXAS + 1)]
    with pytest.raises(RegraNegocioError):
        validar_faixas(normalizar_faixas([(f.min_qtd, f.preco) for f in faixas]) + [_f(999, "1")])


# --------------------------------------------------------------- avisos
def test_aviso_de_faixa_mais_cara_que_a_anterior():
    """Estranho, mas pode ser de propósito — avisa, não recusa."""
    avisos = avisos_faixas([_f(1, "10.00"), _f(10, "12.00")])
    assert avisos == ["A faixa de 10 un está mais cara que a anterior."]


def test_tabela_decrescente_nao_gera_aviso():
    assert avisos_faixas(TABELA) == []
