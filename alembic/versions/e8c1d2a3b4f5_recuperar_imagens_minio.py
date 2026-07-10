"""recupera fotos de variação que ficaram órfãs no bucket MinIO (backfill via repo)

Revision ID: e8c1d2a3b4f5
Revises: b7e2c9f4a1d8
Create Date: 2026-07-10

A migration anterior (``b7e2c9f4a1d8``) tentou fazer backfill das fotos antigas baixando-as
do MinIO no momento do ``alembic upgrade head``. Em produção o servidor **não alcança o
MinIO** (foi o timeout que motivou a saída do MinIO), então TODAS as fotos caíram no ramo
"perdidas": ``imagem_url`` virou NULL e ``imagem_dados`` ficou vazio. Os objetos continuaram
no bucket, mas nunca chegaram ao Postgres.

Esta migration repõe essas fotos **sem depender do MinIO em runtime**: os bytes das 129 fotos
foram baixados do bucket (de uma máquina que o alcança) e commitados no repo em
``alembic/versions/_imagens_recuperadas/<variacao_id>.jpg`` (bytes já normalizados pelo
mesmo pipeline de um upload novo). Aqui só lemos os arquivos e gravamos no Postgres.

Idempotente: só grava onde ``imagem_dados IS NULL`` (não sobrescreve foto já reposta, seja
da migration anterior bem-sucedida ou de re-upload manual). Variações que não existem mais
no banco (órfãs do bucket) são ignoradas — o ``WHERE id = :vid`` simplesmente afeta 0 linhas.
Rodar de novo no próximo deploy é no-op.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import text

from alembic import op

log = logging.getLogger("estrela.migration")

revision: str = "e8c1d2a3b4f5"
down_revision: str | None = "b7e2c9f4a1d8"
branch_labels = None
depends_on = None

# Arquivos de imagem commitados junto desta migration. Caminho relativo ao próprio arquivo
# da migration (robusto independente do cwd do alembic).
IMAGENS_DIR = Path(__file__).resolve().parent / "_imagens_recuperadas"


def _caminho_foto(variacao_id: int) -> str:
    """Mesmo formato guardado por um upload novo (rota de foto, mesma origem)."""
    return f"/produtos/variacao/{variacao_id}/foto?v=rec"


def upgrade() -> None:
    bind = op.get_bind()
    if not IMAGENS_DIR.is_dir():
        log.info("recuperação imagens: pasta %s não encontrada — nada a fazer.", IMAGENS_DIR)
        return

    atualizadas, ausentes = 0, 0
    for caminho in sorted(IMAGENS_DIR.glob("*.jpg")):
        try:
            vid = int(caminho.stem)
        except ValueError:
            log.warning("recuperação imagens: arquivo com nome inválido ignorado: %s", caminho.name)
            continue
        dados = caminho.read_bytes()
        if not dados:
            continue
        result = bind.execute(
            text(
                "UPDATE produto_variacoes "
                "SET imagem_dados = :d, imagem_url = :u "
                "WHERE id = :id AND imagem_dados IS NULL"
            ),
            {"d": dados, "u": _caminho_foto(vid), "id": vid},
        )
        if result.rowcount:
            atualizadas += 1
        else:
            ausentes += 1

    log.info(
        "recuperação imagens: %d foto(s) aplicadas, %d já tinham foto ou variação inexistente.",
        atualizadas,
        ausentes,
    )


def downgrade() -> None:
    # Migration de dados: não destrói fotos no downgrade (não há como saber o estado anterior
    # de cada linha). No-op intencional.
    pass
