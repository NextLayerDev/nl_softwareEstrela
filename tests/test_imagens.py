"""Testes da feature de imagem por variação (foto por cor)."""

from __future__ import annotations

import io

import pytest
from PIL import Image
from sqlalchemy.orm import Session

from app.core.imagens import VARIACOES_DIR, salvar_imagem_variacao
from app.models.categoria import Categoria
from app.models.enums import EstoqueModo
from app.models.produto import Produto, ProdutoVariacao


def _png_bytes(cor=(180, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), cor).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def variacao(db: Session) -> ProdutoVariacao:
    cat = Categoria(nome="Cat Imagem")
    db.add(cat)
    db.flush()
    produto = Produto(codigo="IMG-1", descricao="Produto com foto", categoria_id=cat.id)
    v = ProdutoVariacao(cor="AZUL", estoque_modo=EstoqueModo.EXATO, estoque_fisico=10)
    produto.variacoes.append(v)
    db.add(produto)
    db.flush()
    return v


def test_imagem_url_none_sem_arquivo(variacao: ProdutoVariacao) -> None:
    assert variacao.imagem_filename is None
    assert variacao.imagem_url is None


def test_salvar_gera_arquivo_e_url(variacao: ProdutoVariacao) -> None:
    nome = salvar_imagem_variacao(variacao.id, _png_bytes())
    try:
        variacao.imagem_filename = nome
        caminho = VARIACOES_DIR / nome
        assert caminho.exists() and caminho.stat().st_size > 0
        assert variacao.imagem_url == f"/uploads/variacoes/{nome}"
        # foi convertida para JPEG
        assert nome.endswith(".jpg")
    finally:
        (VARIACOES_DIR / nome).unlink(missing_ok=True)


def test_substituir_remove_anterior(variacao: ProdutoVariacao) -> None:
    antigo = salvar_imagem_variacao(variacao.id, _png_bytes())
    novo = salvar_imagem_variacao(variacao.id, _png_bytes((20, 120, 40)), anterior=antigo)
    try:
        assert novo != antigo
        assert not (VARIACOES_DIR / antigo).exists()
        assert (VARIACOES_DIR / novo).exists()
    finally:
        (VARIACOES_DIR / novo).unlink(missing_ok=True)


def test_arquivo_invalido_rejeitado(variacao: ProdutoVariacao) -> None:
    from app.core.errors import RegraNegocioError

    with pytest.raises(RegraNegocioError):
        salvar_imagem_variacao(variacao.id, b"isto nao e uma imagem")


def test_upload_rbac_bloqueia_nao_admin() -> None:
    """Vendedor e funcionário não podem enviar imagem (403)."""
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.main import app

    db = SessionLocal()
    vid = db.scalar(select(ProdutoVariacao.id).limit(1))
    db.close()
    if vid is None:
        pytest.skip("Sem variações no banco de dev.")

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
