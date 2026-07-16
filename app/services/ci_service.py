"""Estado do CI do GitHub, cacheado no Postgres.

Quem chama a rede é o job do APScheduler (app/jobs.py). O request só LÊ o cache — assim
a aba /deploy continua abrindo instantaneamente sem internet, que é o caso normal no
cliente.

O cache mora no banco, e não em memória, porque são 3 workers Gunicorn: em memória cada
worker teria o seu, o card piscaria entre 3 estados a cada F5 e as chamadas à API
triplicariam contra um rate limit que é por token/IP.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.integracoes import github
from app.models.deploy import CiStatusCache

logger = logging.getLogger("estrela.ci")

# Backoff do circuit breaker: sem internet, para de bater na porta a cada tick.
_BACKOFF = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1)]


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


class CiService:
    def obter(self, db: Session) -> CiStatusCache | None:
        """Só leitura — é isto que o request enxerga."""
        return db.get(CiStatusCache, 1)

    def _linha(self, db: Session) -> CiStatusCache:
        linha = db.get(CiStatusCache, 1)
        if linha is None:
            linha = CiStatusCache(id=1)
            db.add(linha)
            db.flush()
        return linha

    def precisa_atualizar(self, db: Session) -> bool:
        if not settings.github_habilitado:
            return False
        linha = db.get(CiStatusCache, 1)
        if linha is None or linha.consultado_em is None:
            return True
        agora = datetime.now(UTC)
        # Breaker aberto: respeita o recuo antes de tentar de novo.
        if linha.proxima_tentativa_em and agora < linha.proxima_tentativa_em:
            return False
        return agora - linha.consultado_em >= timedelta(seconds=settings.CI_CACHE_TTL_SEG)

    def atualizar(self, db: Session) -> CiStatusCache:
        """Consulta o GitHub e grava o resultado. Chamado SÓ pelo job, nunca por request."""
        r = github.consultar()
        linha = self._linha(db)
        agora = datetime.now(UTC)
        linha.consultado_em = agora

        if r.ok:
            linha.ok = True
            linha.erro = r.erro  # pode haver erro parcial (ex.: release ilegível)
            linha.runs = r.runs
            linha.release_tag = r.release_tag
            linha.release_url = r.release_url
            linha.release_em = _parse(r.release_em)
            linha.falhas_seguidas = 0
            linha.proxima_tentativa_em = None
        else:
            # Preserva os dados antigos: um card "de 20 min atrás" vale mais que um vazio.
            linha.ok = False
            linha.erro = r.erro
            linha.falhas_seguidas = (linha.falhas_seguidas or 0) + 1
            recuo = _BACKOFF[min(linha.falhas_seguidas - 1, len(_BACKOFF) - 1)]
            linha.proxima_tentativa_em = agora + recuo
            logger.info(
                "Consulta ao CI falhou (%dx). Próxima tentativa em %s.",
                linha.falhas_seguidas,
                recuo,
            )
        return linha


ci_service = CiService()
