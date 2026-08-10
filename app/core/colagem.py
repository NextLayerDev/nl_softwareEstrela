"""Leitura do bloco de texto colado da planilha de pedido.

Módulo puro: não conhece Session nem catálogo. Recebe o que o vendedor colou (Ctrl+C no
Excel cai na área de transferência como TSV) e devolve linhas canônicas + o que foi
descartado, com motivo. Quem casa isso com produto é o `colagem_service`.

O formato da cliente é sempre o mesmo:

    07/08/2026                      265550
    CODIGO   DESCIRCAO      QUANT.  V. UNIT.   SUB. TOTAL
    K-708    CANETA AZUL    10      R$ 2,50    R$ 25,00
                                    TOTAL      R$ 0,00

A primeira linha (data + número de controle da planilha) e o rodapé de TOTAL não
interessam ao sistema. O cabeçalho é lido por NOME de coluna, nunca por posição — a
planilha real escreve "DESCIRCAO" e continua funcionando.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal

from app.core.numeros_br import parse_decimal_br, parse_int_br

# Tetos de sanidade. Colagem maior que isso não é pedido, é engano — e vira uma
# pendência explicativa, nunca um erro HTTP (o htmx descarta corpo de resposta 4xx).
MAX_CHARS = 200_000
MAX_LINHAS = 500

# Quantidade absurda costuma ser a coluna SUB. TOTAL caindo no lugar da QUANT.
QTD_MAXIMA = 1_000_000

_SEM_ALFANUM = re.compile(r"[^A-Z0-9]")
_ESPACOS = re.compile(r"\s+")
_DOIS_ESPACOS = re.compile(r" {2,}")
_DATA = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")

# Papel da coluna -> prefixos aceitos no cabeçalho, já normalizados. A ordem importa:
# o primeiro que casar vence.
_COLUNAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("codigo", ("COD",)),
    ("qtd", ("QUANT", "QTD")),
    ("preco_unit", ("VUNIT", "VALORUNIT", "VLRUNIT", "UNIT", "PRECO")),
    ("descricao", ("DES",)),
)

# Posição das colunas quando a colagem vem sem cabeçalho nenhum.
_POSICIONAL = {"codigo": 0, "descricao": 1, "qtd": 2, "preco_unit": 3}


@dataclass(frozen=True)
class LinhaColada:
    """Uma linha de produto lida da colagem, ainda sem vínculo com o catálogo."""

    numero: int  # linha no texto colado (1-based) — é o que ancora a pendência na tela
    codigo: str
    descricao: str
    qtd: int | None  # None = quantidade ilegível; vira pendência no service
    preco_unit: Decimal | None  # None = vazio/zero -> cai no sugerir_preco do sistema
    bruto: str


@dataclass(frozen=True)
class LinhaIgnorada:
    """Linha descartada de propósito. `numero=0` fala da colagem inteira."""

    numero: int
    bruto: str
    motivo: str


# ----------------------------------------------------------------- normalização
def normalizar_texto(valor: str | None) -> str:
    """Maiúsculas sem acento, espaços colapsados. Chave de comparação de cor e cabeçalho.

    A desacentuação é feita aqui, no Python, porque o banco não tem a extensão `unaccent`
    instalada — só `pg_trgm`.
    """
    decomposto = unicodedata.normalize("NFD", str(valor or ""))
    sem_acento = "".join(c for c in decomposto if unicodedata.category(c) != "Mn")
    return _ESPACOS.sub(" ", sem_acento).strip().upper()


def normalizar_codigo(valor: str | None) -> str:
    """Só letras e dígitos: é o que faz "K708" casar com o "K-708" do cadastro."""
    return _SEM_ALFANUM.sub("", normalizar_texto(valor))


# ----------------------------------------------------------------- células
def _papel_da_coluna(celula: str) -> str | None:
    chave = normalizar_codigo(celula)
    if not chave:
        return None
    for papel, prefixos in _COLUNAS:
        if chave.startswith(prefixos):
            return papel
    return None


def _mapear_cabecalho(celulas: list[str]) -> dict[str, int] | None:
    """Mapa papel -> índice quando a linha é o cabeçalho da tabela, senão None.

    Casa por NOME e por prefixo, nunca por posição: a planilha da cliente escreve
    "DESCIRCAO" e "V. UNIT.", e exigir a grafia certa quebraria a colagem por causa de
    um erro de digitação que ninguém vai corrigir. Por isso a descrição não entra no
    teste de "isto é um cabeçalho" — bastam a coluna de código e a de quantidade.
    """
    mapa: dict[str, int] = {}
    for indice, celula in enumerate(celulas):
        papel = _papel_da_coluna(celula)
        if papel is not None and papel not in mapa:
            mapa[papel] = indice
    return mapa if "codigo" in mapa and "qtd" in mapa else None


def _confere_subtotal(numeros: list[str]) -> bool:
    """True quando (qtd × preço) bate com o subtotal, com folga de um centavo."""
    qtd, preco, subtotal = (parse_decimal_br(n) for n in numeros)
    if qtd is None or preco is None or subtotal is None:
        return False
    return abs(qtd * preco - subtotal) <= Decimal("0.01")


def _celulas_por_token(linha: str) -> list[str]:
    """Linha sem separador nenhum: "K-708 CANETA AZUL 10 2,50".

    Lê de trás para frente — o primeiro token é o código, os números do fim são
    quantidade e preço, e o miolo é a descrição. Mesma leitura que o `orcamento.js` faz
    na linha única do balcão.
    """
    tokens = linha.split()
    if len(tokens) < 2:
        return tokens
    codigo, *resto = tokens

    numeros: list[str] = []
    while resto and len(numeros) < 3 and parse_decimal_br(resto[-1]) is not None:
        numeros.insert(0, resto.pop())

    # Três números no fim são "qtd, preço, subtotal" só quando a conta fecha. Não
    # fechando, o primeiro deles é parte da descrição ("CANETA 2  10  2,50").
    if len(numeros) == 3 and not _confere_subtotal(numeros):
        resto.append(numeros.pop(0))

    qtd = numeros[0] if numeros else ""
    preco = numeros[1] if len(numeros) > 1 else ""
    return [codigo, " ".join(resto), qtd, preco]


def _celulas(linha: str, usa_tab: bool) -> list[str]:
    if usa_tab:
        return [c.strip() for c in linha.split("\t")]
    if _DOIS_ESPACOS.search(linha):
        return [c.strip() for c in _DOIS_ESPACOS.split(linha.strip())]
    return _celulas_por_token(linha)


def _pegar(celulas: list[str], mapa: dict[str, int], papel: str) -> str:
    indice = mapa.get(papel)
    if indice is None or not 0 <= indice < len(celulas):
        return ""
    return celulas[indice].strip()


# ----------------------------------------------------------------- parse
def parse_colagem(texto: str) -> tuple[list[LinhaColada], list[LinhaIgnorada]]:
    """Lê o bloco colado. Devolve (linhas de produto, linhas descartadas com motivo)."""
    if not texto or not texto.strip():
        return [], []
    if len(texto) > MAX_CHARS:
        return [], [
            LinhaIgnorada(0, "", f"Colagem grande demais (limite de {MAX_CHARS} caracteres).")
        ]

    brutas = texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(brutas) > MAX_LINHAS:
        return [], [
            LinhaIgnorada(0, "", f"Colagem com {len(brutas)} linhas — o limite é {MAX_LINHAS}.")
        ]

    # O separador é decidido no bloco inteiro, não linha a linha: cópia de Excel é
    # sempre TSV, e uma linha solta sem TAB no meio não muda o formato da planilha.
    usa_tab = any("\t" in linha for linha in brutas)

    lidas: list[LinhaColada] = []
    ignoradas: list[LinhaIgnorada] = []
    mapa: dict[str, int] = dict(_POSICIONAL)

    for numero, bruto in enumerate(brutas, start=1):
        celulas = _celulas(bruto, usa_tab)
        if not any(celulas):
            continue  # linha em branco não precisa virar ruído na tela

        cabecalho = _mapear_cabecalho(celulas)
        if cabecalho is not None:
            mapa = cabecalho
            # Tudo que veio antes do cabeçalho é enfeite da planilha — a data e o número
            # de controle do Excel, que o sistema ignora de propósito.
            ignoradas.extend(
                LinhaIgnorada(linha.numero, linha.bruto, "antes do cabeçalho") for linha in lidas
            )
            lidas.clear()
            ignoradas.append(LinhaIgnorada(numero, bruto.strip(), "cabeçalho da tabela"))
            continue

        codigo = _pegar(celulas, mapa, "codigo")
        descricao = _pegar(celulas, mapa, "descricao")

        if _DATA.match(codigo):
            ignoradas.append(LinhaIgnorada(numero, bruto.strip(), "cabeçalho da planilha"))
            continue
        if not codigo and any(normalizar_codigo(c).startswith("TOTAL") for c in celulas):
            ignoradas.append(LinhaIgnorada(numero, bruto.strip(), "rodapé de total"))
            continue
        if not codigo and not descricao:
            ignoradas.append(LinhaIgnorada(numero, bruto.strip(), "sem código nem descrição"))
            continue

        preco = parse_decimal_br(_pegar(celulas, mapa, "preco_unit"))
        lidas.append(
            LinhaColada(
                numero=numero,
                codigo=codigo,
                descricao=descricao,
                qtd=parse_int_br(_pegar(celulas, mapa, "qtd")),
                # Preço vazio ou zerado vira None de propósito: o pedido cai no preço
                # de tabela pela faixa de quantidade em vez de gravar um item a R$ 0,00.
                preco_unit=preco if preco is not None and preco > 0 else None,
                bruto=bruto.strip(),
            )
        )

    return lidas, ignoradas


def consolidar(linhas: list[LinhaColada]) -> list[LinhaColada]:
    """Mesmo código pelo mesmo preço colado vira uma linha só, somando a quantidade.

    Importa porque o corte de atacado do `sugerir_preco` olha a quantidade do lançamento:
    duas linhas de 6 com o corte em 10 sairiam as duas a varejo, quando o cliente está
    levando 12. Preço colado diferente fica separado — é a mesma regra que o
    `adicionar_item` já aplica dentro do pedido.
    """
    saida: list[LinhaColada] = []
    posicoes: dict[tuple[str, str], int] = {}

    for linha in linhas:
        chave_produto = normalizar_codigo(linha.codigo) or normalizar_texto(linha.descricao)
        if linha.qtd is None or not chave_produto:
            saida.append(linha)
            continue

        chave = (chave_produto, str(linha.preco_unit))
        posicao = posicoes.get(chave)
        if posicao is None:
            posicoes[chave] = len(saida)
            saida.append(linha)
            continue

        anterior = saida[posicao]
        saida[posicao] = replace(anterior, qtd=(anterior.qtd or 0) + linha.qtd)

    return saida
