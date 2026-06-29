"""adiciona status 'separado' ao enum status_pedido

Revision ID: f1a2b3c4d5e6
Revises: de1bbd3b488c
Create Date: 2026-06-29

Estado pós-separação: ao concluir a separação o pedido sai da fila do
funcionário (status 'separado') e segue aguardando faturamento.
"""

from __future__ import annotations

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "de1bbd3b488c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE não pode rodar dentro de uma transação; usa bloco autocommit.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE status_pedido ADD VALUE IF NOT EXISTS 'separado' BEFORE 'faturado'")


def downgrade() -> None:
    # Postgres não suporta remover valores de enum; downgrade é no-op.
    pass
