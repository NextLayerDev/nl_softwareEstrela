from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import RegraNegocioError
from app.models.enums import EstoqueModo, RotuloAprox, StatusInventario, TipoMov
from app.models.movimentacao import MovimentacaoEstoque
from app.models.produto import Produto, ProdutoVariacao
from app.models.usuario import Usuario
from app.repositories.estoque_repo import estoque_repo
from app.services.estoque_service import estoque_service
from app.services.inventario_service import inventario_service


def _cod() -> str:
    return f"T{uuid.uuid4().hex[:10].upper()}"


def _produto(
    db: Session,
    *,
    descricao: str = "Camiseta Teste",
    localizacao: str | None = "A1-P3",
    unidades_por_caixa: int | None = 12,
) -> Produto:
    p = Produto(
        codigo=_cod(),
        descricao=descricao,
        localizacao=localizacao,
        unidades_por_caixa=unidades_por_caixa,
    )
    db.add(p)
    db.flush()
    return p


def _variacao(
    db: Session,
    produto: Produto,
    *,
    cor: str = "Azul",
    modo: EstoqueModo = EstoqueModo.EXATO,
    fisico: int = 100,
    reservado: int = 0,
    minimo: int = 10,
    rotulo: RotuloAprox | None = None,
) -> ProdutoVariacao:
    v = ProdutoVariacao(
        produto_id=produto.id,
        cor=cor,
        estoque_modo=modo,
        estoque_fisico=fisico,
        estoque_reservado=reservado,
        estoque_minimo=minimo,
        rotulo_aprox=rotulo,
    )
    db.add(v)
    db.flush()
    return v


def _ultima_mov(db: Session, variacao_id: int) -> MovimentacaoEstoque | None:
    return db.scalar(
        select(MovimentacaoEstoque)
        .where(MovimentacaoEstoque.produto_variacao_id == variacao_id)
        .order_by(MovimentacaoEstoque.id.desc())
    )


# --------------------------------------------------------------------- reserva
def test_reservar_respeita_disponivel(db: Session, usuario_vendedor: Usuario):
    p = _produto(db)
    v = _variacao(db, p, fisico=100, reservado=0)
    estoque_service.reservar(db, v, 30, usuario_vendedor.id, pedido_id=1)
    assert v.estoque_reservado == 30
    assert v.disponivel == 70
    mov = _ultima_mov(db, v.id)
    assert mov.tipo == TipoMov.RESERVA
    assert mov.saldo_apos == v.estoque_fisico


def test_reservar_bloqueia_quando_insuficiente_exato(db: Session, usuario_vendedor: Usuario):
    p = _produto(db)
    v = _variacao(db, p, fisico=10, reservado=8)  # disponível = 2
    with pytest.raises(RegraNegocioError):
        estoque_service.reservar(db, v, 5, usuario_vendedor.id, pedido_id=1)
    assert v.estoque_reservado == 8  # inalterado


def test_reservar_aproximado_nao_bloqueia(db: Session, usuario_vendedor: Usuario):
    p = _produto(db)
    v = _variacao(db, p, modo=EstoqueModo.APROXIMADO, fisico=0, rotulo=RotuloAprox.TEM)
    estoque_service.reservar(db, v, 999, usuario_vendedor.id, pedido_id=1)
    assert v.estoque_reservado == 999
    assert _ultima_mov(db, v.id).tipo == TipoMov.RESERVA


# ----------------------------------------------------------------------- baixa
def test_baixar_reduz_fisico_e_reservado(db: Session, usuario_admin: Usuario):
    p = _produto(db)
    v = _variacao(db, p, fisico=100, reservado=30)
    estoque_service.baixar(db, v, 30, usuario_admin.id, pedido_id=1)
    assert v.estoque_fisico == 70
    assert v.estoque_reservado == 0
    mov = _ultima_mov(db, v.id)
    assert mov.tipo == TipoMov.SAIDA
    assert mov.saldo_apos == 70


def test_baixar_nunca_deixa_fisico_negativo_exato(db: Session, usuario_admin: Usuario):
    p = _produto(db)
    v = _variacao(db, p, fisico=5, reservado=5)
    with pytest.raises(RegraNegocioError):
        estoque_service.baixar(db, v, 10, usuario_admin.id, pedido_id=1)
    assert v.estoque_fisico == 5


# --------------------------------------------------------------------- estorno
def test_estornar_devolve_reserva(db: Session, usuario_admin: Usuario):
    p = _produto(db)
    v = _variacao(db, p, fisico=100, reservado=40)
    estoque_service.estornar(db, v, 40, usuario_admin.id, pedido_id=1)
    assert v.estoque_reservado == 0
    assert _ultima_mov(db, v.id).tipo == TipoMov.ESTORNO


# ---------------------------------------------------------------------- ajuste
def test_ajustar_exige_motivo(db: Session, usuario_admin: Usuario):
    p = _produto(db)
    v = _variacao(db, p, fisico=50)
    with pytest.raises(RegraNegocioError):
        estoque_service.ajustar(db, v, 60, usuario_admin.id, motivo="  ")
    assert v.estoque_fisico == 50


def test_ajustar_marca_exato_e_gera_movimentacao(db: Session, usuario_admin: Usuario):
    p = _produto(db)
    v = _variacao(db, p, modo=EstoqueModo.APROXIMADO, fisico=0, rotulo=RotuloAprox.POUCO)
    estoque_service.ajustar(db, v, 42, usuario_admin.id, motivo="recontagem")
    assert v.estoque_modo == EstoqueModo.EXATO
    assert v.estoque_fisico == 42
    assert v.rotulo_aprox is None
    mov = _ultima_mov(db, v.id)
    assert mov.tipo == TipoMov.AJUSTE
    assert mov.saldo_apos == 42
    assert mov.motivo == "recontagem"


# --------------------------------------------------------------------- entrada
def test_entrada_marca_exato(db: Session, usuario_funcionario: Usuario):
    p = _produto(db)
    v = _variacao(db, p, modo=EstoqueModo.APROXIMADO, fisico=0, rotulo=RotuloAprox.TEM)
    estoque_service.entrada(db, v, 25, usuario_funcionario.id)
    assert v.estoque_modo == EstoqueModo.EXATO
    assert v.estoque_fisico == 25
    assert v.rotulo_aprox is None
    mov = _ultima_mov(db, v.id)
    assert mov.tipo == TipoMov.ENTRADA
    assert mov.saldo_apos == 25


# ------------------------------------------------------------------ inventário
def test_inventario_aplica_e_gera_ajustes(
    db: Session, usuario_funcionario: Usuario, usuario_admin: Usuario
):
    p = _produto(db)
    v = _variacao(db, p, modo=EstoqueModo.APROXIMADO, fisico=0, rotulo=RotuloAprox.TEM)

    inv = inventario_service.abrir(
        db, usuario_funcionario.id, descricao="teste", variacao_ids=[v.id]
    )
    item = inv.itens[0]
    inventario_service.registrar_contagem(db, inv.id, item.id, 77)
    aplicado = inventario_service.aplicar(db, inv.id, usuario_admin.id)

    assert aplicado.status == StatusInventario.APLICADO
    assert v.estoque_fisico == 77
    assert v.estoque_modo == EstoqueModo.EXATO
    mov = _ultima_mov(db, v.id)
    assert mov.tipo == TipoMov.AJUSTE
    assert mov.motivo == f"inventário #{inv.id}"


def test_inventario_aplicado_nao_reaplica(
    db: Session, usuario_funcionario: Usuario, usuario_admin: Usuario
):
    p = _produto(db)
    v = _variacao(db, p, fisico=10)
    inv = inventario_service.abrir(db, usuario_funcionario.id, variacao_ids=[v.id])
    inventario_service.registrar_contagem(db, inv.id, inv.itens[0].id, 5)
    inventario_service.aplicar(db, inv.id, usuario_admin.id)
    with pytest.raises(RegraNegocioError):
        inventario_service.aplicar(db, inv.id, usuario_admin.id)


# ---------------------------------------------------------------- localização
def test_busca_localizacao_por_codigo_descricao_cor(db: Session):
    p = _produto(db, descricao="Boné Aba Reta Premium", localizacao="C4-P2")
    _variacao(db, p, cor="Vermelho")

    por_codigo = estoque_repo.busca_localizacao(db, p.codigo[:5])
    assert any(x.produto_id == p.id for x in por_codigo)

    por_descricao = estoque_repo.busca_localizacao(db, "Boné Aba")
    assert any(x.produto_id == p.id for x in por_descricao)

    por_cor = estoque_repo.busca_localizacao(db, "Vermelho")
    assert any(x.produto_id == p.id for x in por_cor)
