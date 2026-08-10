"""Índices do código normalizado (busca por código sem traço e sem caixa)

A busca de produto por código passou a comparar o código NORMALIZADO — só letras e
dígitos, em maiúsculas — para que "ch1086" ache "CH-1086". Isso é uma expressão, não a
coluna, então o `ix_produtos_codigo` deixa de ser usado por essas consultas.

Os índices abaixo são funcionais sobre exatamente a mesma expressão que o
`app/core/codigos.coluna_normalizada` gera, e usam `gin_trgm_ops` porque a busca casa
por PEDAÇO (`%termo%`) — um btree só serviria para prefixo. Mudar a fórmula no Python
sem refazer estes índices faz a busca voltar a varrer a tabela.

`upper()` e `regexp_replace()` são IMMUTABLE no Postgres, então a expressão é indexável.

Revision ID: f2b8c1d47a09
Revises: a7c3f0d81e64
Create Date: 2026-08-10

"""

from alembic import op

revision = "f2b8c1d47a09"
down_revision = "a7c3f0d81e64"
branch_labels = None
depends_on = None

_EXPR_PRODUTO = "upper(regexp_replace(codigo, '[^A-Za-z0-9]', '', 'g'))"
_EXPR_ALT = "upper(regexp_replace(codigo_alt, '[^A-Za-z0-9]', '', 'g'))"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX ix_produtos_codigo_norm ON produtos "
        f"USING gin (({_EXPR_PRODUTO}) gin_trgm_ops)"
    )
    op.execute(
        f"CREATE INDEX ix_produto_codigos_alt_norm ON produto_codigos_alt "
        f"USING gin (({_EXPR_ALT}) gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_produto_codigos_alt_norm")
    op.execute("DROP INDEX IF EXISTS ix_produtos_codigo_norm")
