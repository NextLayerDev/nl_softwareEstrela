"""Advisory locks do Postgres para eleger um único executor entre os workers.

O `lifespan` do FastAPI roda uma vez POR WORKER do Gunicorn (são 3 em produção), então o
APScheduler sobe 3 vezes e todo job agendado dispara 3 vezes. Isso já acontece hoje com
o `marcar_atrasados` — passa despercebido só porque ele é idempotente.

Fica em `core` e não em `jobs` para o resto do app poder usar sem importar o scheduler.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

# Chaves arbitrárias e fixas. Não reutilize entre jobs diferentes.
LOCK_CI = 815_001
LOCK_ATRASADOS = 815_002


def tenta_lock(db: Session, chave: int) -> bool:
    """True se ESTE worker pegou o lock. Liberado sozinho no fim da transação.

    Versão `xact`: não exige unlock explícito, então um worker que morra no meio não
    deixa o lock preso para sempre.
    """
    return bool(db.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": chave}))
