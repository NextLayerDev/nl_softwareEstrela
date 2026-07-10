"""imagens das variações passam a morar no Postgres (bytea), sem MinIO/S3

Revision ID: b7e2c9f4a1d8
Revises: a9c4d7e1f2b3
Create Date: 2026-07-10

Antes a foto de cada cor era um objeto num bucket MinIO remoto
(api-nextpy-minio.1nwz76.easyplan.host), e ``imagem_url`` guardava a chave do objeto;
a exibição usava URL assinada. Isso quebra o offline-first (CLAUDE.md §16): se o servidor
não alcança o MinIO, o upload dá timeout (erro 500) e a foto não carrega.

Agora os bytes do JPEG ficam em ``produto_variacoes.imagem_dados`` (bytea) e são servidos
pela rota ``GET /produtos/variacao/{id}/foto`` (mesma origem, exige login). ``imagem_url``
passa a guardar o caminho dessa rota.

Backfill: para cada variação com ``imagem_url`` antigo (chave MinIO), tenta baixar a imagem
do MinIO e guardar os bytes. Se o MinIO estiver inalcançável (caso do servidor offline), o
download falha e o ``imagem_url`` é zerado — a foto deixa de ser referenciada, mas o objeto
continua no bucket (recuperável depois de uma máquina com acesso). Best-effort, nunca derruba
o upgrade.
"""

from __future__ import annotations

import logging
import uuid

import boto3
import sqlalchemy as sa
from botocore.client import Config as BotoConfig
from sqlalchemy import text

from alembic import op

revision: str = "b7e2c9f4a1d8"
down_revision: str | None = "a9c4d7e1f2b3"
branch_labels = None
depends_on = None

log = logging.getLogger("estrela.migration")

_BOTO_CONFIG = BotoConfig(
    signature_version="s3v4", connect_timeout=5, read_timeout=15, retries={"max_attempts": 1}
)


def _caminho_foto(variacao_id: int) -> str:
    return f"/produtos/variacao/{variacao_id}/foto?v={uuid.uuid4().hex[:8]}"


def upgrade() -> None:
    bind = op.get_bind()

    # 1) nova coluna bytea para os bytes da foto
    op.add_column("produto_variacoes", sa.Column("imagem_dados", sa.LargeBinary(), nullable=True))

    # 2) backfill best-effort das fotos que ainda apontam para o MinIO
    cfg = _config_minio(bind)
    migradas = 0
    perdidas = 0
    if cfg:
        cliente = _cliente_minio(cfg)
        # chaves antigas = imagem_url não nulo E que ainda não é um caminho de rota (não começa com /)
        rows = bind.execute(
            text(
                "SELECT id, imagem_url FROM produto_variacoes "
                "WHERE imagem_url IS NOT NULL AND imagem_url NOT LIKE '/%'"
            )
        ).fetchall()
        for vid, chave in rows:
            dados = _baixar(cliente, cfg["bucket"], chave)
            if dados:
                bind.execute(
                    text(
                        "UPDATE produto_variacoes SET imagem_dados = :d, imagem_url = :u WHERE id = :id"
                    ),
                    {"d": dados, "u": _caminho_foto(vid), "id": vid},
                )
                migradas += 1
            else:
                bind.execute(
                    text("UPDATE produto_variacoes SET imagem_url = NULL WHERE id = :id"),
                    {"id": vid},
                )
                perdidas += 1
    else:
        # sem configuração de MinIO: zera os links antigos (quebrados) para consistência
        n = bind.execute(
            text(
                "UPDATE produto_variacoes SET imagem_url = NULL "
                "WHERE imagem_url IS NOT NULL AND imagem_url NOT LIKE '/%'"
            )
        ).rowcount
        perdidas = n

    log.info("backfill imagens: %d migradas, %d links antigos zerados", migradas, perdidas)


def downgrade() -> None:
    op.drop_column("produto_variacoes", "imagem_dados")


def _config_minio(bind) -> dict | None:
    """Lê as settings de S3 do ambiente (.env). Retorna None se não configurado."""
    try:
        from app.core.config import settings

        if not settings.S3_ACCESS_KEY or not settings.S3_BUCKET:
            return None
        return {
            "endpoint": settings.S3_ENDPOINT_URL or None,
            "access_key": settings.S3_ACCESS_KEY,
            "secret_key": settings.S3_SECRET_KEY,
            "bucket": settings.S3_BUCKET,
        }
    except Exception:  # noqa: BLE001
        return None


def _cliente_minio(cfg: dict):
    kwargs = {"endpoint_url": cfg["endpoint"]} if cfg["endpoint"] else {}
    return boto3.client(
        "s3",
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name="us-east-1",
        config=_BOTO_CONFIG,
        **kwargs,
    )


def _baixar(cliente, bucket: str, chave: str) -> bytes | None:
    """Baixa o objeto do MinIO. Devolve None se inalcançável/inexistente."""
    try:
        obj = cliente.get_object(Bucket=bucket, Key=chave)
        return obj["Body"].read()
    except Exception:  # noqa: BLE001
        return None
