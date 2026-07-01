"""Persistência de imagens de variação (foto por cor) — MinIO (S3-compatible).

Redimensiona para um tamanho de tela razoável e envia pro bucket configurado em
``settings.S3_BUCKET``. A chave do objeto muda a cada upload (sufixo aleatório) para
evitar cache antigo no navegador. O banco guarda só a URL pública final.
"""

from __future__ import annotations

import io
import uuid
from functools import lru_cache

import boto3
from botocore.client import Config as BotoConfig
from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.core.errors import RegraNegocioError

_MAX_BYTES = 8 * 1024 * 1024  # 8 MB
_MAX_LADO = 700  # px
_PREFIXO = "variacoes"


@lru_cache
def _cliente_s3():
    # endpoint_url só é passado quando configurado: a moto (testes) não intercepta chamadas
    # com endpoint customizado, então em teste settings.S3_ENDPOINT_URL fica vazio e cai no
    # endpoint padrão da AWS, que a moto mocka normalmente.
    kwargs = {"endpoint_url": settings.S3_ENDPOINT_URL} if settings.S3_ENDPOINT_URL else {}
    return boto3.client(
        "s3",
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name="us-east-1",
        # timeout curto: se a rede até o MinIO travar, falha rápido em vez de prender o
        # worker do Gunicorn (que só tem poucos workers sync) até o request expirar sozinho.
        config=BotoConfig(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=10,
            retries={"max_attempts": 1},
        ),
        **kwargs,
    )


def _url_publica(chave: str) -> str:
    return f"{settings.S3_PUBLIC_URL}/{settings.S3_BUCKET}/{chave}"


def _chave_da_url(url: str) -> str | None:
    prefixo = f"{settings.S3_PUBLIC_URL}/{settings.S3_BUCKET}/"
    if not url.startswith(prefixo):
        return None
    return url[len(prefixo) :]


def remover_imagem(url: str | None) -> None:
    if not url:
        return
    chave = _chave_da_url(url)
    if chave is None:
        return
    _cliente_s3().delete_object(Bucket=settings.S3_BUCKET, Key=chave)


def salvar_imagem_variacao(variacao_id: int, conteudo: bytes, anterior: str | None = None) -> str:
    """Valida, redimensiona e envia ao MinIO; remove a anterior. Retorna a URL pública."""
    if not conteudo:
        raise RegraNegocioError("Arquivo de imagem vazio.")
    if len(conteudo) > _MAX_BYTES:
        raise RegraNegocioError("Imagem muito grande (máximo 8 MB).")
    try:
        img = Image.open(io.BytesIO(conteudo))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise RegraNegocioError("Arquivo não é uma imagem válida.") from exc

    img = img.convert("RGB")
    img.thumbnail((_MAX_LADO, _MAX_LADO))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82, optimize=True)

    chave = f"{_PREFIXO}/{variacao_id}_{uuid.uuid4().hex[:8]}.jpg"
    _cliente_s3().put_object(
        Bucket=settings.S3_BUCKET,
        Key=chave,
        Body=buf.getvalue(),
        ContentType="image/jpeg",
    )

    url = _url_publica(chave)
    if anterior and anterior != url:
        remover_imagem(anterior)
    return url
