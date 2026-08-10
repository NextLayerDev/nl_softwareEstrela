"""Leitura de números escritos em pt-BR (vírgula decimal, ponto de milhar).

A regra vivia em três cópias divergentes pelo projeto. A do `web/routes/pedidos.py` só
trocava a vírgula por ponto, então "1.234,50" estourava `InvalidOperation` e virava
`Decimal("0")` em silêncio no preço do item. Aqui a regra é uma só, e é a mesma que o
`parseMoedaBR` do `static/js/orcamento.js` aplica no navegador.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_SO_NUMERO = re.compile(r"[^\d.,]")


def _corpo_numerico(bruto: object | None) -> tuple[str, bool] | None:
    """Isola o número de um texto como "R$ 1.234,50" ou "-12,00".

    Devolve (corpo, negativo) ou None quando não sobrou dígito nenhum.
    """
    if bruto is None:
        return None
    texto = str(bruto).strip()
    if not texto:
        return None
    negativo = texto.startswith("-")
    corpo = _SO_NUMERO.sub("", texto)
    if not any(c.isdigit() for c in corpo):
        return None
    return corpo, negativo


def parse_decimal_br(bruto: object | None) -> Decimal | None:
    """ "R$ 1.234,50" -> Decimal("1234.50"). None quando não há número legível.

    - Com vírgula: o ponto é separador de milhar e a vírgula é o decimal.
    - Sem vírgula: UM ponto seguido de 1 ou 2 casas é decimal ("12.5", "12.50");
      qualquer outro arranjo de pontos é milhar ("1.234", "1.234.567").

    Devolver None em vez de zero é de propósito: quem chama decide se um campo ilegível
    vale zero (formulário) ou vale uma pendência na tela (colagem de planilha).
    """
    lido = _corpo_numerico(bruto)
    if lido is None:
        return None
    corpo, negativo = lido

    if "," in corpo:
        corpo = corpo.replace(".", "").replace(",", ".")
    else:
        partes = corpo.split(".")
        if not (len(partes) == 2 and 1 <= len(partes[1]) <= 2):
            corpo = "".join(partes)

    try:
        valor = Decimal(corpo)
    except InvalidOperation:
        return None
    return -valor if negativo else valor


def parse_int_br(bruto: object | None) -> int | None:
    """Quantidade inteira: "1.000" -> 1000, "10,00" -> 10, "10,5" -> None.

    Quantidade fracionada não é arredondada — o sistema vende unidade, e adivinhar se
    "10,5" era 10 ou 11 é o tipo de chute que só aparece no faturamento.
    """
    valor = parse_decimal_br(bruto)
    if valor is None or valor != valor.to_integral_value():
        return None
    return int(valor)
