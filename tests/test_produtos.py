"""Testes do CRUD de produtos: serviço (regras) e rotas (custo oculto + RBAC)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.errors import RegraNegocioError
from app.main import app
from app.models.produto import Produto
from app.schemas.produto import ProdutoCreate, VariacaoCreate
from app.services.produto_service import produto_service

PRECO_CUSTO = "99.77"


def _login(client: TestClient, perfil: str) -> None:
    resp = client.post(
        "/login",
        data={"email": f"{perfil}@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


def _codigo() -> str:
    return f"TST-{uuid.uuid4().hex[:8].upper()}"


def test_criar_produto_codigo_duplicado_falha(db: Session) -> None:
    codigo = _codigo()
    produto_service.criar(db, ProdutoCreate(codigo=codigo, descricao="CANETA AZUL"))
    db.flush()
    with pytest.raises(RegraNegocioError):
        produto_service.criar(db, ProdutoCreate(codigo=codigo, descricao="OUTRA"))


def test_criar_produto_com_variacoes(db: Session) -> None:
    p = produto_service.criar(
        db,
        ProdutoCreate(
            codigo=_codigo(),
            descricao="LAPIS 6B",
            variacoes=[VariacaoCreate(cor="PRETO", estoque_fisico=10)],
        ),
    )
    db.flush()
    assert len(p.variacoes) == 1
    assert p.variacoes[0].cor == "PRETO"


def test_inativar_e_soft_delete(db: Session) -> None:
    p = produto_service.criar(db, ProdutoCreate(codigo=_codigo(), descricao="BORRACHA"))
    db.flush()
    produto_service.inativar(db, p.id)
    assert p.ativo is False


def test_custo_visivel_para_admin() -> None:
    client = TestClient(app)
    _login(client, "admin")
    codigo = _codigo()
    client.post(
        "/produtos",
        data={
            "codigo": codigo,
            "descricao": "PRODUTO CUSTO ADMIN",
            "preco_pouca_qtd": "10",
            "preco_muita_qtd": "8",
            "preco_custo": PRECO_CUSTO,
            "ativo": "on",
        },
        follow_redirects=False,
    )
    resp = client.get(f"/produtos?q={codigo}")
    assert resp.status_code == 200
    # admin vê a coluna de custo
    assert "Custo" in resp.text
    # limpeza: inativa o produto criado
    _remover(codigo)


def test_custo_oculto_para_vendedor() -> None:
    client = TestClient(app)
    _login(client, "admin")
    codigo = _codigo()
    client.post(
        "/produtos",
        data={
            "codigo": codigo,
            "descricao": "PRODUTO CUSTO OCULTO",
            "preco_pouca_qtd": "10",
            "preco_muita_qtd": "8",
            "preco_custo": PRECO_CUSTO,
            "ativo": "on",
        },
        follow_redirects=False,
    )
    vend = TestClient(app)
    _login(vend, "vendedor")
    resp = vend.get(f"/produtos?q={codigo}")
    assert resp.status_code == 200
    # vendedor NÃO vê o preço de custo no HTML
    assert "99,77" not in resp.text
    assert PRECO_CUSTO not in resp.text
    _remover(codigo)


def test_vendedor_nao_cria_produto() -> None:
    client = TestClient(app)
    _login(client, "vendedor")
    resp = client.post(
        "/produtos",
        data={"codigo": _codigo(), "descricao": "X", "ativo": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def _remover(codigo: str) -> None:
    """Remove fisicamente o produto de teste (criado via TestClient/commit real)."""
    from app.core.database import SessionLocal

    s = SessionLocal()
    try:
        p = s.query(Produto).filter(Produto.codigo == codigo).one_or_none()
        if p is not None:
            s.delete(p)
            s.commit()
    finally:
        s.close()
