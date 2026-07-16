from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.deploy import CiStatusCache, Deploy


class DeployRepository:
    def listar(self, db: Session, limit: int = 20) -> list[Deploy]:
        # joinedload: o relationship é lazy="raise" e o histórico mostra o nome de quem
        # clicou — sem isto seria uma query por linha (ou um erro).
        stmt = (
            select(Deploy)
            .options(joinedload(Deploy.usuario))
            .order_by(Deploy.solicitado_em.desc())
            .limit(limit)
        )
        return list(db.scalars(stmt))

    def get(self, db: Session, deploy_id: int) -> Deploy | None:
        stmt = select(Deploy).options(joinedload(Deploy.usuario)).where(Deploy.id == deploy_id)
        return db.scalar(stmt)

    def em_voo(self, db: Session) -> Deploy | None:
        """O deploy em andamento, se houver. É ele que a tela acompanha."""
        stmt = (
            select(Deploy)
            .options(joinedload(Deploy.usuario))
            .where(Deploy.status.in_(("solicitado", "executando")))
            .order_by(Deploy.solicitado_em.desc())
            .limit(1)
        )
        return db.scalar(stmt)

    def ultimo_sucesso(self, db: Session) -> Deploy | None:
        stmt = (
            select(Deploy)
            .where(Deploy.status == "sucesso")
            .order_by(Deploy.concluido_em.desc())
            .limit(1)
        )
        return db.scalar(stmt)

    def cache_ci(self, db: Session) -> CiStatusCache | None:
        return db.get(CiStatusCache, 1)


deploy_repo = DeployRepository()
