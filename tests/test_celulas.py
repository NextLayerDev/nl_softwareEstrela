"""Células editáveis da lista em planilha: Nº, DATA, CLIENTE, ORIGEM, VENDEDOR e STATUS.

O STATUS é livre, mas o estoque e o financeiro acompanham — é o que estes testes cercam.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.errors import PermissaoNegadaError, RegraNegocioError
from app.models.conta_receber import ContaReceber
from app.models.enums import EstoqueModo, OrigemPedido, StatusConta, StatusPedido
from app.models.pedido import Pedido
from app.models.produto import Produto, ProdutoVariacao
from app.schemas.pedido import PedidoCompletoCreate
from app.services.pedido_service import pedido_service
from app.services.planilha_service import planilha_service


def _variacao(db, fisico=100) -> ProdutoVariacao:
    p = Produto(
        codigo=f"CE{uuid.uuid4().hex[:8].upper()}",
        descricao="CANECA DE CELULA",
        preco_pouca_qtd=Decimal("10.00"),
        preco_muita_qtd=Decimal("8.00"),
        preco_minimo=Decimal("0"),
    )
    db.add(p)
    db.flush()
    v = ProdutoVariacao(
        produto_id=p.id,
        cor="azul",
        estoque_modo=EstoqueModo.EXATO,
        estoque_fisico=fisico,
        estoque_reservado=0,
    )
    db.add(v)
    db.flush()
    return v


def _rascunho(db, usuario, variacao, qtd=5):
    return pedido_service.criar_completo(
        db,
        PedidoCompletoCreate(
            itens=[{"tipo": "catalogo", "variacao_id": variacao.id, "qtd": qtd, "preco_unit": "10"}]
        ),
        usuario.id,
        "admin",
    )


def _status(db, pedido, novo, usuario):
    return planilha_service.editar_celula(db, pedido.id, "status", novo, usuario.id, "admin")


def _contas(db, pedido) -> list[ContaReceber]:
    return list(db.scalars(select(ContaReceber).where(ContaReceber.pedido_id == pedido.id)))


# --------------------------------------------------------------------- status livre
def test_rascunho_para_entregue_reserva_baixa_numera_e_gera_contas(db, usuario_admin) -> None:
    v = _variacao(db)
    pedido = _rascunho(db, usuario_admin, v)
    _status(db, pedido, "entregue", usuario_admin)
    db.refresh(v)
    assert pedido.status == StatusPedido.ENTREGUE
    assert pedido.numero is not None and pedido.faturado_em is not None
    assert (v.estoque_fisico, v.estoque_reservado) == (95, 0)
    assert sum(c.valor for c in _contas(db, pedido)) == Decimal("50.00")


def test_voltar_de_faturado_para_confirmado_devolve_e_rereserva(db, usuario_admin) -> None:
    v = _variacao(db)
    pedido = _rascunho(db, usuario_admin, v)
    _status(db, pedido, "faturado", usuario_admin)
    _status(db, pedido, "confirmado", usuario_admin)
    db.refresh(v)
    assert pedido.status == StatusPedido.CONFIRMADO
    assert (v.estoque_fisico, v.estoque_reservado) == (100, 5)
    assert _contas(db, pedido) == []  # contas do faturamento desfeito somem
    assert pedido.faturado_em is None


def test_cancelar_confirmado_estorna_e_voltar_a_rascunho_nao_move(db, usuario_admin) -> None:
    v = _variacao(db)
    pedido = _rascunho(db, usuario_admin, v)
    _status(db, pedido, "separacao", usuario_admin)
    db.refresh(v)
    assert v.estoque_reservado == 5
    _status(db, pedido, "cancelado", usuario_admin)
    db.refresh(v)
    assert (v.estoque_fisico, v.estoque_reservado) == (100, 0)
    _status(db, pedido, "rascunho", usuario_admin)
    db.refresh(v)
    assert (v.estoque_fisico, v.estoque_reservado) == (100, 0)


def test_nao_volta_de_faturado_com_conta_paga(db, usuario_admin) -> None:
    v = _variacao(db)
    pedido = _rascunho(db, usuario_admin, v)
    _status(db, pedido, "faturado", usuario_admin)
    for c in _contas(db, pedido):
        c.status = StatusConta.PAGO
    db.flush()
    with pytest.raises(RegraNegocioError, match="paga"):
        _status(db, pedido, "cancelado", usuario_admin)


def test_sem_saldo_exato_nao_confirma(db, usuario_admin) -> None:
    v = _variacao(db, fisico=2)
    pedido = _rascunho(db, usuario_admin, v, qtd=5)
    with pytest.raises(RegraNegocioError, match="insuficiente"):
        _status(db, pedido, "confirmado", usuario_admin)


# --------------------------------------------------------------------- demais colunas
def test_numero_unico_e_sequence_acompanha(db, usuario_admin) -> None:
    v = _variacao(db)
    a = _rascunho(db, usuario_admin, v, qtd=1)
    b = _rascunho(db, usuario_admin, v, qtd=1)
    _status(db, a, "confirmado", usuario_admin)
    _status(db, b, "confirmado", usuario_admin)
    with pytest.raises(RegraNegocioError, match="Já existe"):
        planilha_service.editar_celula(db, b.id, "numero", str(a.numero), usuario_admin.id, "admin")

    # Pouco acima do maior número: setval não volta no rollback do teste, e um salto
    # grande deixaria buraco na numeração do banco de dev/CI.
    alto = int(db.scalar(select(func.max(Pedido.numero))) or 0) + 2
    planilha_service.editar_celula(db, b.id, "numero", str(alto), usuario_admin.id, "admin")
    assert b.numero == alto
    c = _rascunho(db, usuario_admin, v, qtd=1)
    _status(db, c, "confirmado", usuario_admin)
    assert c.numero > alto  # a próxima confirmação não colide com o número digitado


def test_rascunho_nao_ganha_numero_na_mao(db, usuario_admin) -> None:
    pedido = _rascunho(db, usuario_admin, _variacao(db))
    with pytest.raises(RegraNegocioError, match="Rascunho"):
        planilha_service.editar_celula(db, pedido.id, "numero", "5", usuario_admin.id, "admin")


def test_data_cliente_origem_e_vendedor(db, usuario_admin, usuario_vendedor) -> None:
    pedido = _rascunho(db, usuario_admin, _variacao(db))
    ed = planilha_service.editar_celula
    ed(db, pedido.id, "data", "2026-09-01", usuario_admin.id, "admin")
    ed(db, pedido.id, "cliente", "MARIA DA PLANILHA", usuario_admin.id, "admin")
    ed(db, pedido.id, "origem", "whatsapp", usuario_admin.id, "admin")
    ed(db, pedido.id, "vendedor", str(usuario_vendedor.id), usuario_admin.id, "admin")
    assert pedido.criado_em.date() == date(2026, 9, 1)
    assert pedido.nome_cliente == "MARIA DA PLANILHA"
    assert pedido.origem == OrigemPedido.WHATSAPP
    assert pedido.vendedor_id == usuario_vendedor.id


def test_vendedor_nao_troca_status_numero_nem_vendedor(db, usuario_admin, usuario_vendedor) -> None:
    pedido = _rascunho(db, usuario_admin, _variacao(db))
    for campo, valor in (("status", "confirmado"), ("numero", "7"), ("vendedor", "1")):
        with pytest.raises(PermissaoNegadaError):
            planilha_service.editar_celula(
                db, pedido.id, campo, valor, usuario_vendedor.id, "vendedor"
            )
    # o resto o vendedor edita
    planilha_service.editar_celula(
        db, pedido.id, "cliente", "JOAO", usuario_vendedor.id, "vendedor"
    )
    assert pedido.nome_cliente == "JOAO"


# --------------------------------------------------------------------- HTTP
@pytest.fixture
def client_admin(db, usuario_admin):
    from app.deps.auth import get_current_user
    from app.deps.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: usuario_admin
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_http_celula_e_linha_editavel(client_admin, db, usuario_admin) -> None:
    v = _variacao(db, fisico=1)
    pedido = _rascunho(db, usuario_admin, v, qtd=5)
    linha = client_admin.get(f"/pedidos/{pedido.id}/linha-planilha").text
    assert "PedidoPlanilha.celula" in linha

    ok = client_admin.post(
        f"/pedidos/{pedido.id}/celula", data={"campo": "cliente", "valor": "ANA"}
    )
    assert ok.json() == {"ok": True}

    # Sem saldo: recusa com 200 + mensagem, e o savepoint desfaz a reserva pela metade.
    r = client_admin.post(
        f"/pedidos/{pedido.id}/celula", data={"campo": "status", "valor": "confirmado"}
    )
    assert r.status_code == 200 and r.json()["ok"] is False
    db.refresh(v)
    db.refresh(pedido)
    assert v.estoque_reservado == 0 and pedido.status == StatusPedido.RASCUNHO
