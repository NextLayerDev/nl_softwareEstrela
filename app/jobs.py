"""Jobs internos do sistema (APScheduler).

NÃO inicia o scheduler em import. O integrador chama `iniciar_scheduler()` no
startup da aplicação (ex.: evento de startup do FastAPI em app/main.py).

ATENÇÃO: o `lifespan` roda uma vez por worker do Gunicorn (3 em produção), então cada
job dispara 3 vezes. Todo job aqui pega um advisory lock e desiste se outro worker já
estiver com ele (ver app/core/locks.py).
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.database import SessionLocal
from app.core.locks import LOCK_ATRASADOS, LOCK_CI, tenta_lock
from app.services.ci_service import ci_service
from app.services.financeiro_service import financeiro_service

logger = logging.getLogger("estrela.jobs")


def job_marcar_atrasados() -> int:
    """Abre uma Session própria, marca atrasados e comita. Retorna nº atualizado."""
    db = SessionLocal()
    try:
        if not tenta_lock(db, LOCK_ATRASADOS):
            return 0  # outro worker está fazendo
        n = financeiro_service.marcar_atrasados(db)
        db.commit()
        if n:
            logger.info("marcar_atrasados: %d conta(s) marcada(s) como ATRASADO.", n)
        return n
    except Exception:
        db.rollback()
        logger.exception("Falha ao marcar contas atrasadas.")
        raise
    finally:
        db.close()


def job_atualizar_ci() -> bool:
    """Atualiza o cache do status do CI, se o TTL venceu e o breaker permitir.

    Roda a cada 30s de propósito: quem decide se vale bater na rede é o TTL/breaker do
    ci_service, não o intervalo do scheduler. Assim o "Atualizar agora" da tela só
    precisa zerar o TTL e esperar no máximo meio minuto, sem que nenhum request espere
    a internet.
    """
    db = SessionLocal()
    try:
        if not ci_service.precisa_atualizar(db):
            return False
        if not tenta_lock(db, LOCK_CI):
            return False
        ci_service.atualizar(db)
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.warning("Falha ao atualizar o status do CI.", exc_info=True)
        return False
    finally:
        db.close()


def iniciar_scheduler() -> BackgroundScheduler:
    """Cria, agenda os jobs e inicia o scheduler. Retorna a instância."""
    scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
    scheduler.add_job(
        job_marcar_atrasados,
        trigger="cron",
        hour=0,
        minute=5,
        id="marcar_atrasados",
        replace_existing=True,
    )
    scheduler.add_job(
        job_atualizar_ci,
        trigger="interval",
        seconds=30,
        id="atualizar_ci",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Scheduler iniciado: marcar_atrasados (00:05) e atualizar_ci (30s).")
    return scheduler
