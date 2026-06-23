"""Testes do CRUD de clientes."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
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


def test_atualizar_cliente(db: Session) -> None:
    c = cliente_service.criar(db, ClienteCreate(nome="Antigo Nome"))
    db.flush()
    cliente_service.atualizar(db, c.id, ClienteUpdate(nome="Novo Nome"))
    assert c.nome == "Novo Nome"


def test_inativar_cliente(db: Session) -> None:
    c = cliente_service.criar(db, ClienteCreate(nome="Some Cliente"))
    db.flush()
    cliente_service.inativar(db, c.id)
    assert c.ativo is False


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
