"""Testes de RBAC nas rotas de cadastros (doc §7)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _client(perfil: str) -> TestClient:
    c = TestClient(app)
    resp = c.post(
        "/login",
        data={"email": f"{perfil}@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    return c


def test_sem_login_redireciona_login() -> None:
    c = TestClient(app)
    resp = c.get("/produtos", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.parametrize("perfil", ["admin", "vendedor", "financeiro", "funcionario"])
def test_todos_perfis_veem_produtos(perfil: str) -> None:
    assert _client(perfil).get("/produtos").status_code == 200


@pytest.mark.parametrize("perfil", ["vendedor", "financeiro", "funcionario"])
def test_nao_admin_nao_cria_produto(perfil: str) -> None:
    resp = _client(perfil).post(
        "/produtos",
        data={"codigo": f"X-{uuid.uuid4().hex[:6]}", "descricao": "X", "ativo": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_funcionario_nao_acessa_clientes() -> None:
    resp = _client("funcionario").get("/clientes", follow_redirects=False)
    assert resp.status_code == 403


@pytest.mark.parametrize("perfil", ["admin", "vendedor", "financeiro"])
def test_perfis_permitidos_veem_clientes(perfil: str) -> None:
    assert _client(perfil).get("/clientes").status_code == 200


@pytest.mark.parametrize("perfil", ["vendedor", "financeiro", "funcionario"])
def test_nao_admin_nao_acessa_usuarios(perfil: str) -> None:
    resp = _client(perfil).get("/usuarios", follow_redirects=False)
    assert resp.status_code == 403
