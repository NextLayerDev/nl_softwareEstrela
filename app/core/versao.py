"""Identidade do build: versão, commit e data, resolvidos uma vez por processo.

A imagem de produção recebe estes valores como build-arg (ver Dockerfile) porque lá
dentro não existe .git — o `.dockerignore` o exclui de propósito. Em dev não há
build-arg, então caímos no git local só para o número não ficar mentindo na tela.

Nunca levanta exceção: a aba /deploy é justamente a tela que precisa continuar de pé
quando o resto está quebrado.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import settings

DESCONHECIDA = "desconhecida"


@dataclass(frozen=True)
class VersaoBuild:
    versao: str
    git_sha: str
    build_date: str
    tag: str

    @property
    def git_sha_curto(self) -> str:
        return self.git_sha[:7] if self.git_sha and self.git_sha != DESCONHECIDA else DESCONHECIDA

    @property
    def e_dev(self) -> bool:
        """True quando o build não veio do pipeline (rodando local ou build manual)."""
        return self.versao == "dev"


def _git(*args: str) -> str:
    """Consulta o git local. Só faz sentido em dev; em produção não há .git na imagem."""
    try:
        saida = subprocess.run(  # noqa: S603 - argv fixo, sem shell, sem entrada do usuário
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return saida.stdout.strip() if saida.returncode == 0 else ""


@lru_cache
def versao_build() -> VersaoBuild:
    """Cascata: build-arg (produção) -> git (só dev) -> desconhecida."""
    versao = settings.APP_VERSION.strip()
    sha = settings.GIT_SHA.strip()
    data = settings.BUILD_DATE.strip()
    tag = settings.APP_TAG.strip()

    # Sob pytest não chamamos o git: deixaria o teste dependente da árvore de trabalho.
    if settings.is_dev and "pytest" not in sys.modules:
        versao = versao or "dev"
        sha = sha or _git("rev-parse", "HEAD") or DESCONHECIDA
        data = data or _git("log", "-1", "--format=%cI") or DESCONHECIDA

    return VersaoBuild(
        versao=versao or DESCONHECIDA,
        git_sha=sha or DESCONHECIDA,
        build_date=data or DESCONHECIDA,
        tag=tag or DESCONHECIDA,
    )
