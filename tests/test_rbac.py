"""Testes de RBAC das rotas (doc §7), na matriz de dois perfis.

A empresa tem admin e vendedor. O vendedor faz o dia a dia inteiro; o que é só do
admin é faturamento (faturar + /financeiro + valorização) e administração do sistema
(usuários, empresa, importação).
"""

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


@pytest.mark.parametrize("perfil", ["admin", "vendedor"])
def test_ambos_perfis_veem_produtos(perfil: str) -> None:
    assert _client(perfil).get("/produtos").status_code == 200


@pytest.mark.parametrize(
    "rota",
    ["/", "/produtos/novo", "/pedidos", "/separacao", "/clientes", "/estoque", "/relatorios"],
)
def test_vendedor_faz_o_dia_a_dia(rota: str) -> None:
    """O vendedor abre tudo o que é operação de loja."""
    assert _client("vendedor").get(rota).status_code == 200


def test_vendedor_cria_produto() -> None:
    """Cadastrar produto deixou de ser exclusivo do admin."""
    resp = _client("vendedor").post(
        "/produtos",
        data={"codigo": f"X-{uuid.uuid4().hex[:6]}", "descricao": "X", "ativo": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


@pytest.mark.parametrize(
    "rota", ["/financeiro", "/usuarios", "/empresa", "/importacao", "/relatorios/valorizacao"]
)
def test_vendedor_nao_acessa_o_que_e_do_admin(rota: str) -> None:
    resp = _client("vendedor").get(rota, follow_redirects=False)
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "rota", ["/financeiro", "/usuarios", "/empresa", "/importacao", "/relatorios/valorizacao"]
)
def test_admin_acessa_o_que_e_dele(rota: str) -> None:
    assert _client("admin").get(rota).status_code == 200


def test_login_com_sessao_vai_direto_para_o_painel() -> None:
    """Quem já está logado não vê o formulário de novo.

    Nos terminais em modo aplicativo o atalho aponta para uma URL fixa; sem isso quem
    já tem cookie válido caía num login pedindo o que a sessão já sabe.
    """
    resp = _client("vendedor").get("/login", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_login_sem_sessao_continua_mostrando_o_formulario() -> None:
    resp = TestClient(app).get("/login")
    assert resp.status_code == 200
    assert 'name="senha"' in resp.text
