"""Testes do motor de importação (ETL do CONTROLE.xlsx).

- Parser: preços pouca/muita, EXATO vs APROXIMADO, localização, unidades/caixa, cód. alt.
- Carga: idempotência (2x não duplica) e movimentação inicial ENTRADA/IMPORTACAO.

A planilha de teste é SINTÉTICA, montada com openpyxl em arquivo temporário,
reproduzindo os padrões reais (blocos, preços soltos, cores empilhadas, notas).
"""

from __future__ import annotations

from decimal import Decimal

import openpyxl
import pytest
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.importer.carga import carregar
from app.importer.parser import parse_blocos
from app.importer.staging import ler_staging
from app.models.enums import EstoqueModo, OrigemMov, RotuloAprox, TipoMov
from app.models.movimentacao import MovimentacaoEstoque
from app.models.produto import Produto, ProdutoCodigoAlt, ProdutoVariacao
from app.models.usuario import Usuario

ABA = "CANETAS PLÁSTICAS"
CATS = {ABA: "Canetas Plásticas"}

# Códigos sintéticos usados nos testes (limpos no teardown).
CODIGOS_TESTE = ["TST708", "TST33", "TSTUNID", "TSTALT", "TSTLOC"]


def _montar_planilha(caminho) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = ABA
    header = ["CÓDIGO", "DESCRIÇÃO", "COD ALTERNATIVO", "CORES", "QUANTIDADE"]

    def bloco(linhas):
        ws.append(header)
        for ln in linhas:
            ws.append(ln + [None] * (5 - len(ln)))
        ws.append([])

    # Bloco 1: dois preços (pouca/muita) + número exato + "TEM MUITO" + "-"
    bloco(
        [
            ["TST708", "CANETA TESTE", None, "BRANCO", 156],
            [None, "COM SUPORTE", None, "AZUL", "TEM MUITO"],
            [1.2, "pouca qnt", None, "PRETO", "-"],
            [1.0, "muita qnt", None, "VERDE", "375 UNID"],
        ]
    )

    # Bloco 2: localização "4º andar" + unidades por caixa "1.000 EM CADA CAIXA"
    bloco(
        [
            ["TSTLOC", "CADERNO TESTE", None, "AZUL", "TEM MUITO"],
            [9.5, "4º andar à direita", None, "PRETO", "TEM POUCO"],
            [None, "1.000 EM CADA CAIXA", None, "VERMELHO", "-"],
        ]
    )

    # Bloco 3: código alternativo válido
    bloco(
        [
            ["TSTALT", "CANETA COM ALT", "K-803", "PRATA", 10],
        ]
    )

    # Bloco 4: "375 UNID" como exato e unidades por caixa na coluna CÓDIGO
    bloco(
        [
            ["TSTUNID", "COPO TESTE", None, "INOX", "375 UNID"],
            ["60 UNID EM CADA CAIXA", None, None, None, None],
        ]
    )

    # Bloco 5: sem cor, quantidade "ACABOU"
    bloco(
        [
            ["TST33", "KIT TESTE", "ACABOU", "-", "-"],
            [22, "chega em abril", None, None, None],
        ]
    )

    wb.save(caminho)


@pytest.fixture
def planilha(tmp_path):
    caminho = tmp_path / "controle_teste.xlsx"
    _montar_planilha(caminho)
    return caminho


@pytest.fixture
def produtos(planilha):
    staging = ler_staging(planilha, [ABA])
    return parse_blocos(staging, CATS)


def _por_codigo(produtos, codigo):
    return next(p for p in produtos if p.codigo == codigo)


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def test_dois_precos_viram_pouca_e_muita(produtos):
    p = _por_codigo(produtos, "TST708")
    assert p.preco_pouca_qtd == Decimal("1.2")
    assert p.preco_muita_qtd == Decimal("1.0")


def test_numero_puro_vira_exato_com_saldo(produtos):
    p = _por_codigo(produtos, "TST708")
    branco = next(v for v in p.variacoes if v.cor == "BRANCO")
    assert branco.estoque_modo == EstoqueModo.EXATO
    assert branco.estoque_fisico == 156


def test_tem_muito_vira_aproximado_muito(produtos):
    p = _por_codigo(produtos, "TST708")
    azul = next(v for v in p.variacoes if v.cor == "AZUL")
    assert azul.estoque_modo == EstoqueModo.APROXIMADO
    assert azul.rotulo_aprox == RotuloAprox.MUITO


def test_traco_vira_acabou(produtos):
    p = _por_codigo(produtos, "TST708")
    preto = next(v for v in p.variacoes if v.cor == "PRETO")
    assert preto.estoque_modo == EstoqueModo.APROXIMADO
    assert preto.rotulo_aprox == RotuloAprox.ACABOU


def test_375_unid_vira_exato_375(produtos):
    p = _por_codigo(produtos, "TSTUNID")
    inox = next(v for v in p.variacoes if v.cor == "INOX")
    assert inox.estoque_modo == EstoqueModo.EXATO
    assert inox.estoque_fisico == 375


def test_quarto_andar_vira_localizacao(produtos):
    p = _por_codigo(produtos, "TSTLOC")
    assert p.localizacao is not None
    assert "andar" in p.localizacao.lower()


def test_mil_em_cada_caixa_vira_unidades(produtos):
    p = _por_codigo(produtos, "TSTLOC")
    assert p.unidades_por_caixa == 1000


def test_unidades_caixa_na_coluna_codigo(produtos):
    p = _por_codigo(produtos, "TSTUNID")
    assert p.unidades_por_caixa == 60


def test_codigo_alternativo_capturado(produtos):
    p = _por_codigo(produtos, "TSTALT")
    assert "K-803" in p.codigos_alt


def test_alt_nao_codigo_nao_vira_codigo_alt(produtos):
    p = _por_codigo(produtos, "TST33")
    assert p.codigos_alt == []  # "ACABOU" não é código


def test_produto_sem_cor_gera_variacao_unica(produtos):
    p = _por_codigo(produtos, "TST33")
    assert len(p.variacoes) == 1
    assert p.variacoes[0].cor == ""
    assert p.variacoes[0].rotulo_aprox == RotuloAprox.ACABOU


# --------------------------------------------------------------------------- #
# Carga (usa SessionLocal direto + cleanup, pois carrega faz commit)
# --------------------------------------------------------------------------- #


def _limpar(db):
    ids = db.scalars(select(Produto.id).where(Produto.codigo.in_(CODIGOS_TESTE))).all()
    if not ids:
        return
    var_ids = db.scalars(
        select(ProdutoVariacao.id).where(ProdutoVariacao.produto_id.in_(ids))
    ).all()
    if var_ids:
        db.query(MovimentacaoEstoque).filter(
            MovimentacaoEstoque.produto_variacao_id.in_(var_ids)
        ).delete(synchronize_session=False)
    db.query(ProdutoCodigoAlt).filter(ProdutoCodigoAlt.produto_id.in_(ids)).delete(
        synchronize_session=False
    )
    db.query(ProdutoVariacao).filter(ProdutoVariacao.produto_id.in_(ids)).delete(
        synchronize_session=False
    )
    db.query(Produto).filter(Produto.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def db_carga(produtos):
    db = SessionLocal()
    # garante um admin para usuario_id da movimentação
    admin = db.scalar(select(Usuario).where(Usuario.perfil == "admin"))
    if admin is None:
        from app.core.security import hash_senha

        admin = Usuario(
            nome="Admin ETL Teste",
            email="admin-etl-teste@teste.local",
            senha_hash=hash_senha("x"),
            perfil="admin",
        )
        db.add(admin)
        db.commit()
    _limpar(db)
    try:
        yield db
    finally:
        _limpar(db)
        db.close()


def test_idempotencia_carga(db_carga, produtos):
    carregar(db_carga, produtos, dry_run=False)
    n_prod_1 = db_carga.scalar(
        select(func.count(Produto.id)).where(Produto.codigo.in_(CODIGOS_TESTE))
    )
    n_var_1 = db_carga.scalar(
        select(func.count(ProdutoVariacao.id))
        .join(Produto)
        .where(Produto.codigo.in_(CODIGOS_TESTE))
    )

    # segunda carga não deve duplicar
    carregar(db_carga, produtos, dry_run=False)
    n_prod_2 = db_carga.scalar(
        select(func.count(Produto.id)).where(Produto.codigo.in_(CODIGOS_TESTE))
    )
    n_var_2 = db_carga.scalar(
        select(func.count(ProdutoVariacao.id))
        .join(Produto)
        .where(Produto.codigo.in_(CODIGOS_TESTE))
    )

    assert n_prod_1 == n_prod_2 == len(CODIGOS_TESTE)
    assert n_var_1 == n_var_2


def test_variacao_exata_gera_movimentacao_entrada_importacao(db_carga, produtos):
    carregar(db_carga, produtos, dry_run=False)

    branco = db_carga.scalar(
        select(ProdutoVariacao)
        .join(Produto)
        .where(Produto.codigo == "TST708", ProdutoVariacao.cor == "BRANCO")
    )
    assert branco is not None
    assert branco.estoque_modo == EstoqueModo.EXATO
    assert branco.estoque_fisico == 156

    mov = db_carga.scalar(
        select(MovimentacaoEstoque).where(MovimentacaoEstoque.produto_variacao_id == branco.id)
    )
    assert mov is not None
    assert mov.tipo == TipoMov.ENTRADA
    assert mov.origem == OrigemMov.IMPORTACAO
    assert mov.saldo_apos == 156

    # carga 2x não cria movimentação duplicada
    carregar(db_carga, produtos, dry_run=False)
    n_mov = db_carga.scalar(
        select(func.count(MovimentacaoEstoque.id)).where(
            MovimentacaoEstoque.produto_variacao_id == branco.id
        )
    )
    assert n_mov == 1


def test_aproximado_nao_gera_movimentacao(db_carga, produtos):
    carregar(db_carga, produtos, dry_run=False)
    azul = db_carga.scalar(
        select(ProdutoVariacao)
        .join(Produto)
        .where(Produto.codigo == "TST708", ProdutoVariacao.cor == "AZUL")
    )
    assert azul.estoque_modo == EstoqueModo.APROXIMADO
    n_mov = db_carga.scalar(
        select(func.count(MovimentacaoEstoque.id)).where(
            MovimentacaoEstoque.produto_variacao_id == azul.id
        )
    )
    assert n_mov == 0
