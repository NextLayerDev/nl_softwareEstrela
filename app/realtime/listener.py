"""Escuta o canal de eventos do Postgres e repassa para os WebSockets do worker.

Roda uma task por worker, iniciada no lifespan do app. A conexão é **dedicada** (não sai do
pool do SQLAlchemy) e `autocommit=True`, senão o LISTEN não vale de imediato e as
notificações ficam presas esperando uma fronteira de transação.

Se o banco piscar, o realtime degrada e volta sozinho — nunca derruba o worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random

import psycopg

from app.core.config import settings
from app.realtime.manager import manager

logger = logging.getLogger("estrela.realtime")

_BACKOFF_MAX = 30.0


async def _escutar(parar: asyncio.Event) -> None:
    """Abre a conexão, escuta o canal e distribui até mandarem parar."""
    async with await psycopg.AsyncConnection.connect(settings.libpq_url, autocommit=True) as aconn:
        await aconn.execute(f'LISTEN "{settings.REALTIME_CHANNEL}"')
        logger.info("Listener de realtime conectado ao canal %s.", settings.REALTIME_CHANNEL)
        async for notificacao in aconn.notifies():
            if parar.is_set():
                break
            # Um evento problemático não pode derrubar a conexão: sem este try, um payload
            # inesperado tiraria o realtime do ar até o backoff reconectar.
            try:
                await _distribuir(notificacao.payload)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("Evento ignorado por erro no tratamento.", exc_info=True)


async def _distribuir(payload: str) -> None:
    envelope = json.loads(payload)
    if envelope.get("tipo") == "sessao.invalidada":
        usuario_id = (envelope.get("dados") or {}).get("usuario_id")
        if usuario_id is not None:
            await manager.desconectar_usuario(usuario_id)
        return
    await manager.fan_out(envelope)


async def supervisionar(parar: asyncio.Event) -> None:
    """Mantém o listener de pé, reconectando com backoff exponencial + jitter."""
    tentativa = 0
    while not parar.is_set():
        try:
            await _escutar(parar)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - banco fora do ar, rede, etc.
            logger.warning("Listener de realtime caiu.", exc_info=True)
        if parar.is_set():
            return
        # Chega aqui tanto por exceção quanto por o servidor ter encerrado a conexão sem
        # erro. Nos dois casos espera antes de reabrir — sem isto, uma conexão que fecha
        # limpa na hora viraria um laço de reconexão sem pausa.
        tentativa += 1
        espera = min(_BACKOFF_MAX, 2**tentativa) + random.uniform(0, 1)
        logger.info("Reconectando o listener em %.1fs.", espera)
        try:
            await asyncio.wait_for(parar.wait(), timeout=espera)
        except TimeoutError:
            tentativa = min(tentativa, 8)  # teto do expoente; o backoff já satura em 30s
