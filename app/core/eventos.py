"""Emissão de eventos de realtime.

O barramento é o próprio Postgres (LISTEN/NOTIFY). O ``NOTIFY`` é **transacional**: a
mensagem só é entregue quando a transação commita e é descartada no rollback. Isso encaixa
exatamente na regra do projeto de que os services não commitam (quem commita é o ``get_db``
no fim do request) — o evento vira uma extensão natural do "tudo ou nada", sem buffer de
aplicação. Também cobre de graça os dois caminhos que commitam fora do request:
``app/jobs.py`` (APScheduler) e ``app/importer/carga.py`` (ETL).

Os services só conhecem a função ``emitir``. Quem escuta e distribui é ``app/realtime/``.

REGRA: o payload leva apenas ids/primitivos. NUNCA HTML, ``preco_custo``, margem ou
``senha_hash`` — a UI re-busca o fragmento no endpoint HTTP, que já aplica o RBAC.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import Perfil

logger = logging.getLogger("estrela.eventos")

# O NOTIFY do Postgres tem limite de 8000 bytes de payload. Cortamos antes com folga.
_LIMITE_PAYLOAD = 7000

# Audiências prontas (evita audiência "stringly-typed" espalhada pelos services).
TODOS: tuple[str, ...] = (
    Perfil.ADMIN.value,
    Perfil.VENDEDOR.value,
    Perfil.FINANCEIRO.value,
    Perfil.FUNCIONARIO.value,
)
SEP_AUD: tuple[str, ...] = (Perfil.ADMIN.value, Perfil.FUNCIONARIO.value)
FIN_AUD: tuple[str, ...] = (Perfil.ADMIN.value, Perfil.FINANCEIRO.value)
ADMIN_AUD: tuple[str, ...] = (Perfil.ADMIN.value,)


def emitir(
    db: Session,
    tipo: str,
    dados: dict[str, Any],
    *,
    audiencia: Sequence[str],
    vendedor_id: int | None = None,
    target_usuario_id: int | None = None,
    silencioso: bool = False,
) -> None:
    """Enfileira um evento no canal do Postgres, entregue somente se a transação commitar.

    :param audiencia: perfis que recebem o evento.
    :param vendedor_id: se preenchido, o dono do pedido também recebe (mesmo fora da audiência).
    :param target_usuario_id: entrega dirigida a um único usuário (ex.: ``sessao.invalidada``).
    :param silencioso: o cliente atualiza a tela mas não mostra toast (ex.: efeito colateral).

    Nunca levanta exceção: um problema no realtime não pode derrubar uma regra de negócio.
    """
    if not settings.REALTIME_ENABLED:
        return

    envelope: dict[str, Any] = {
        "tipo": tipo,
        "audiencia": list(audiencia),
        "vendedor_id": vendedor_id,
        "target_usuario_id": target_usuario_id,
        "silencioso": silencioso,
        "dados": dados,
        "ts": datetime.now(UTC).isoformat(),
    }
    try:
        carga = json.dumps(envelope, default=str, ensure_ascii=False)
        if len(carga.encode("utf-8")) > _LIMITE_PAYLOAD:
            # Degrada para um envelope mínimo: o cliente ainda re-busca o fragmento e
            # converge, mesmo sem os dados detalhados.
            logger.warning("Evento %s excedeu o limite do NOTIFY; enviando sem 'dados'.", tipo)
            envelope["dados"] = {}
            envelope["truncado"] = True
            carga = json.dumps(envelope, default=str, ensure_ascii=False)
        db.execute(
            text("SELECT pg_notify(:canal, :carga)"),
            {"canal": settings.REALTIME_CHANNEL, "carga": carga},
        )
    except Exception:  # noqa: BLE001 - realtime é best-effort, jamais quebra o fluxo
        logger.warning("Falha ao emitir o evento %s.", tipo, exc_info=True)
