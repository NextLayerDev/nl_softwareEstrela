"""Limitador de tentativas em memória — proteção contra força-bruta no login.

Sistema é servidor único e offline, então um contador em memória basta (sem Redis). Com
vários workers do Gunicorn o estado é por-worker, o que ainda reduz bastante a taxa efetiva
de tentativas. A chave combina IP + e-mail, então bloquear uma conta não afeta as outras.
"""

from __future__ import annotations

import threading
import time


class LimitadorTentativas:
    def __init__(self, max_tentativas: int = 5, janela_seg: int = 900) -> None:
        self.max_tentativas = max_tentativas
        self.janela_seg = janela_seg
        self._falhas: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recentes(self, chave: str, agora: float) -> list[float]:
        limite = agora - self.janela_seg
        return [t for t in self._falhas.get(chave, []) if t > limite]

    def bloqueado(self, chave: str) -> bool:
        agora = time.monotonic()
        with self._lock:
            recentes = self._recentes(chave, agora)
            self._falhas[chave] = recentes
            return len(recentes) >= self.max_tentativas

    def registrar_falha(self, chave: str) -> None:
        agora = time.monotonic()
        with self._lock:
            recentes = self._recentes(chave, agora)
            recentes.append(agora)
            self._falhas[chave] = recentes

    def limpar(self, chave: str) -> None:
        with self._lock:
            self._falhas.pop(chave, None)


# Instância compartilhada para o login: 5 tentativas por 15 minutos.
limitador_login = LimitadorTentativas(max_tentativas=5, janela_seg=900)
