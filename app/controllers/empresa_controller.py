from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.empresa_config import EmpresaConfig
from app.schemas.empresa import EmpresaConfigUpdate
from app.services.empresa_service import empresa_service


class EmpresaController:
    def obter(self, db: Session) -> EmpresaConfig | None:
        return empresa_service.obter(db)

    def salvar(self, db: Session, form: dict) -> EmpresaConfig:
        dados = EmpresaConfigUpdate(
            razao_social=(form.get("razao_social") or None),
            nome_fantasia=(form.get("nome_fantasia") or None),
            cnpj=(form.get("cnpj") or None),
            inscricao_estadual=(form.get("inscricao_estadual") or None),
            telefone=(form.get("telefone") or None),
            email=(form.get("email") or None),
            endereco=(form.get("endereco") or None),
            observacao_cupom=(form.get("observacao_cupom") or None),
        )
        return empresa_service.salvar(db, dados)


empresa_controller = EmpresaController()
