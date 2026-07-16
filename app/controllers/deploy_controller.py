from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.repositories.deploy_repo import deploy_repo
from app.services.ci_service import ci_service
from app.services.saude_service import saude_service


class DeployController:
    def saude(self, db: Session) -> dict[str, Any]:
        s = saude_service.coletar(db)
        return {"saude": s, "versao": s.versao}

    def status(self, db: Session) -> dict[str, Any]:
        """Bloco que a tela repica de perto: só o deploy em andamento."""
        return {"execucao": deploy_repo.em_voo(db)}

    def historico(self, db: Session, limit: int = 20) -> dict[str, Any]:
        return {"deploys": deploy_repo.listar(db, limit=limit)}

    def ci(self, db: Session) -> dict[str, Any]:
        return {"ci": ci_service.obter(db)}

    def pagina(self, db: Session) -> dict[str, Any]:
        # O card de CI NÃO entra aqui: ele é carregado à parte (hx-trigger="load") para
        # a página nunca depender do que o job já conseguiu buscar.
        return {**self.saude(db), **self.status(db), **self.historico(db)}


deploy_controller = DeployController()
