"""Etapa de PARSING: agrupa as células brutas em blocos e produz registros canônicos.

Layout da planilha (loose, por blocos):
    - O cabeçalho `CÓDIGO | DESCRIÇÃO | COD ALTERNATIVO | CORES | QUANTIDADE`
      se repete a cada produto. Localizamos as colunas pelo texto do header
      (não assumimos posição fixa).
    - Um bloco vai de um header até o próximo (ou até o fim da aba).
    - Na coluna CÓDIGO: o 1º texto não-numérico é o código do produto;
      números soltos abaixo são PREÇOS (1 = pouca; 2 = pouca/muita).
    - DESCRIÇÃO multi-linha é concatenada; dela extraímos localização
      ("...andar...") e unidades por caixa ("X EM CADA CAIXA"). O resto
      vira observação.
    - CORES empilhadas -> uma variação por cor. QUANTIDADE empilhada é
      pareada por ordem de aparição com as cores.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.models.enums import EstoqueModo, RotuloAprox

# ---------------------------------------------------------------------------
# Registros canônicos
# ---------------------------------------------------------------------------


@dataclass
class VariacaoCanonica:
    cor: str
    estoque_modo: EstoqueModo
    estoque_fisico: int
    rotulo_aprox: RotuloAprox | None
    linha: int  # linha de origem (para o relatório)


@dataclass
class ProdutoCanonico:
    aba: str
    linha_inicio: int
    codigo: str | None
    descricao: str
    categoria: str | None = None
    localizacao: str | None = None
    unidades_por_caixa: int | None = None
    observacao: str | None = None
    preco_pouca_qtd: Decimal | None = None
    preco_muita_qtd: Decimal | None = None
    codigos_alt: list[str] = field(default_factory=list)
    variacoes: list[VariacaoCanonica] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers de normalização
# ---------------------------------------------------------------------------

_RE_ANDAR = re.compile(r"[^.;\n]*\bandar\b[^.;\n]*", re.IGNORECASE)
_RE_SUBSOLO = re.compile(r"[^.;\n]*\bsubsolo\b[^.;\n]*", re.IGNORECASE)
_RE_CAIXA = re.compile(r"([\d.,]+)\s*(?:UNID(?:ADES)?\.?\s*)?EM\s*CADA\s*CAIXA", re.IGNORECASE)
_RE_QTD_UNID = re.compile(r"^\s*([\d.,]+)\s*UNID", re.IGNORECASE)
_RE_SO_NUMERO = re.compile(r"^\s*\d[\d.,]*\s*$")

# Termos na coluna COD ALTERNATIVO que NÃO são código de empresa.
_ALT_STOPWORDS = ("acabou", "chega", "dia ", "cx", "pendente", "falta")


def _txt(v: object | None) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _is_header(valores: list[object | None]) -> bool:
    cells = {_txt(c).upper() for c in valores[:8] if c is not None}
    return "CÓDIGO" in cells and "QUANTIDADE" in cells and "CORES" in cells


def _mapear_colunas(valores: list[object | None]) -> dict[str, int]:
    idx: dict[str, int] = {}
    for ci, c in enumerate(valores):
        t = _txt(c).upper()
        if not t:
            continue
        if t == "CÓDIGO" and "cod" not in idx:
            idx["cod"] = ci
        elif t == "DESCRIÇÃO" and "desc" not in idx:
            idx["desc"] = ci
        elif "ALTERNATIV" in t and "alt" not in idx:
            idx["alt"] = ci
        elif t == "CORES" and "cor" not in idx:
            idx["cor"] = ci
        elif t == "QUANTIDADE" and "qtd" not in idx:
            idx["qtd"] = ci
    return idx


def _to_decimal(v: object | None) -> Decimal | None:
    """Converte célula em Decimal tratando vírgula decimal pt-BR. Retorna None se não for número."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return Decimal(str(v))
        except InvalidOperation:
            return None
    s = _txt(v)
    if not s or not _RE_SO_NUMERO.match(s):
        return None
    # pt-BR: "1.234,56" -> remove separador de milhar (.), vírgula vira ponto.
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _parece_preco(v: object | None) -> Decimal | None:
    """Número solto na coluna CÓDIGO que representa preço (valor pequeno, tipicamente < 1000)."""
    d = _to_decimal(v)
    if d is None:
        return None
    return d


def _extrair_unidades_caixa(texto: str) -> int | None:
    m = _RE_CAIXA.search(texto)
    if not m:
        return None
    digitos = re.sub(r"[^\d]", "", m.group(1))  # remove separador de milhar
    return int(digitos) if digitos else None


def _extrair_localizacao(texto: str) -> str | None:
    m = _RE_ANDAR.search(texto)
    if m:
        return m.group(0).strip()
    m = _RE_SUBSOLO.search(texto)
    if m:
        return m.group(0).strip()
    return None


def _classificar_quantidade(v: object | None) -> tuple[EstoqueModo, int, RotuloAprox | None]:
    """Normaliza uma célula de QUANTIDADE para (modo, estoque_fisico, rotulo)."""
    if v is None:
        return EstoqueModo.APROXIMADO, 0, RotuloAprox.ACABOU
    if isinstance(v, (int, float)):
        return EstoqueModo.EXATO, int(v), None
    s = _txt(v)
    if not s or s == "-":
        return EstoqueModo.APROXIMADO, 0, RotuloAprox.ACABOU
    su = s.upper()
    if su in ("ACABOU", "ACABO"):
        return EstoqueModo.APROXIMADO, 0, RotuloAprox.ACABOU
    # "375 UNID", "75 UNIDADES"
    m = _RE_QTD_UNID.match(s)
    if m:
        digitos = re.sub(r"[^\d]", "", m.group(1))
        return EstoqueModo.EXATO, int(digitos) if digitos else 0, None
    # número puro como texto
    if _RE_SO_NUMERO.match(s):
        d = _to_decimal(s)
        if d is not None:
            return EstoqueModo.EXATO, int(d), None
    if "MUITO" in su:
        return EstoqueModo.APROXIMADO, 0, RotuloAprox.MUITO
    if "POUCO" in su or "POUCA" in su:
        return EstoqueModo.APROXIMADO, 0, RotuloAprox.POUCO
    if "TEM" in su:
        return EstoqueModo.APROXIMADO, 0, RotuloAprox.TEM
    # texto desconhecido -> aproximado/TEM (há algo, mas não sabemos quanto)
    return EstoqueModo.APROXIMADO, 0, RotuloAprox.TEM


def _alt_parece_codigo(s: str) -> bool:
    if not s:
        return False
    low = s.lower()
    if any(w in low for w in _ALT_STOPWORDS):
        return False
    # código de empresa: tem dígito e (letra ou hífen), curto
    tem_digito = any(ch.isdigit() for ch in s)
    tem_letra_ou_hifen = any(ch.isalpha() for ch in s) or "-" in s
    return tem_digito and tem_letra_ou_hifen and len(s) <= 20


# ---------------------------------------------------------------------------
# Parsing de blocos
# ---------------------------------------------------------------------------


def _parse_bloco(aba, linhas, cols, categoria) -> ProdutoCanonico:
    ci_cod = cols.get("cod", 0)
    ci_desc = cols.get("desc", 1)
    ci_alt = cols.get("alt", 2)
    ci_cor = cols.get("cor", 3)
    ci_qtd = cols.get("qtd", 4)
    ncols_chave = max(ci_cod, ci_desc, ci_alt, ci_cor, ci_qtd) + 1

    prod = ProdutoCanonico(
        aba=aba,
        linha_inicio=linhas[0].linha if linhas else 0,
        codigo=None,
        descricao="",
        categoria=categoria,
    )

    desc_partes: list[str] = []
    obs_partes: list[str] = []
    precos: list[Decimal] = []
    cores: list[tuple[str, int]] = []  # (cor, linha)
    quantidades: list[tuple[object, int]] = []  # (valor bruto, linha)

    for cel in linhas:
        v_cod = cel.valor(ci_cod)
        v_desc = cel.valor(ci_desc)
        v_alt = cel.valor(ci_alt)
        v_cor = cel.valor(ci_cor)
        v_qtd = cel.valor(ci_qtd)

        # CÓDIGO: texto -> código do produto; número -> preço; "X EM CADA CAIXA" -> caixa
        t_cod = _txt(v_cod)
        if t_cod:
            if _extrair_unidades_caixa(t_cod) is not None and prod.unidades_por_caixa is None:
                prod.unidades_por_caixa = _extrair_unidades_caixa(t_cod)
            else:
                preco = _parece_preco(v_cod)
                if preco is not None:
                    precos.append(preco)
                elif prod.codigo is None:
                    prod.codigo = t_cod
                else:
                    # texto extra na coluna código (raro) -> observação
                    obs_partes.append(t_cod)

        # DESCRIÇÃO
        t_desc = _txt(v_desc)
        if t_desc:
            cx = _extrair_unidades_caixa(t_desc)
            if cx is not None and prod.unidades_por_caixa is None:
                prod.unidades_por_caixa = cx
            loc = _extrair_localizacao(t_desc)
            if loc and prod.localizacao is None:
                prod.localizacao = loc
            # remove o trecho de caixa/localização do que vai pra descrição/obs
            resto = t_desc
            if cx is not None:
                resto = _RE_CAIXA.sub("", resto).strip()
            if loc:
                resto = resto.replace(loc, "").strip(" .;-")
            if resto:
                low = resto.lower()
                # anotações de preço/observação vs. parte da descrição
                if any(k in low for k in ("qnt", "qtd", "pedidos", "grande", "andar", "subsolo")):
                    obs_partes.append(resto)
                else:
                    desc_partes.append(resto)

        # COD ALTERNATIVO
        t_alt = _txt(v_alt)
        if t_alt:
            if _alt_parece_codigo(t_alt):
                if t_alt not in prod.codigos_alt:
                    prod.codigos_alt.append(t_alt)
            else:
                obs_partes.append(t_alt)

        # CORES
        t_cor = _txt(v_cor)
        if t_cor and t_cor != "-":
            cores.append((t_cor, cel.linha))

        # QUANTIDADE
        if v_qtd is not None and _txt(v_qtd) != "":
            quantidades.append((v_qtd, cel.linha))

        # Colunas livres à direita (observações soltas: "adiocionado ao catálogo", etc.)
        for ci in range(ncols_chave, len(cel.valores)):
            t = _txt(cel.valor(ci))
            if t:
                obs_partes.append(t)

    prod.descricao = " ".join(desc_partes).strip()

    # preços: 1 -> pouca; 2 -> pouca/muita (ordem de aparição)
    if len(precos) >= 1:
        prod.preco_pouca_qtd = precos[0]
    if len(precos) >= 2:
        prod.preco_muita_qtd = precos[1]

    # variações: pareia cor[i] com quantidade[i] por ordem. Sobras viram inconsistência
    # (cor sem qtd / qtd sem cor) — registradas como variação parcial p/ o validador ver.
    n = max(len(cores), len(quantidades))
    if n == 0:
        # produto sem cor e sem quantidade: variação única ACABOU
        prod.variacoes.append(
            VariacaoCanonica("", EstoqueModo.APROXIMADO, 0, RotuloAprox.ACABOU, prod.linha_inicio)
        )
    else:
        for i in range(n):
            cor, lc = cores[i] if i < len(cores) else ("", prod.linha_inicio)
            if i < len(quantidades):
                bruto, lq = quantidades[i]
                modo, fisico, rot = _classificar_quantidade(bruto)
                linha = lq
            else:
                # cor sem quantidade
                modo, fisico, rot = EstoqueModo.APROXIMADO, 0, None
                linha = lc
            prod.variacoes.append(VariacaoCanonica(cor, modo, fisico, rot, linha))

    # observação consolidada (dedup preservando ordem)
    vistos: set[str] = set()
    obs_limpa = []
    for o in obs_partes:
        if o and o not in vistos:
            vistos.add(o)
            obs_limpa.append(o)
    prod.observacao = " | ".join(obs_limpa) if obs_limpa else None

    return prod


def parse_blocos(
    linhas_por_aba: dict[str, list],
    aba_para_categoria: dict[str, str] | None = None,
) -> list[ProdutoCanonico]:
    """Percorre cada aba, fatia em blocos por header e devolve os produtos canônicos."""
    aba_para_categoria = aba_para_categoria or {}
    produtos: list[ProdutoCanonico] = []

    for aba, linhas in linhas_por_aba.items():
        categoria = aba_para_categoria.get(aba)
        cols_atual: dict[str, int] | None = None
        bloco: list = []

        for cel in linhas:
            if _is_header(cel.valores):
                # fecha bloco anterior antes de abrir novo
                if cols_atual and any(not c.vazia for c in bloco):
                    produtos.append(_parse_bloco(aba, bloco, cols_atual, categoria))
                cols_atual = _mapear_colunas(cel.valores)
                bloco = []
                continue
            if cols_atual is not None:
                bloco.append(cel)
        if cols_atual and any(not c.vazia for c in bloco):
            produtos.append(_parse_bloco(aba, bloco, cols_atual, categoria))

    return produtos
