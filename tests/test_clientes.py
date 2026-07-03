"""Testes do CRUD de clientes."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.controllers.cliente_controller import cliente_controller
from app.core.errors import RegraNegocioError
from app.main import app
from app.models.pedido import Pedido
from app.models.usuario import Usuario
from app.schemas.cliente import ClienteCreate, ClienteUpdate
from app.services.cliente_service import cliente_service


def _login(client: TestClient, perfil: str) -> None:
    resp = client.post(
        "/login",
        data={"email": f"{perfil}@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


def test_criar_e_listar_cliente(db: Session) -> None:
    nome = f"Cliente {uuid.uuid4().hex[:6]}"
    c = cliente_service.criar(db, ClienteCreate(nome=nome, telefone="11999"))
    db.flush()
    achados = cliente_service.listar(db, termo=nome[:10])
    assert any(x.id == c.id for x in achados)


def test_atualizar_cliente_em_caixa_alta(db: Session) -> None:
    c = cliente_service.criar(db, ClienteCreate(nome="Antigo Nome"))
    db.flush()
    cliente_service.atualizar(db, c.id, ClienteUpdate(nome="Novo Nome"))
    # nome, endereço, observação, vendedor saem em CAIXA ALTA.
    assert c.nome == "NOVO NOME"


def test_criar_cliente_campos_novos(db: Session) -> None:
    c = cliente_service.criar(
        db,
        ClienteCreate(
            nome="loja teste",
            telefone="1130001111",
            telefone2="11999998888",
            endereco="rua a, 100",
            vendedor="joão",
            categoria="ruim",
            observacao="só à vista",
        ),
    )
    db.flush()
    assert c.telefone2 == "11999998888"
    assert c.nome == "LOJA TESTE"
    assert c.endereco == "RUA A, 100"
    assert c.vendedor == "JOÃO"
    assert c.observacao == "SÓ À VISTA"
    assert c.categoria == "ruim"


def test_condicao_avista_e_outro(db: Session) -> None:
    # Via controller (caminho do form): "À vista" e "Outro" + descrição.
    a = cliente_controller.criar(db, {"nome": "AV", "cond_tipo": "avista", "ativo": "on"})
    db.flush()
    assert a.condicao_pagto_padrao == "À VISTA"

    o = cliente_controller.criar(
        db, {"nome": "OU", "cond_tipo": "outro", "cond_desc": "30 dias", "ativo": "on"}
    )
    db.flush()
    assert o.condicao_pagto_padrao == "30 DIAS"


def test_excluir_cliente_sem_pedidos(db: Session) -> None:
    c = cliente_service.criar(db, ClienteCreate(nome="Sem Pedidos"))
    db.flush()
    cliente_service.excluir(db, c.id)
    assert cliente_service.listar(db) is not None
    assert db.get(type(c), c.id) is None


def test_excluir_cliente_com_pedidos_falha(db: Session, usuario_admin: Usuario) -> None:
    c = cliente_service.criar(db, ClienteCreate(nome="Com Pedidos"))
    db.flush()
    db.add(Pedido(cliente_id=c.id, vendedor_id=usuario_admin.id))
    db.flush()
    with pytest.raises(RegraNegocioError):
        cliente_service.excluir(db, c.id)


def test_financeiro_pode_ver_mas_nao_criar() -> None:
    client = TestClient(app)
    _login(client, "financeiro")
    # visualiza
    assert client.get("/clientes").status_code == 200
    # não cria
    resp = client.post(
        "/clientes", data={"nome": "Bloqueado", "ativo": "on"}, follow_redirects=False
    )
    assert resp.status_code == 403


def test_excluir_cliente_rbac_nao_admin() -> None:
    """Só admin pode excluir; vendedor recebe 403."""
    client = TestClient(app)
    _login(client, "vendedor")
    resp = client.post("/clientes/999999/excluir", follow_redirects=False)
    assert resp.status_code == 403
