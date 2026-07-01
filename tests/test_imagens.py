"""Testes da feature de imagem por variação (foto por cor) — S3/MinIO mockado (moto)."""

from __future__ import annotations

import io

import pytest
from moto import mock_aws
from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.imagens import _cliente_s3, salvar_imagem_variacao
from app.models.categoria import Categoria
from app.models.enums import EstoqueModo
from app.models.produto import Produto, ProdutoVariacao


def _png_bytes(cor=(180, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), cor).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _s3_mock(monkeypatch: pytest.MonkeyPatch):
    # Credenciais falsas no formato que a moto espera — nunca toca o MinIO real.
    monkeypatch.setattr(settings, "S3_ACCESS_KEY", "testing")
    monkeypatch.setattr(settings, "S3_SECRET_KEY", "testing")
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", "")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    _cliente_s3.cache_clear()
    with mock_aws():
        _cliente_s3().create_bucket(Bucket=settings.S3_BUCKET)
        yield
    _cliente_s3.cache_clear()


def _chave(url: str) -> str:
    return url.split(f"{settings.S3_BUCKET}/", 1)[1]


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
    assert variacao.imagem_url is None


def test_salvar_gera_url_publica_e_objeto(variacao: ProdutoVariacao) -> None:
    url = salvar_imagem_variacao(variacao.id, _png_bytes())
    assert url.startswith(f"{settings.S3_PUBLIC_URL}/{settings.S3_BUCKET}/variacoes/")
    assert url.endswith(".jpg")  # foi convertida para JPEG
    obj = _cliente_s3().get_object(Bucket=settings.S3_BUCKET, Key=_chave(url))
    assert obj["ContentLength"] > 0


def test_substituir_remove_anterior(variacao: ProdutoVariacao) -> None:
    from botocore.exceptions import ClientError

    antiga = salvar_imagem_variacao(variacao.id, _png_bytes())
    nova = salvar_imagem_variacao(variacao.id, _png_bytes((20, 120, 40)), anterior=antiga)
    assert nova != antiga
    with pytest.raises(ClientError):
        _cliente_s3().get_object(Bucket=settings.S3_BUCKET, Key=_chave(antiga))
    obj = _cliente_s3().get_object(Bucket=settings.S3_BUCKET, Key=_chave(nova))
    assert obj["ContentLength"] > 0


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
