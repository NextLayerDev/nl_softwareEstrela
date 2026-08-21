"""Tabela de preço por quantidade — a ÚNICA fonte da regra das faixas.

A regra, em uma frase: **se o produto tem tabela, a faixa que vale para a quantidade
manda; se não tem — ou se a quantidade fica abaixo da menor faixa —, vale a regra de
sempre (atacado a partir do corte, senão varejo).**

Vive em `core/` e não em `services/` porque não recebe `Session` e não orquestra nada:
é conta pura, na mesma prateleira de `numeros_br.py` e `codigos.py`. Dois lugares
precisam dela — o `produto_service`, para validar na hora de salvar, e o
`pedido_service`, para precificar — e pôr a regra dentro de um deles obrigaria o outro
a importar um service irmão só para fazer uma conta.

`validar_faixas` LEVANTA e `avisos_faixas` DEVOLVE TEXTO de propósito: faixa duplicada e
falta da faixa de 1 un são erro (a tabela fica ambígua ou com buraco), enquanto uma
faixa mais cara que a anterior é só estranha — pode ser intencional, e recusar o save
por causa disso seria o sistema achando que sabe mais que o dono do preço.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.core.errors import RegraNegocioError

# Teto de faixas por produto. Acima disso a tabela vira uma parede que ninguém lê, e o
# vendedor volta a digitar o preço na mão — que é justamente o que a tabela evita.
MAX_FAIXAS = 10


@dataclass(frozen=True)
class Faixa:
    """A partir de `min_qtd` unidades, cada uma sai por `preco`."""

    min_qtd: int
    preco: Decimal


def normalizar_faixas(brutas: Iterable[object]) -> list[Faixa]:
    """Limpa e ordena o que veio do formulário (ou do banco) em faixas utilizáveis.

    Descarta o que não é número, quantidade menor que 1 e preço negativo. Em `min_qtd`
    repetido vence a PRIMEIRA ocorrência, para o resultado não depender da ordem em que
    a lista chegou — a validação recusa o caso depois, mas quem só quer precificar não
    pode receber uma resposta diferente a cada chamada.
    """
    vistos: set[int] = set()
    saida: list[Faixa] = []
    for bruta in brutas:
        min_qtd, preco = _ler(bruta)
        if min_qtd is None or preco is None:
            continue
        if min_qtd < 1 or preco < 0:
            continue
        if min_qtd in vistos:
            continue
        vistos.add(min_qtd)
        saida.append(Faixa(min_qtd=min_qtd, preco=preco))
    return sorted(saida, key=lambda f: f.min_qtd)


def _ler(bruta: object) -> tuple[int | None, Decimal | None]:
    """Aceita a Faixa pronta, o objeto do ORM e o par (min_qtd, preco)."""
    if isinstance(bruta, Faixa):
        return bruta.min_qtd, bruta.preco
    if isinstance(bruta, (tuple, list)) and len(bruta) == 2:
        cru_qtd, cru_preco = bruta
    else:
        cru_qtd = getattr(bruta, "min_qtd", None)
        cru_preco = getattr(bruta, "preco", None)
    try:
        min_qtd = int(cru_qtd)  # type: ignore[arg-type]
        # str() antes de Decimal: `Decimal(8.0)` de um float vira 8.0000000000000004…,
        # e dinheiro que sai torto do parser sai torto do pedido.
        preco = Decimal(str(cru_preco))
    except (TypeError, ValueError, InvalidOperation):
        return None, None
    return min_qtd, preco


def faixa_para(faixas: Sequence[Faixa], qtd: int) -> Faixa | None:
    """A faixa que vale para `qtd`: a de maior `min_qtd` que ainda seja <= qtd.

    `None` quando não há tabela ou quando a quantidade fica abaixo da primeira faixa —
    aí quem responde é a regra de varejo/atacado.
    """
    escolhida: Faixa | None = None
    for faixa in faixas:
        if faixa.min_qtd <= qtd:
            escolhida = faixa
        else:
            break  # ordenado por min_qtd: daqui para frente é tudo maior
    return escolhida


def proxima_faixa(faixas: Sequence[Faixa], qtd: int) -> Faixa | None:
    """A faixa seguinte — o empurrãozinho de "levando mais N, sai por menos"."""
    for faixa in faixas:
        if faixa.min_qtd > qtd:
            return faixa
    return None


def rotulo_faixa(faixas: Sequence[Faixa], i: int) -> str:
    """Rótulo da faixa `i`: "1 a 9 un", "10+ un"."""
    if i < 0 or i >= len(faixas):
        return ""
    atual = faixas[i]
    seguinte = faixas[i + 1] if i + 1 < len(faixas) else None
    if seguinte is None:
        return f"{atual.min_qtd}+ un"
    return f"{atual.min_qtd} a {seguinte.min_qtd - 1} un"


def rotulo_para(faixas: Sequence[Faixa], qtd: int) -> str:
    """O rótulo da faixa que vale para `qtd`. Vazio quando nenhuma vale."""
    escolhida = faixa_para(faixas, qtd)
    if escolhida is None:
        return ""
    return rotulo_faixa(faixas, faixas.index(escolhida))


def validar_faixas(faixas: Sequence[Faixa]) -> None:
    """Recusa a tabela que não dá para usar. Tabela VAZIA é válida (é "sem tabela")."""
    if not faixas:
        return
    if len(faixas) > MAX_FAIXAS:
        raise RegraNegocioError(f"No máximo {MAX_FAIXAS} faixas de preço por produto.")

    vistos: set[int] = set()
    for faixa in faixas:
        if faixa.min_qtd in vistos:
            raise RegraNegocioError(
                f"Há duas faixas começando em {faixa.min_qtd} un — deixe só uma."
            )
        vistos.add(faixa.min_qtd)

    if min(f.min_qtd for f in faixas) != 1:
        raise RegraNegocioError(
            "Falta a faixa a partir de 1 un. Sem ela não dá para salvar: é o preço de "
            "quem leva pouco."
        )


def avisos_faixas(faixas: Sequence[Faixa]) -> list[str]:
    """O que é estranho mas pode ser de propósito. Nunca impede o save."""
    ordenadas = sorted(faixas, key=lambda f: f.min_qtd)
    return [
        f"A faixa de {atual.min_qtd} un está mais cara que a anterior."
        for anterior, atual in zip(ordenadas, ordenadas[1:], strict=False)
        if atual.preco > anterior.preco
    ]
