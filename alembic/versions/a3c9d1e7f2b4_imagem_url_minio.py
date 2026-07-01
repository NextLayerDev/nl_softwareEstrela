"""imagem_filename -> imagem_url (MinIO)

Revision ID: a3c9d1e7f2b4
Revises: f1a2b3c4d5e6
Create Date: 2026-07-01 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c9d1e7f2b4"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A coluna agora guarda a URL pública completa do objeto no MinIO (app/core/imagens.py) em
    # vez do filename local. Linhas com valor antigo (bare filename, sem "http") são migradas
    # pelo script scripts/migrar_imagens_minio.py, que sobe o arquivo pro bucket e atualiza a URL.
    op.alter_column(
        "produto_variacoes",
        "imagem_filename",
        new_column_name="imagem_url",
        type_=sa.String(length=500),
        existing_type=sa.String(length=255),
    )


def downgrade() -> None:
    op.alter_column(
        "produto_variacoes",
        "imagem_url",
        new_column_name="imagem_filename",
        type_=sa.String(length=255),
        existing_type=sa.String(length=500),
    )
