"""Reduz os perfis a admin e vendedor

A empresa tem dono e vendedores — nada mais. Os perfis `financeiro` e `funcionario`
foram desenhados na fase de descoberta e nunca corresponderam à operação real: quem
fatura é o dono, e quem separa é o mesmo que vende.

Conversão: `financeiro -> admin` (é quem fatura e mexe em contas a receber) e
`funcionario -> vendedor` (o vendedor herdou a fila de separação e as entradas de
mercadoria). `usuarios.perfil` é `VARCHAR(20)`, não um ENUM do Postgres, então não há
tipo a alterar — só dados.

Revision ID: d4b1e7a95c30
Revises: 417902adb707
Create Date: 2026-08-09

"""

from alembic import op

revision = "d4b1e7a95c30"
down_revision = "417902adb707"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE usuarios SET perfil = 'admin' WHERE perfil = 'financeiro'")
    op.execute("UPDATE usuarios SET perfil = 'vendedor' WHERE perfil = 'funcionario'")


def downgrade() -> None:
    """Sem volta.

    O upgrade é destrutivo por natureza: depois da conversão não existe mais o registro
    de quem era `financeiro` e quem já era `admin`. Reverter com um palpite rebaixaria
    admins legítimos e tiraria o acesso de quem fatura. Quem precisar voltar restaura o
    backup e reatribui os perfis pela tela de usuários.
    """
