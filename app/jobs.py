"""Jobs internos do sistema (APScheduler).

NÃO inicia o scheduler em import. O integrador chama `iniciar_scheduler()` no
startup da aplicação (ex.: evento de startup do FastAPI em app/main.py).
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.database import SessionLocal
from app.services.financeiro_service import financeiro_service

logger = logging.getLogger("estrela.jobs")


def job_marcar_atrasados() -> int:
    """Abre uma Session própria, marca atrasados e comita. Retorna nº atualizado."""
    db = SessionLocal()
    try:
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


def iniciar_scheduler() -> BackgroundScheduler:
    """Cria, agenda o job diário e inicia o scheduler. Retorna a instância."""
    scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
    scheduler.add_job(
        job_marcar_atrasados,
        trigger="cron",
        hour=0,
        minute=5,
        id="marcar_atrasados",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler iniciado: marcar_atrasados agendado para 00:05 diariamente.")
    return scheduler
