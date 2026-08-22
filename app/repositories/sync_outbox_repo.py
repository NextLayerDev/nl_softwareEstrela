from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.sync_outbox import SyncOutbox

MAX_TENTATIVAS = 10


class SyncOutboxRepository:
    def enfileirar(
        self, db: Session, entidade: str, entidade_id: int, payload: dict[str, Any]
    ) -> SyncOutbox:
        """Grava a intenção de sincronizar. Substitui qualquer pendente da mesma entidade.

        Várias mutações seguidas do mesmo produto (ex.: várias movimentações de estoque
        em sequência) não devem virar N envios — só o snapshot mais recente importa.
        """
        db.execute(
            delete(SyncOutbox).where(
                SyncOutbox.entidade == entidade,
                SyncOutbox.entidade_id == entidade_id,
                SyncOutbox.status == "pendente",
            )
        )
        row = SyncOutbox(entidade=entidade, entidade_id=entidade_id, payload=payload)
        db.add(row)
        db.flush()
        return row

    def pegar_pendentes(self, db: Session, limite: int = 50) -> list[SyncOutbox]:
        stmt = (
            select(SyncOutbox)
            .where(SyncOutbox.status == "pendente")
            .order_by(SyncOutbox.criado_em)
            .limit(limite)
        )
        return list(db.scalars(stmt))

    def marcar_enviado(self, db: Session, row: SyncOutbox) -> None:
        row.status = "enviado"
        row.erro = None
        db.flush()

    def marcar_erro(self, db: Session, row: SyncOutbox, mensagem: str) -> None:
        row.tentativas += 1
        row.erro = mensagem[:2000]
        if row.tentativas >= MAX_TENTATIVAS:
            row.status = "falhou"
        db.flush()


sync_outbox_repo = SyncOutboxRepository()
