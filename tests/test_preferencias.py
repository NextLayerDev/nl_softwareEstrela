"""Preferências do usuário ("Meu perfil"): pedidos em planilha e abrir direto em Pedidos."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models.enums import Perfil
from app.schemas.usuario import PreferenciasUpdate, UsuarioCreate
from app.services.usuario_service import usuario_service


@pytest.fixture
def cliente(db, usuario_vendedor):
    from app.deps.auth import get_current_user
    from app.deps.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: usuario_vendedor
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_admin_novo_nasce_com_as_duas_ligadas(db) -> None:
    admin = usuario_service.criar(
        db,
        UsuarioCreate(
            nome="A", email="novo.admin@teste.local", senha="Senha@12345", perfil=Perfil.ADMIN
        ),
    )
    vendedor = usuario_service.criar(
        db,
        UsuarioCreate(
            nome="V", email="novo.vend@teste.local", senha="Senha@12345", perfil=Perfil.VENDEDOR
        ),
    )
    assert admin.pedidos_em_planilha and admin.abrir_em_pedidos
    assert not vendedor.pedidos_em_planilha and not vendedor.abrir_em_pedidos


def test_salvar_so_o_que_veio(db, usuario_vendedor) -> None:
    usuario_service.salvar_preferencias(
        db, usuario_vendedor.id, PreferenciasUpdate(abrir_em_pedidos=True)
    )
    assert usuario_vendedor.abrir_em_pedidos is True
    assert usuario_vendedor.pedidos_em_planilha is False  # não veio, não mexe


def test_tela_de_perfil_e_link_no_cabecalho(cliente) -> None:
    tela = cliente.get("/perfil").text
    assert "Abrir direto em Pedidos" in tela and "Pedidos em planilha" in tela
    assert 'href="/perfil"' in tela


def test_post_grava_a_preferencia(cliente, usuario_vendedor) -> None:
    r = cliente.post("/perfil/preferencias", data={"campo": "pedidos_em_planilha", "valor": "true"})
    assert r.json() == {"ok": True}
    assert usuario_vendedor.pedidos_em_planilha is True


def test_post_recusa_campo_desconhecido(cliente, usuario_vendedor) -> None:
    r = cliente.post("/perfil/preferencias", data={"campo": "perfil", "valor": "admin"})
    assert r.json()["ok"] is False
    assert usuario_vendedor.perfil == "vendedor"


def test_pedidos_abre_na_visao_preferida(cliente, db, usuario_vendedor) -> None:
    assert 'id="tabela-planilha"' not in cliente.get("/pedidos").text
    usuario_vendedor.pedidos_em_planilha = True
    db.flush()
    assert "Clique numa célula para editar" in cliente.get("/pedidos").text
    # ?visao= na URL continua mandando
    assert "Clique numa célula para editar" not in cliente.get("/pedidos?visao=lista").text


def test_entrar_por_barra_vai_para_pedidos_mas_painel_continua_acessivel(
    cliente, db, usuario_vendedor
) -> None:
    assert cliente.get("/", follow_redirects=False).status_code == 200
    usuario_vendedor.abrir_em_pedidos = True
    db.flush()
    r = cliente.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/pedidos"
    # Clique em "Painel" de dentro do sistema: mostra o painel.
    dentro = cliente.get(
        "/", headers={"referer": "http://testserver/pedidos"}, follow_redirects=False
    )
    assert dentro.status_code == 200


def test_login_leva_para_pedidos_quando_preferido(db, usuario_vendedor) -> None:
    from app.deps.db import get_db
    from app.main import app

    usuario_vendedor.abrir_em_pedidos = True
    db.flush()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as c:
            r = c.post(
                "/login",
                data={"email": usuario_vendedor.email, "senha": "teste123"},
                follow_redirects=False,
            )
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 303 and r.headers["location"] == "/pedidos"
