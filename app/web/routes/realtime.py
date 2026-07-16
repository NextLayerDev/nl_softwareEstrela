"""Endpoint WebSocket dos eventos em tempo real.

Autentica com o mesmo cookie httpOnly das rotas HTTP. Não dá para reusar o
``get_current_user`` via ``Depends``: ele é tipado com ``Request`` e um handler de WebSocket
recebe um ``WebSocket``. As checagens abaixo são as mesmas dele (token válido, usuário ativo,
``token_version`` em dia) — se elas mudarem lá, mude aqui.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.core.database import SessionLocal
from app.core.security import decodificar_token
from app.deps.auth import COOKIE_NOME
from app.models.usuario import Usuario
from app.realtime.manager import manager

logger = logging.getLogger("estrela.realtime")

router = APIRouter()


def _identificar(token: str) -> tuple[int, str] | None:
    """Valida o token e devolve (usuario_id, perfil).

    Roda numa thread do pool (a camada de dados é sync). Devolve primitivos de propósito:
    um objeto ORM sobreviveria ao fechamento da Session e viraria instância detached.
    """
    payload = decodificar_token(token)
    if not payload or "sub" not in payload:
        return None
    with SessionLocal() as db:
        usuario = db.scalar(select(Usuario).where(Usuario.id == int(payload["sub"])))
        if usuario is None or not usuario.ativo:
            return None
        if int(payload.get("tv", 0)) != usuario.token_version:
            return None
        return usuario.id, usuario.perfil


@router.websocket("/ws")
async def eventos_ws(websocket: WebSocket) -> None:
    token = websocket.cookies.get(COOKIE_NOME)
    ident = await run_in_threadpool(_identificar, token) if token else None
    if ident is None:
        # 1008 = policy violation. Fecha antes do accept: sem sessão, sem socket.
        await websocket.close(code=1008)
        return

    usuario_id, perfil = ident
    await websocket.accept()
    conexao = manager.registrar(websocket, usuario_id, perfil)
    logger.debug(
        "WS conectado: usuário %s (%s). Conexões no worker: %s", usuario_id, perfil, manager.total
    )
    try:
        while True:
            # O cliente só manda heartbeat; o fluxo real é servidor → cliente. Ler mantém a
            # conexão viva e é como detectamos a desconexão.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.remover(conexao)
