"""Como um código de produto é comparado — a regra mora aqui, e só aqui.

O código no cadastro é escrito de um jeito ("K-708", "Z 07-1", "LLSC-533") e digitado
de outro: sem traço, sem espaço, em minúsculas, ou lido por um scanner. Comparar o
texto cru só acha quem digitou exatamente igual, e era o que fazia `ch1086` não
encontrar `CH-1086`.

A normalização é agressiva de propósito — sobram apenas letras e dígitos ASCII, em
maiúsculas — porque num código a pontuação é decoração do cadastro, não informação.

Os dois lados precisam dar EXATAMENTE o mesmo resultado: `normalizar` roda no Python
(sobre o que o usuário digitou) e `coluna_normalizada` gera a mesma expressão em SQL
(sobre a coluna). Por isso o Python não dobra acento: o banco não tem a extensão
`unaccent`, então "Ç" desaparece no SQL, e dobrar para "C" aqui faria os dois lados
discordarem justamente no caso raro. Para texto livre (descrição, cor, cabeçalho da
planilha) quem normaliza é o `core/colagem.py`, que aí sim tira acento.
"""

from __future__ import annotations

import re

from sqlalchemy import case, func

# O regexp roda ANTES do upper() no SQL, então a classe precisa cobrir as duas caixas.
# Manter a fórmula idêntica aqui e no índice funcional é o que mantém a busca indexada.
_PADRAO = "[^A-Za-z0-9]"
_SEM_ALFANUM = re.compile(_PADRAO)


def normalizar(valor: str | None) -> str:
    """ "k-708" / "K 708" / "K708" -> "K708". Vazio quando não sobra nada."""
    return _SEM_ALFANUM.sub("", str(valor or "")).upper()


def coluna_normalizada(coluna):  # noqa: ANN001, ANN201 - expressão SQL
    """Mesma normalização do `normalizar`, aplicada a uma coluna.

    É a expressão indexada por `ix_produtos_codigo_norm` e `ix_produto_codigos_alt_norm`
    — mudar a fórmula aqui exige refazer os índices, senão a busca deixa de usá-los e
    passa a varrer a tabela inteira.
    """
    return func.upper(func.regexp_replace(coluna, _PADRAO, "", "g"))


def casa_codigo(coluna, termo: str):  # noqa: ANN001, ANN201 - expressão SQL
    """ "Este código contém o que foi digitado", normalizado dos dois lados."""
    return coluna_normalizada(coluna).like(f"%{normalizar(termo)}%")


def prioridade_codigo(coluna, termo: str):  # noqa: ANN001, ANN201 - expressão SQL
    """0 = código igual, 1 = começa com, 2 = contém, 3 = casou por outra coisa.

    Serve para o ORDER BY: quem digita um código quer aquele produto na PRIMEIRA linha.
    Antes a lista saía em ordem alfabética, então o item certo aparecia no meio de quem
    tinha casado pela descrição — e o vendedor concluía que o sistema não achou.
    """
    alvo = normalizar(termo)
    normalizada = coluna_normalizada(coluna)
    return case(
        (normalizada == alvo, 0),
        (normalizada.like(f"{alvo}%"), 1),
        (normalizada.like(f"%{alvo}%"), 2),
        else_=3,
    )
