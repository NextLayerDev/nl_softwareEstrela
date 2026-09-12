"""Cliente do webhook de sincronização de catálogo (omni_respostaMax).

O sistema é offline-first (CLAUDE.md §16), mas este servidor tem saída para a internet
(diferente do GitHub — este é o único outro lugar que disca pra fora). Mesmas regras do
`app/integracoes/github.py`:

- NUNCA é chamado de dentro de um request. Quem chama é o job do APScheduler
  (`job_flush_sync_outbox`, app/jobs.py) — um destino externo lento/fora do ar nunca pode
  travar uma tela.
- Timeout TOTAL (não só connect/read).
- A URL vem só de config validada, nunca de input do usuário.
- O segredo (HMAC) nunca vai para log, exceção ou mensagem de erro.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger("estrela.catalogo_webhook")

_TIMEOUT_TOTAL = 10.0


def _assinar(corpo: bytes) -> str:
    return hmac.new(settings.CATALOGO_WEBHOOK_SECRET.encode(), corpo, hashlib.sha256).hexdigest()


def enviar_evento(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """Envia um snapshot de produto para o catálogo externo.

    Retorna (ok, erro). Nunca levanta exceção — falha de rede é reportada, não propagada,
    para o chamador (o job) decidir o que fazer com a linha da fila.
    """
    if not settings.catalogo_sync_habilitado:
        return False, "Sincronização de catálogo não configurada."

    corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    assinatura = _assinar(corpo)
    try:
        with httpx.Client(timeout=_TIMEOUT_TOTAL, follow_redirects=False) as c:
            resp = c.post(
                settings.CATALOGO_WEBHOOK_URL,
                content=corpo,
                headers={
                    "Content-Type": "application/json",
                    "X-Estrela-Signature": assinatura,
                },
            )
            if resp.status_code >= 400:
                # Nunca inclui o corpo da resposta no erro: pode ecoar dados sensíveis.
                return False, f"Catálogo externo respondeu {resp.status_code}."
            return True, None
    except httpx.TimeoutException:
        return False, "O catálogo externo demorou demais para responder."
    except httpx.TransportError:
        return False, "Sem conexão com o catálogo externo."
    except Exception as exc:  # noqa: BLE001 - envio é best-effort, jamais quebra o job
        logger.warning("Falha ao enviar evento ao catálogo externo: %s", type(exc).__name__)
        return False, "Não foi possível enviar ao catálogo externo."
