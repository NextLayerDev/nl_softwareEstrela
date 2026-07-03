"""Persistência de imagens de variação (foto por cor) — MinIO (S3-compatible).

Redimensiona para um tamanho de tela razoável e envia pro bucket configurado em
``settings.S3_BUCKET``. A chave do objeto muda a cada upload (sufixo aleatório) para
evitar cache antigo no navegador.

**Segurança:** o bucket é PRIVADO. O banco guarda só a *chave* do objeto; a URL exibida
no ``<img src="">`` é uma URL assinada (presigned) gerada a cada render, com expiração
curta (``settings.S3_URL_EXPIRA_SEG``). Assim ninguém acessa uma foto sem passar pelo
sistema, e não há bucket com leitura anônima.
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

_BOTO_CONFIG = BotoConfig(
    signature_version="s3v4",
    # timeout curto: se a rede até o MinIO travar, falha rápido em vez de prender o
    # worker do Gunicorn (que só tem poucos workers sync) até o request expirar sozinho.
    connect_timeout=5,
    read_timeout=10,
    retries={"max_attempts": 1},
)


@lru_cache
def _cliente_s3():
    """Client para enviar/apagar objetos — usa o endpoint interno (rápido, sem sair da rede)."""
    # endpoint_url só é passado quando configurado: a moto (testes) não intercepta chamadas
    # com endpoint customizado, então em teste settings.S3_ENDPOINT_URL fica vazio e cai no
    # endpoint padrão da AWS, que a moto mocka normalmente.
    kwargs = {"endpoint_url": settings.S3_ENDPOINT_URL} if settings.S3_ENDPOINT_URL else {}
    return boto3.client(
        "s3",
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name="us-east-1",
        config=_BOTO_CONFIG,
        **kwargs,
    )


@lru_cache
def _cliente_s3_assinatura():
    """Client usado só para gerar presigned URLs — aponta pro endpoint PÚBLICO, senão a URL
    assinada apontaria para o hostname interno e o navegador não conseguiria abri-la."""
    kwargs = {"endpoint_url": settings.S3_PUBLIC_URL} if settings.S3_PUBLIC_URL else {}
    return boto3.client(
        "s3",
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name="us-east-1",
        config=_BOTO_CONFIG,
        **kwargs,
    )


def _chave_de(valor: str | None) -> str | None:
    """Normaliza para a chave do objeto. Aceita a chave direta ou uma URL pública antiga
    (linhas gravadas antes da migração para presigned)."""
    if not valor:
        return None
    if "://" in valor:
        marcador = f"/{settings.S3_BUCKET}/"
        idx = valor.find(marcador)
        if idx == -1:
            return None
        # remove querystring de uma eventual URL assinada
        return valor[idx + len(marcador) :].split("?", 1)[0]
    return valor


def url_para_exibicao(valor: str | None) -> str:
    """Recebe a chave (ou URL antiga) e devolve uma URL assinada temporária para o <img>."""
    chave = _chave_de(valor)
    if not chave:
        return ""
    return _cliente_s3_assinatura().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": chave},
        ExpiresIn=settings.S3_URL_EXPIRA_SEG,
    )


def remover_imagem(valor: str | None) -> None:
    chave = _chave_de(valor)
    if chave is None:
        return
    _cliente_s3().delete_object(Bucket=settings.S3_BUCKET, Key=chave)


def salvar_imagem_variacao(variacao_id: int, conteudo: bytes, anterior: str | None = None) -> str:
    """Valida, redimensiona e envia ao MinIO; remove a anterior. Retorna a CHAVE do objeto."""
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

    if anterior and _chave_de(anterior) != chave:
        remover_imagem(anterior)
    return chave
