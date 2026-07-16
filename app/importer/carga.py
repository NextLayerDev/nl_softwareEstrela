"""Etapa de CARGA: grava os produtos canônicos de forma idempotente.

Chaves de idempotência:
    produto      -> codigo
    variação     -> (produto_id, cor)
    código alt   -> (produto_id, codigo_alt)

Para cada variação EXATA, gera uma `MovimentacaoEstoque` (tipo=ENTRADA,
origem=IMPORTACAO, saldo_apos=estoque_fisico) como saldo inicial rastreável.
Não cria movimentação duplicada se a variação já existe com o mesmo saldo.

A função NÃO faz commit em `dry_run`. Em carga real, o commit é feito aqui
(é um script de carga em lote, fora do ciclo request->uow do app).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import eventos
from app.importer.parser import ProdutoCanonico
from app.models.categoria import Categoria
from app.models.enums import EstoqueModo, OrigemMov, TipoMov
from app.models.movimentacao import MovimentacaoEstoque
from app.models.produto import Produto, ProdutoCodigoAlt, ProdutoVariacao
from app.models.usuario import Usuario


@dataclass
class ResultadoCarga:
    produtos_criados: int = 0
    produtos_atualizados: int = 0
    variacoes_criadas: int = 0
    variacoes_atualizadas: int = 0
    codigos_alt_criados: int = 0
    movimentacoes_criadas: int = 0
    ignorados_sem_codigo: int = 0
    categorias_criadas: int = 0
    detalhes: list[str] = field(default_factory=list)


def _admin_id(db: Session) -> int:
    uid = db.scalar(select(Usuario.id).where(Usuario.perfil == "admin").order_by(Usuario.id))
    if uid is None:
        uid = db.scalar(select(Usuario.id).order_by(Usuario.id))
    if uid is None:
        raise RuntimeError(
            "Nenhum usuário no banco. Rode `uv run python scripts/seed.py` antes da carga."
        )
    return uid


def _get_or_create_categoria(db: Session, nome: str, res: ResultadoCarga) -> Categoria:
    cat = db.scalar(select(Categoria).where(Categoria.nome == nome))
    if cat is None:
        cat = Categoria(nome=nome)
        db.add(cat)
        db.flush()
        res.categorias_criadas += 1
    return cat


def carregar(
    db: Session, produtos: list[ProdutoCanonico], *, dry_run: bool = False
) -> ResultadoCarga:
    res = ResultadoCarga()
    usuario_id = _admin_id(db)
    cache_cat: dict[str, int] = {}

    for p in produtos:
        if not p.codigo:
            res.ignorados_sem_codigo += 1
            continue

        # categoria
        categoria_id: int | None = None
        if p.categoria:
            if p.categoria not in cache_cat:
                cache_cat[p.categoria] = _get_or_create_categoria(db, p.categoria, res).id
            categoria_id = cache_cat[p.categoria]

        existente = db.scalar(select(Produto).where(Produto.codigo == p.codigo))
        if existente is None:
            produto = Produto(
                codigo=p.codigo,
                descricao=p.descricao or p.codigo,
                categoria_id=categoria_id,
                localizacao=p.localizacao,
                unidades_por_caixa=p.unidades_por_caixa,
                preco_pouca_qtd=p.preco_pouca_qtd or Decimal("0"),
                preco_muita_qtd=p.preco_muita_qtd or p.preco_pouca_qtd or Decimal("0"),
            )
            db.add(produto)
            db.flush()
            res.produtos_criados += 1
        else:
            produto = existente
            produto.descricao = p.descricao or produto.descricao
            produto.categoria_id = categoria_id or produto.categoria_id
            produto.localizacao = p.localizacao or produto.localizacao
            produto.unidades_por_caixa = p.unidades_por_caixa or produto.unidades_por_caixa
            if p.preco_pouca_qtd is not None:
                produto.preco_pouca_qtd = p.preco_pouca_qtd
            if p.preco_muita_qtd is not None:
                produto.preco_muita_qtd = p.preco_muita_qtd
            produto.observacao = p.observacao or produto.observacao
            res.produtos_atualizados += 1
        if p.observacao and existente is None:
            produto.observacao = p.observacao

        # códigos alternativos (idempotente por (produto_id, codigo_alt))
        for cod_alt in p.codigos_alt:
            ja = db.scalar(
                select(ProdutoCodigoAlt).where(
                    ProdutoCodigoAlt.produto_id == produto.id,
                    ProdutoCodigoAlt.codigo_alt == cod_alt,
                )
            )
            if ja is None:
                db.add(ProdutoCodigoAlt(produto_id=produto.id, codigo_alt=cod_alt))
                res.codigos_alt_criados += 1

        # variações (idempotente por (produto_id, cor))
        for v in p.variacoes:
            var = db.scalar(
                select(ProdutoVariacao).where(
                    ProdutoVariacao.produto_id == produto.id,
                    ProdutoVariacao.cor == v.cor,
                )
            )
            criou_var = var is None
            if criou_var:
                var = ProdutoVariacao(
                    produto_id=produto.id,
                    cor=v.cor,
                    estoque_modo=v.estoque_modo,
                    estoque_fisico=v.estoque_fisico if v.estoque_modo == EstoqueModo.EXATO else 0,
                    rotulo_aprox=v.rotulo_aprox,
                )
                db.add(var)
                db.flush()
                res.variacoes_criadas += 1
            else:
                var.estoque_modo = v.estoque_modo
                var.rotulo_aprox = v.rotulo_aprox
                if v.estoque_modo == EstoqueModo.EXATO:
                    var.estoque_fisico = v.estoque_fisico
                res.variacoes_atualizadas += 1

            # movimentação inicial só para EXATO; idempotente: não recria se já há
            # movimentação de IMPORTACAO para a variação.
            if v.estoque_modo == EstoqueModo.EXATO:
                ja_mov = db.scalar(
                    select(MovimentacaoEstoque.id).where(
                        MovimentacaoEstoque.produto_variacao_id == var.id,
                        MovimentacaoEstoque.origem == OrigemMov.IMPORTACAO,
                    )
                )
                if ja_mov is None:
                    db.add(
                        MovimentacaoEstoque(
                            produto_variacao_id=var.id,
                            tipo=TipoMov.ENTRADA,
                            qtd=v.estoque_fisico,
                            origem=OrigemMov.IMPORTACAO,
                            ref_id=None,
                            usuario_id=usuario_id,
                            saldo_apos=v.estoque_fisico,
                            motivo="Saldo inicial (importação CONTROLE.xlsx)",
                        )
                    )
                    res.movimentacoes_criadas += 1

    if dry_run:
        db.rollback()
    else:
        # Um único resumo: a carga escreve as movimentações direto (sem passar pelo
        # estoque_service), então nada dispara por linha — e nem deveria, seriam milhares.
        # O emit entra antes do commit de propósito: o NOTIFY é transacional.
        eventos.emitir(
            db,
            "importacao.concluida",
            {
                "produtos_criados": res.produtos_criados,
                "produtos_atualizados": res.produtos_atualizados,
                "variacoes_criadas": res.variacoes_criadas,
                "variacoes_atualizadas": res.variacoes_atualizadas,
                "movimentacoes_criadas": res.movimentacoes_criadas,
            },
            audiencia=eventos.SEP_AUD,
        )
        db.commit()

    return res
