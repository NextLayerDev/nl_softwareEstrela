#!/usr/bin/env python
"""Aplica as migrations do Alembic SEM travar o container num rollback.

Substitui o `alembic upgrade head` cru do entrypoint. O motivo é concreto: sob rollback,
a imagem ANTIGA sobe contra um banco que a imagem NOVA já migrou. O `upgrade head` cru
manda o Alembic resolver uma revision que não existe no histórico daquele código, morre
com "Can't locate revision identified by ...", e o `restart: always` do compose transforma
isso num CRASHLOOP — o sistema fica fora do ar exatamente na hora em que o rollback
deveria estar salvando o dia.

Três estados, três respostas:

  vazio     (sem alembic_version)            -> upgrade head
  atrás     (head aplicada existe aqui)      -> upgrade head
  À FRENTE  (head aplicada NÃO existe aqui)  -> sobe SEM migrar, loga alto e segue

O terceiro caso é a razão de existir deste arquivo e ele NÃO pode abortar. A premissa é
expand/contract: toda migration precisa ser tolerada pela versão anterior do código
(coluna nova entra nullable/com default, coluna velha só é removida uma release depois).
Quem quebrar essa premissa quebra o rollback — não há como este script consertar isso.

Downgrade NUNCA acontece aqui. Reverter schema apaga dados em silêncio; a decisão do
projeto é reverter só a imagem.

Sai com 0 quando o banco está utilizável e 1 quando não está — o agente de deploy usa
este código de saída como o pré-flight que tira a migration do caminho crítico do app.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from alembic import command

logger = logging.getLogger("estrela.migrar_seguro")

RAIZ = Path(__file__).resolve().parent.parent

# Mutex entre dois migradores simultâneos: o pré-flight do agente roda contra o MESMO
# banco do app que está no ar, e um `up -d` no meio do caminho subiria um entrypoint que
# também migra. Advisory lock é liberado sozinho se a conexão morrer.
LOCK_MIGRACAO = 815_010

VAZIO = "vazio"
EM_DIA = "em_dia"
ATRAS = "atras"
A_FRENTE = "a_frente"


def classificar(
    aplicadas: Sequence[str],
    heads: Sequence[str],
    conhecidas: Iterable[str],
) -> str:
    """Onde o BANCO está em relação a ESTE código. Função pura — é o coração testável.

    :param aplicadas: revisions presentes em `alembic_version` (vazio = banco novo).
    :param heads: head(s) do diretório de migrations deste código.
    :param conhecidas: todas as revisions que este código conhece.
    """
    conhecidas = set(conhecidas)
    if not aplicadas:
        return VAZIO
    # Uma revision desconhecida só pode ter vindo de uma versão mais nova do código.
    if any(rev not in conhecidas for rev in aplicadas):
        return A_FRENTE
    if set(aplicadas) == set(heads):
        return EM_DIA
    return ATRAS


def _dsn() -> str:
    """DATABASE_URL do ambiente; em último caso, a Settings da app.

    O env vem primeiro para este script continuar utilizável quando a app não importa
    (é justamente o cenário em que ele é mais necessário).
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    from app.core.config import settings

    return settings.DATABASE_URL


def _config() -> Config:
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "alembic"))
    return cfg


def _estado_atual(engine, script: ScriptDirectory) -> tuple[str, tuple[str, ...]]:
    with engine.connect() as conn:
        aplicadas = tuple(MigrationContext.configure(conn).get_current_heads())
    conhecidas = {r.revision for r in script.walk_revisions()}
    return classificar(aplicadas, script.get_heads(), conhecidas), aplicadas


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[migrar_seguro] %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    cfg = _config()
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()

    engine = create_engine(_dsn(), poolclass=NullPool)
    try:
        estado, aplicadas = _estado_atual(engine, script)
    except Exception:
        logger.exception("Não foi possível ler o estado do schema.")
        return 1

    atual = ", ".join(aplicadas) or "(nenhuma)"
    esperada = ", ".join(heads) or "(nenhuma)"
    logger.info("Banco em [%s]; este código espera [%s].", atual, esperada)

    # Linha estável para o agente de deploy raspar do stdout do pré-flight.
    print(f"ESTADO={estado}", flush=True)

    if estado == A_FRENTE:
        logger.warning(
            "BANCO À FRENTE DO CÓDIGO. Subindo SEM migrar (expand/contract): a revision "
            "[%s] não existe neste código, o que significa que uma versão mais nova já "
            "migrou este banco. Isto é o esperado durante um rollback. NENHUM downgrade "
            "será feito — reverter schema apagaria dados. Se alguma tela quebrar, a "
            "saída é avançar para a versão nova de novo, não descer o banco.",
            atual,
        )
        return 0

    if estado == EM_DIA:
        logger.info("Schema em dia; nada a fazer.")
        return 0

    logger.info("Aplicando migrations (%s -> %s)...", atual, esperada)
    try:
        with engine.connect() as trava:
            # Sessão à parte só para segurar o mutex: o command.upgrade abre a conexão
            # dele pelo env.py e não daria para compartilhar esta.
            trava.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_MIGRACAO})
            try:
                # Re-checa sob o lock: outro migrador pode ter terminado enquanto
                # esperávamos, e aí não há mais nada a fazer.
                estado, aplicadas = _estado_atual(engine, script)
                if estado == EM_DIA:
                    logger.info("Outro processo já migrou enquanto aguardávamos o lock.")
                    return 0
                if estado == A_FRENTE:
                    logger.warning("Banco passou a estar à frente enquanto aguardávamos.")
                    return 0
                command.upgrade(cfg, "head")
            finally:
                trava.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_MIGRACAO})
    except Exception:
        logger.exception("FALHA ao aplicar as migrations.")
        return 1

    logger.info("Migrations aplicadas com sucesso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
