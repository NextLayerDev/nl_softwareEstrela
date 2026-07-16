"""Cliente somente-leitura da API do GitHub, para o card de CI da aba /deploy.

O sistema é offline-first (CLAUDE.md §16). Este módulo é a única coisa que disca para
fora, e por isso:

- NUNCA é chamado de dentro de um request. Quem chama é o job do APScheduler
  (app/jobs.py); o request lê apenas o cache no Postgres. Um request que esperasse a
  rede travaria a tela no pior caso (rede lenta mas não caída), que é justamente o mais
  provável no cliente.
- Tem timeout TOTAL, não só de connect/read: o `read` do httpx rearma a cada chunk, então
  um servidor lento entregando 1 byte por vez seguraria a conexão indefinidamente.
  `httpx.Timeout(..., pool=...)` não cobre isso — o teto duro é o `_TIMEOUT_TOTAL`.
- A URL é montada de config validada (nunca de input do usuário) — ver o field_validator
  de GITHUB_OWNER/GITHUB_REPO em app/core/config.py.
- O token é opcional: o repositório é público. Sem token o limite é 60 req/h por IP e o
  job consulta 1x a cada 5 min. Ele nunca vai para template, log ou mensagem de erro.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger("estrela.github")

_BASE = "https://api.github.com"
_TIMEOUT_TOTAL = 10.0
_MAX_RUNS = 5


@dataclass
class ResultadoCi:
    ok: bool
    erro: str | None = None
    runs: list[dict[str, Any]] = field(default_factory=list)
    release_tag: str | None = None
    release_url: str | None = None
    release_em: str | None = None


def _cabecalhos() -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.GITHUB_TOKEN_LEITURA:
        h["Authorization"] = f"Bearer {settings.GITHUB_TOKEN_LEITURA}"
    return h


def _erro_amigavel(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "O GitHub demorou demais para responder."
    if isinstance(exc, httpx.TransportError):
        return "Sem conexão com o GitHub."
    return "Não foi possível consultar o GitHub."


def _traduzir_status(resp: httpx.Response) -> str | None:
    """Mensagem de erro por status. Nunca inclui corpo da resposta (pode ecoar o token)."""
    if resp.status_code == 401:
        return "Token do GitHub inválido ou expirado."
    if resp.status_code == 403:
        # 403 no GitHub é quase sempre rate limit, não permissão.
        if resp.headers.get("X-RateLimit-Remaining") == "0":
            return "Limite de consultas ao GitHub atingido. Tentaremos mais tarde."
        return "Acesso negado pelo GitHub."
    if resp.status_code == 404:
        return "Repositório não encontrado no GitHub."
    if resp.status_code >= 500:
        return "O GitHub está com problemas."
    return None


def consultar() -> ResultadoCi:
    """Busca os últimos runs da main e a release mais recente. Nunca levanta exceção."""
    if not settings.github_habilitado:
        return ResultadoCi(ok=False, erro="Consulta ao GitHub não configurada.")

    repo = f"{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}"
    try:
        with httpx.Client(
            timeout=_TIMEOUT_TOTAL,
            headers=_cabecalhos(),
            follow_redirects=False,
        ) as c:
            # ORDEM IMPORTA e é semântica, não estética: os runs vêm primeiro porque um
            # 404 aqui significa "repo não existe", enquanto um 404 em /releases/latest
            # significa apenas "ainda não há release". Paralelizar ou inverter faria a
            # aba acusar "repositório não encontrado" num repo saudável e sem releases.
            r = c.get(
                f"{_BASE}/repos/{repo}/actions/runs",
                params={"branch": "main", "per_page": _MAX_RUNS, "exclude_pull_requests": "true"},
            )
            if (msg := _traduzir_status(r)) is not None:
                return ResultadoCi(ok=False, erro=msg)
            r.raise_for_status()
            runs = [
                {
                    "workflow": x.get("name"),
                    "status": x.get("status"),
                    "conclusion": x.get("conclusion"),
                    "url": x.get("html_url"),
                    "sha": (x.get("head_sha") or "")[:7],
                    "titulo": x.get("display_title"),
                    "criado_em": x.get("created_at"),
                }
                for x in (r.json().get("workflow_runs") or [])[:_MAX_RUNS]
            ]

            rel = c.get(f"{_BASE}/repos/{repo}/releases/latest")
            if rel.status_code == 404:
                # Repo sem release ainda: estado normal, não é erro.
                return ResultadoCi(ok=True, runs=runs)
            if (msg := _traduzir_status(rel)) is not None:
                return ResultadoCi(ok=True, runs=runs, erro=msg)
            rel.raise_for_status()
            j = rel.json()
            return ResultadoCi(
                ok=True,
                runs=runs,
                release_tag=j.get("tag_name"),
                release_url=j.get("html_url"),
                release_em=j.get("published_at"),
            )
    except Exception as exc:  # noqa: BLE001 - card do CI jamais quebra a aba
        logger.warning("Falha ao consultar o GitHub: %s", type(exc).__name__)
        return ResultadoCi(ok=False, erro=_erro_amigavel(exc))
