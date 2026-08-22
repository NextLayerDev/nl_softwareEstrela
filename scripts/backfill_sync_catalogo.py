"""Enfileira, de uma vez, todos os produtos já marcados `publicar_catalogo=True`.

Necessário só na virada: sem isto, produtos já existentes só entram na fila de
sincronização do catálogo externo (omni_respostaMax) na próxima vez que forem editados
ou tiverem estoque movimentado. É idempotente (pode rodar de novo sem duplicar nada —
`sync_outbox_repo.enfileirar` substitui a pendente anterior da mesma linha).

Uso:
    uv run python scripts/backfill_sync_catalogo.py
    # ou, de fora, entrando no container:
    docker compose exec -T app uv run python scripts/backfill_sync_catalogo.py

Só grava na fila (sync_outbox) — não envia HTTP. O envio é feito pelo job
`job_flush_sync_outbox` (app/jobs.py), que já roda em produção.
"""

from __future__ import annotations

import sys
from urllib.parse import urlsplit

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.produto import Produto
from app.services.catalogo_sync_service import catalogo_sync_service


def _dsn_sem_senha(dsn: str) -> str:
    p = urlsplit(dsn)
    return f"{p.hostname}:{p.port or 5432}{p.path}"


def main() -> int:
    print(f"Banco alvo : {_dsn_sem_senha(settings.DATABASE_URL)}")
    print(f"ENV        : {settings.ENV}")

    db = SessionLocal()
    try:
        produtos = list(db.scalars(select(Produto).where(Produto.publicar_catalogo.is_(True))))
        print(f"Produtos com publicar_catalogo=True: {len(produtos)}")
        for produto in produtos:
            catalogo_sync_service.enfileirar_produto(db, produto)
        db.commit()
        print(f"\n[ok] {len(produtos)} produto(s) enfileirado(s) para sincronização.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
