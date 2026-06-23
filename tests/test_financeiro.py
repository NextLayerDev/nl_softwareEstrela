"""Testes do módulo financeiro: baixa de recebimento, atrasados, RBAC."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.cliente import Cliente
from app.models.enums import StatusConta, StatusPedido
from app.models.pedido import Pedido
from app.models.usuario import Usuario
from app.schemas.financeiro import BaixaInput, FiltroContas
from app.services.financeiro_service import financeiro_service


def _login(perfil: str) -> TestClient:
    c = TestClient(app)
    resp = c.post(
        "/login",
        data={"email": f"{perfil}@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    return c


def _pedido(db: Session, vendedor: Usuario, *, total: str = "100.00") -> Pedido:
    cliente = Cliente(nome=f"Cli {uuid.uuid4().hex[:6]}")
    db.add(cliente)
    db.flush()
    pedido = Pedido(
        cliente_id=cliente.id,
        vendedor_id=vendedor.id,
        status=StatusPedido.FATURADO,
        total=Decimal(total),
        faturado_em=datetime.now(UTC),
    )
    db.add(pedido)
    db.flush()
    return pedido


def _conta(
    db: Session,
    pedido: Pedido,
    *,
    valor: str = "100.00",
    vencimento: date | None = None,
    status: StatusConta = StatusConta.PENDENTE,
):
    from app.models.conta_receber import ContaReceber

    conta = ContaReceber(
        pedido_id=pedido.id,
        parcela=1,
        valor=Decimal(valor),
        vencimento=vencimento or date.today(),
        status=status,
    )
    db.add(conta)
    db.flush()
    return conta


def test_baixar_muda_status_e_grava_baixado(db: Session, usuario_financeiro: Usuario) -> None:
    pedido = _pedido(db, usuario_financeiro)
    conta = _conta(db, pedido)
    financeiro_service.baixar(
        db, conta.id, BaixaInput(forma_pagamento="pix"), usuario_financeiro.id
    )
    assert conta.status == StatusConta.PAGO
    assert conta.forma_pagamento == "pix"
    assert conta.baixado_por == usuario_financeiro.id
    assert conta.baixado_em is not None


def test_baixar_conta_ja_paga_falha(db: Session, usuario_financeiro: Usuario) -> None:
    pedido = _pedido(db, usuario_financeiro)
    conta = _conta(db, pedido, status=StatusConta.PAGO)
    import pytest

    from app.core.errors import RegraNegocioError

    with pytest.raises(RegraNegocioError):
        financeiro_service.baixar(
            db, conta.id, BaixaInput(forma_pagamento="boleto"), usuario_financeiro.id
        )


def test_marcar_atrasados_marca_vencidos(db: Session, usuario_financeiro: Usuario) -> None:
    pedido = _pedido(db, usuario_financeiro)
    venc = _conta(db, pedido, vencimento=date.today() - timedelta(days=5))
    no_prazo = _conta(db, pedido, vencimento=date.today() + timedelta(days=5))
    paga = _conta(db, pedido, vencimento=date.today() - timedelta(days=3), status=StatusConta.PAGO)

    n = financeiro_service.marcar_atrasados(db)

    assert n >= 1
    assert venc.status == StatusConta.ATRASADO
    assert no_prazo.status == StatusConta.PENDENTE
    assert paga.status == StatusConta.PAGO


def test_marcar_atrasados_idempotente(db: Session, usuario_financeiro: Usuario) -> None:
    pedido = _pedido(db, usuario_financeiro)
    _conta(db, pedido, vencimento=date.today() - timedelta(days=2))
    financeiro_service.marcar_atrasados(db)
    n2 = financeiro_service.marcar_atrasados(db)
    assert n2 == 0


def test_listar_filtra_por_status(db: Session, usuario_financeiro: Usuario) -> None:
    pedido = _pedido(db, usuario_financeiro)
    _conta(db, pedido, status=StatusConta.PENDENTE)
    pagas = financeiro_service.listar(db, FiltroContas(status=StatusConta.PAGO))
    assert all(c.status == StatusConta.PAGO for c in pagas)


# ---------------- RBAC ----------------
def test_vendedor_nao_acessa_financeiro() -> None:
    resp = _login("vendedor").get("/financeiro", follow_redirects=False)
    assert resp.status_code == 403


def test_funcionario_nao_acessa_financeiro() -> None:
    resp = _login("funcionario").get("/financeiro", follow_redirects=False)
    assert resp.status_code == 403


def test_financeiro_e_admin_acessam() -> None:
    assert _login("financeiro").get("/financeiro").status_code == 200
    assert _login("admin").get("/financeiro").status_code == 200


def test_so_admin_marca_atrasados_via_rota() -> None:
    resp = _login("financeiro").post("/financeiro/marcar-atrasados", follow_redirects=False)
    assert resp.status_code == 403
    resp_admin = _login("admin").post("/financeiro/marcar-atrasados", follow_redirects=False)
    assert resp_admin.status_code == 303
