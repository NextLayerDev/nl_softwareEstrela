from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.deploy import Deploy
from app.models.usuario import Usuario
from app.repositories.deploy_repo import deploy_repo
from app.services.ci_service import ci_service
from app.services.deploy_service import Releases, deploy_service
from app.services.saude_service import saude_service


class DeployController:
    def saude(self, db: Session) -> dict[str, Any]:
        s = saude_service.coletar(db)
        return {"saude": s, "versao": s.versao}

    def status(self, db: Session) -> dict[str, Any]:
        """Bloco que a tela repica de perto: só o deploy em andamento."""
        return {"execucao": deploy_repo.em_voo(db)}

    def _releases_ctx(self, info: Releases) -> dict[str, Any]:
        # `por_tag` liga o histórico à allowlist: a linha do histórico guarda a versão,
        # mas quem sabe o sha, a head e se voltar é seguro é o agente. Sem o cruzamento,
        # o botão de reverter ofereceria versões que o agente não tem mais.
        return {"releases": info, "por_tag": {r.tag: r for r in info.itens}}

    def _painel_ctx(self, db: Session) -> dict[str, Any]:
        """Contexto do card "Atualizar o sistema" e do bloco de auto-deploy.

        A allowlist e o "em voo" são resolvidos UMA vez e repassados ao `auto_deploy`:
        os três blocos falam da mesma máquina, e refazer as consultas em cada um
        multiplicaria o custo do fragmento sem mudar uma vírgula do que aparece.
        """
        info = deploy_service.releases(db)
        st = self.status(db)
        return {
            **self._releases_ctx(info),
            **st,
            "auto": deploy_service.auto_deploy(db, info, st["execucao"] is not None),
        }

    def releases(self, db: Session) -> dict[str, Any]:
        return self._painel_ctx(db)

    def historico(self, db: Session, limit: int = 20) -> dict[str, Any]:
        return {
            "deploys": deploy_repo.listar(db, limit=limit),
            **self._releases_ctx(deploy_service.releases(db)),
            **self.status(db),
        }

    def ci(self, db: Session) -> dict[str, Any]:
        return {"ci": ci_service.obter(db)}

    def pagina(self, db: Session) -> dict[str, Any]:
        # A página inteira renderiza os DOIS fragmentos, então a allowlist e o "em voo"
        # são resolvidos UMA vez e compartilhados: chamar releases() + historico() aqui
        # dobraria as duas consultas por F5.
        #
        # O card de CI NÃO entra aqui: ele é carregado à parte (hx-trigger="load") para
        # a página nunca depender do que o job já conseguiu buscar.
        return {
            **self.saude(db),
            **self._painel_ctx(db),
            "deploys": deploy_repo.listar(db),
        }

    # ------------------------------------------------------------------ ações
    def solicitar_atualizacao(self, db: Session, tag: str, usuario: Usuario) -> Deploy:
        return deploy_service.solicitar_atualizacao(db, tag, usuario)

    def solicitar_rollback(
        self, db: Session, tag: str, usuario: Usuario, confirmacao: str = ""
    ) -> Deploy:
        return deploy_service.solicitar_rollback(db, tag, usuario, confirmacao=confirmacao)

    def cancelar(self, db: Session, deploy_id: int, usuario: Usuario) -> Deploy:
        return deploy_service.cancelar(db, deploy_id, usuario)


deploy_controller = DeployController()
