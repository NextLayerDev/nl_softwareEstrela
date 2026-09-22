"""Preferências de cada usuário: pedidos em planilha e abrir direto em Pedidos.

Duas escolhas por pessoa, feitas na tela "Meu perfil":

- `pedidos_em_planilha`: /pedidos abre no modo planilha (a folha amarela), em vez da
  lista. Antes isso ficava no localStorage do navegador — e num terminal compartilhado
  a escolha de um vendedor virava a do próximo.
- `abrir_em_pedidos`: depois do login, cai direto em /pedidos em vez do Painel.

Expand puro: duas colunas com default, a imagem anterior sobe com o schema novo sem
perceber. Os admins já começam com as duas ligadas (pedido da operação).

Revision ID: f56a3eb45b15
Revises: cec9c2440066
Create Date: 2026-09-22 17:36:46.439939

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f56a3eb45b15"
down_revision: str | None = "cec9c2440066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usuarios",
        sa.Column("pedidos_em_planilha", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "usuarios",
        sa.Column("abrir_em_pedidos", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE usuarios SET pedidos_em_planilha = true, abrir_em_pedidos = true "
        "WHERE perfil = 'admin'"
    )


def downgrade() -> None:
    op.drop_column("usuarios", "abrir_em_pedidos")
    op.drop_column("usuarios", "pedidos_em_planilha")
