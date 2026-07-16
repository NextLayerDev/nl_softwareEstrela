"""Registro das conexões WebSocket do processo e distribuição dos eventos.

O registro é **por processo**: em produção rodam 3 workers do Gunicorn, cada um com o seu
gerenciador e o seu listener. Como o Postgres entrega o NOTIFY a todos os workers que
escutam, e cada WebSocket vive em exatamente um worker, cada cliente recebe cada evento uma
única vez — sem precisar de dedup entre processos.

INVARIANTE: todo acesso vem do event loop do worker (handlers de WS e task do listener),
nunca de uma thread do pool. Por isso não há lock aqui.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from starlette.websockets import WebSocket

from app.models.enums import tem_perfil

logger = logging.getLogger("estrela.realtime")

# Código de fechamento usado quando a sessão do usuário é invalidada (troca de perfil,
# desativação ou reset de senha). O cliente reage mandando o terminal para o /login.
CODIGO_SESSAO_INVALIDADA = 4001


@dataclass(eq=False)
class Conexao:
    """Um terminal conectado. `eq=False` para o dataclass ser hashável por identidade."""

    ws: WebSocket
    usuario_id: int
    perfil: str


class GerenciadorConexoes:
    def __init__(self) -> None:
        self._conexoes: set[Conexao] = set()

    @property
    def total(self) -> int:
        return len(self._conexoes)

    def registrar(self, ws: WebSocket, usuario_id: int, perfil: str) -> Conexao:
        conexao = Conexao(ws=ws, usuario_id=usuario_id, perfil=perfil)
        self._conexoes.add(conexao)
        return conexao

    def remover(self, conexao: Conexao) -> None:
        self._conexoes.discard(conexao)

    def destina(self, conexao: Conexao, envelope: dict[str, Any]) -> bool:
        """Decide se esta conexão recebe o evento — o RBAC do realtime.

        Entrega dirigida a um usuário vence tudo; senão o dono do pedido recebe mesmo fora
        da audiência; senão vale o perfil.

        O `dev` é superusuário e recebe qualquer audiência. A regra fica AQUI, e não
        espalhada nas tuplas de audiência do `eventos.py`, para que uma audiência nova
        (escrita inline num service) não esqueça dele e o deixe com a tela parada.
        """
        alvo = envelope.get("target_usuario_id")
        if alvo is not None:
            return conexao.usuario_id == alvo
        dono = envelope.get("vendedor_id")
        if dono is not None and conexao.usuario_id == dono:
            return True
        return tem_perfil(conexao.perfil, *envelope.get("audiencia", ()))

    async def fan_out(self, envelope: dict[str, Any]) -> None:
        mortas: list[Conexao] = []
        for conexao in list(self._conexoes):
            if not self.destina(conexao, envelope):
                continue
            try:
                await conexao.ws.send_json(envelope)
            except Exception:  # noqa: BLE001 - socket caiu no meio do envio
                mortas.append(conexao)
        for conexao in mortas:
            self.remover(conexao)

    async def desconectar_usuario(
        self, usuario_id: int, code: int = CODIGO_SESSAO_INVALIDADA
    ) -> None:
        """Derruba os sockets de um usuário — a sessão dele deixou de valer."""
        for conexao in [c for c in self._conexoes if c.usuario_id == usuario_id]:
            try:
                await conexao.ws.close(code=code)
            except Exception:  # noqa: BLE001 - já pode ter caído
                logger.debug("Socket do usuário %s já estava fechado.", usuario_id)
            self.remover(conexao)


manager = GerenciadorConexoes()
