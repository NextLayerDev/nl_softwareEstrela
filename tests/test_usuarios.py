"""Testes do CRUD de usuários: email único, reset de senha, RBAC."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.errors import RegraNegocioError
from app.core.security import verificar_senha
from app.main import app
from app.models.enums import Perfil
from app.schemas.usuario import UsuarioCreate
from app.services.usuario_service import usuario_service


def _login(client: TestClient, perfil: str) -> None:
    resp = client.post(
        "/login",
        data={"email": f"{perfil}@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


def _email() -> str:
    return f"novo-{uuid.uuid4().hex[:8]}@teste.local"


def test_criar_usuario_email_duplicado_falha(db: Session) -> None:
    email = _email()
    usuario_service.criar(
        db, UsuarioCreate(nome="A", email=email, senha="segredo1", perfil=Perfil.VENDEDOR)
    )
    db.flush()
    with pytest.raises(RegraNegocioError):
        usuario_service.criar(
            db, UsuarioCreate(nome="B", email=email, senha="segredo1", perfil=Perfil.ADMIN)
        )


def test_criar_usuario_faz_hash_da_senha(db: Session) -> None:
    u = usuario_service.criar(
        db, UsuarioCreate(nome="Hash", email=_email(), senha="minhasenha", perfil=Perfil.ADMIN)
    )
    db.flush()
    assert u.senha_hash != "minhasenha"
    assert verificar_senha("minhasenha", u.senha_hash)


def test_resetar_senha(db: Session) -> None:
    u = usuario_service.criar(
        db, UsuarioCreate(nome="Reset", email=_email(), senha="antiga1", perfil=Perfil.FINANCEIRO)
    )
    db.flush()
    usuario_service.resetar_senha(db, u.id, "novasenha9")
    assert verificar_senha("novasenha9", u.senha_hash)


def test_resetar_senha_curta_falha(db: Session) -> None:
    u = usuario_service.criar(
        db, UsuarioCreate(nome="Curta", email=_email(), senha="antiga1", perfil=Perfil.ADMIN)
    )
    db.flush()
    with pytest.raises(RegraNegocioError):
        usuario_service.resetar_senha(db, u.id, "123")


def test_nao_admin_nao_acessa_usuarios() -> None:
    client = TestClient(app)
    _login(client, "vendedor")
    assert client.get("/usuarios", follow_redirects=False).status_code == 403


def test_admin_acessa_usuarios() -> None:
    client = TestClient(app)
    _login(client, "admin")
    assert client.get("/usuarios").status_code == 200
