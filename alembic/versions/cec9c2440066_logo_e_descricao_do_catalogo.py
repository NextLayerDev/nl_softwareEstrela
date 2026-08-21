"""Logo e descrição para a capa do Catálogo Inteligente

O catálogo em PDF abre com uma capa: logo, nome da loja, uma linha do que ela vende e o
telefone. A `empresa_config` tinha o nome e o telefone; faltavam o logo e a descrição.

Os bytes do logo ficam no Postgres, como as fotos de variação — offline-first, sem
depender de armazenamento externo numa máquina que roda sozinha no cliente.

Colunas todas anuláveis: sem logo a capa cai no nome, e o catálogo continua saindo.

Revision ID: cec9c2440066
Revises: 84c818a23a3f
Create Date: 2026-08-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cec9c2440066"
down_revision: str | None = "84c818a23a3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("empresa_config", sa.Column("logo_dados", sa.LargeBinary(), nullable=True))
    op.add_column("empresa_config", sa.Column("logo_url", sa.String(length=500), nullable=True))
    op.add_column("empresa_config", sa.Column("descricao_catalogo", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("empresa_config", "descricao_catalogo")
    op.drop_column("empresa_config", "logo_url")
    op.drop_column("empresa_config", "logo_dados")
