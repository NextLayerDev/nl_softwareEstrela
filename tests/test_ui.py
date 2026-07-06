"""Testes de regressão das melhorias de UI/acessibilidade/usabilidade."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def _admin() -> TestClient:
    c = TestClient(app)
    r = c.post(
        "/login",
        data={"email": "admin@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    return c


def test_login_tem_aviso_caps_lock_e_meta_ios() -> None:
    t = TestClient(app).get("/login").text
    assert "caps-hint" in t
    assert "apple-mobile-web-app-capable" in t


def test_tabelas_tem_scope_col() -> None:
    """Acessibilidade: cabeçalhos de tabela com scope."""
    for url in ("/clientes", "/produtos", "/", "/usuarios"):
        t = _admin().get(url).text
        assert 'scope="col"' in t, url


def test_lista_clientes_usa_modal_de_confirmacao() -> None:
    t = _admin().get("/clientes").text
    # macro confirmar_botao (data-action) + modal (dialog acessível)
    assert "data-action=" in t
    assert 'role="dialog"' in t
    # não deve mais usar o confirm() nativo nas linhas
    assert "return confirm(" not in t


def test_flash_de_sucesso_aparece_via_query_ok() -> None:
    t = _admin().get("/clientes?ok=Cliente+salvo+com+sucesso.").text
    assert "alerta-ok" in t
    assert "Cliente salvo com sucesso." in t


def test_categoria_cliente_tem_aria_label(db) -> None:
    """Cor não é o único indicador da categoria (WCAG 1.4.1)."""
    from app.core.templates import templates
    from app.models.enums import CATEGORIA_CLIENTE_INFO

    class _Cli:
        nome = "X"
        cnpj_cpf = telefone = vendedor = condicao_pagto_padrao = None
        categoria = "ruim"

    html = templates.get_template("clientes/_linhas.html").render(
        clientes=[_Cli()], categorias=CATEGORIA_CLIENTE_INFO, pode_editar=False
    )
    assert 'role="img"' in html
    assert "Categoria: Ruim" in html
