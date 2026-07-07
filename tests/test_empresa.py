"""Testes da configuração da empresa (emitente) e RBAC do cupom."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.controllers.empresa_controller import empresa_controller
from app.main import app
from app.services.empresa_service import empresa_service


def _login(client: TestClient, perfil: str) -> None:
    resp = client.post(
        "/login",
        data={"email": f"{perfil}@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


def test_salvar_empresa_normaliza(db: Session) -> None:
    empresa = empresa_controller.salvar(
        db,
        {
            "razao_social": "estrela ltda",
            "nome_fantasia": "estrela",
            "cnpj": "00.000.000/0001-00",
            "email": "Contato@Estrela.COM",
            "endereco": "rua a, 100",
        },
    )
    db.flush()
    assert empresa.razao_social == "ESTRELA LTDA"  # razão social em CAIXA ALTA
    assert empresa.nome_fantasia == "ESTRELA"
    assert empresa.endereco == "RUA A, 100"
    assert empresa.email == "contato@estrela.com"  # e-mail em minúsculas


def test_empresa_registro_unico(db: Session) -> None:
    e1 = empresa_controller.salvar(db, {"razao_social": "Um"})
    db.flush()
    e2 = empresa_controller.salvar(db, {"razao_social": "Dois"})
    db.flush()
    # Sempre o mesmo registro (id=1): a segunda gravação sobrescreve a primeira.
    assert e1.id == e2.id == 1
    assert empresa_service.obter(db).razao_social == "DOIS"


def test_empresa_rbac_admin_ok() -> None:
    client = TestClient(app)
    _login(client, "admin")
    assert client.get("/empresa").status_code == 200


def test_empresa_rbac_bloqueia_nao_admin() -> None:
    client = TestClient(app)
    _login(client, "vendedor")
    assert client.get("/empresa", follow_redirects=False).status_code == 403


def test_cupom_rbac_bloqueia_funcionario() -> None:
    """Cupom segue o mesmo RBAC da impressão A4: funcionário não acessa."""
    client = TestClient(app)
    _login(client, "funcionario")
    resp = client.get("/pedidos/999999/cupom", follow_redirects=False)
    assert resp.status_code == 403
