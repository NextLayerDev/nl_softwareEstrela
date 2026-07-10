"""Testes da feature de imagem por variação (foto por cor) — armazenada no Postgres (bytea).

A foto é guardada como bytes JPEG em ``produto_variacoes.imagem_dados`` e servida pela
rota ``GET /produtos/variacao/{id}/foto`` (mesma origem, exige login). Sem MinIO/S3.
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.errors import RegraNegocioError
from app.core.imagens import salvar_imagem_variacao
from app.main import app
from app.models.produto import Produto, ProdutoVariacao


def _png_bytes(cor=(180, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), cor).save(buf, "PNG")
    return buf.getvalue()


def _criar_variacao(db: Session) -> ProdutoVariacao:
    """Cria um produto+variação reais (commit) e devolve a variação para testes de rota."""
    p = Produto(codigo=f"IMG-{uuid.uuid4().hex[:8].upper()}", descricao="Produto com foto")
    v = ProdutoVariacao(cor="AZUL")
    p.variacoes.append(v)
    db.add(p)
    db.commit()
    return v


def _limpar(db: Session, variacao: ProdutoVariacao) -> None:
    p = db.get(Produto, variacao.produto_id)
    if p is not None:
        db.delete(p)
    db.commit()


# --------------------------------------------------------------------------- #
# Unit: salvar_imagem_variacao (validação + redimensionamento, sem tocar no DB) #
# --------------------------------------------------------------------------- #


def test_salvar_devolve_bytes_jpeg() -> None:
    dados = salvar_imagem_variacao(1, _png_bytes())
    assert isinstance(dados, bytes)
    assert len(dados) > 0
    # Redimensionou para no máximo 700px de lado.
    img = Image.open(io.BytesIO(dados))
    assert max(img.size) <= 700
    assert img.format == "JPEG"


def test_salvar_rejeita_arquivo_invalido() -> None:
    with pytest.raises(RegraNegocioError):
        salvar_imagem_variacao(1, b"isto nao e uma imagem")


def test_salvar_rejeita_vazio() -> None:
    with pytest.raises(RegraNegocioError):
        salvar_imagem_variacao(1, b"")


# --------------------------------------------------------------------------- #
# Integração: rota de upload + rota que serve a foto                            #
# --------------------------------------------------------------------------- #


def test_upload_armazena_no_postgres_e_rota_serve() -> None:
    db = SessionLocal()
    try:
        v = _criar_variacao(db)
        vid = v.id
    finally:
        db.close()

    client = TestClient(app)
    client.post(
        "/login",
        data={"email": "admin@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    r = client.post(
        f"/produtos/variacao/{vid}/imagem",
        files={"imagem": ("foto.png", _png_bytes(), "image/png")},
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text
    # O card renderizado aponta para a rota de foto (mesma origem).
    assert f"/produtos/variacao/{vid}/foto" in r.text

    # A rota de foto devolve os bytes JPEG (autenticado via cookie).
    rf = client.get(f"/produtos/variacao/{vid}/foto")
    assert rf.status_code == 200
    assert rf.headers["content-type"] == "image/jpeg"
    assert len(rf.content) > 0

    # Persistiu os bytes no Postgres.
    db = SessionLocal()
    try:
        v = db.get(ProdutoVariacao, vid)
        assert v is not None
        assert v.imagem_dados is not None
        assert v.imagem_url is not None
        _limpar(db, v)
    finally:
        db.close()


def test_remover_foto_limpa_bytes() -> None:
    db = SessionLocal()
    try:
        v = _criar_variacao(db)
        vid = v.id
    finally:
        db.close()

    client = TestClient(app)
    client.post(
        "/login",
        data={"email": "admin@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    client.post(
        f"/produtos/variacao/{vid}/imagem",
        files={"imagem": ("foto.png", _png_bytes(), "image/png")},
    )
    r = client.post(f"/produtos/variacao/{vid}/imagem/remover", follow_redirects=False)
    assert r.status_code == 200

    db = SessionLocal()
    try:
        v = db.get(ProdutoVariacao, vid)
        assert v is not None
        assert v.imagem_dados is None
        assert v.imagem_url is None
        # Rota de foto agora 404 (sem foto).
        rf = client.get(f"/produtos/variacao/{vid}/foto")
        assert rf.status_code == 404
        _limpar(db, v)
    finally:
        db.close()


def test_foto_rota_exige_login() -> None:
    db = SessionLocal()
    try:
        v = _criar_variacao(db)
        vid = v.id
    finally:
        db.close()

    client = TestClient(app)  # sem login
    r = client.get(f"/produtos/variacao/{vid}/foto", follow_redirects=False)
    # Sem autenticação -> redireciona ao login (não serve a imagem).
    assert r.status_code != 200

    db = SessionLocal()
    try:
        _limpar(db, db.get(ProdutoVariacao, vid))
    finally:
        db.close()


def test_upload_rbac_bloqueia_nao_admin() -> None:
    """Vendedor e funcionário não podem enviar imagem (403)."""
    db = SessionLocal()
    try:
        v = _criar_variacao(db)
        vid = v.id
    finally:
        db.close()

    client = TestClient(app)
    for email in ("vendedor@estrela.local", "funcionario@estrela.local"):
        tok = client.post(
            "/login", data={"email": email, "senha": "estrela123"}, follow_redirects=False
        ).cookies.get("estrela_token")
        r = client.post(
            f"/produtos/variacao/{vid}/imagem",
            cookies={"estrela_token": tok},
            files={"imagem": ("x.png", _png_bytes(), "image/png")},
        )
        assert r.status_code == 403

    db = SessionLocal()
    try:
        _limpar(db, db.get(ProdutoVariacao, vid))
    finally:
        db.close()
