from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.empresa_config import EmpresaConfig
from app.repositories.empresa_repo import empresa_repo
from app.schemas.empresa import EmpresaConfigUpdate

# Campos de texto gravados em CAIXA ALTA (padronização, como no cadastro de cliente).
_CAMPOS_MAIUSCULOS = {"razao_social", "nome_fantasia", "endereco"}


def _norm(campo: str, valor: object) -> object:
    if not isinstance(valor, str):
        return valor
    valor = valor.strip()
    if not valor:
        return None
    if campo == "email":
        return valor.lower()
    if campo in _CAMPOS_MAIUSCULOS:
        return valor.upper()
    return valor


class EmpresaService:
    def obter(self, db: Session) -> EmpresaConfig | None:
        """Retorna a configuração da empresa (ou None se ainda não foi preenchida)."""
        return empresa_repo.get(db)

    def salvar(self, db: Session, dados: EmpresaConfigUpdate) -> EmpresaConfig:
        empresa = empresa_repo.get_or_create(db)
        for campo, valor in dados.model_dump().items():
            setattr(empresa, campo, _norm(campo, valor))
        db.flush()
        return empresa


empresa_service = EmpresaService()
