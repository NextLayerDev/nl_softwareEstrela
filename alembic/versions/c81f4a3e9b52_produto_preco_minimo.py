"""Preço mínimo de venda por produto

O vendedor pode digitar o preço unitário livremente na hora do pedido, e o único
guarda-corpo era o limite de desconto em % — contornável baixando o preço direto.
`produtos.preco_minimo` é o piso por unidade que o admin define; ZERO significa "sem
piso", que é o estado de todo produto até alguém revisar.

Revision ID: c81f4a3e9b52
Revises: b6d9f21c47ae
Create Date: 2026-08-09

"""

import sqlalchemy as sa

from alembic import op

revision = "c81f4a3e9b52"
down_revision = "b6d9f21c47ae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "produtos",
        sa.Column(
            "preco_minimo",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("produtos", "preco_minimo")
