from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.empresa_config import EmpresaConfig

# Registro único da configuração da empresa (emitente).
_EMPRESA_ID = 1


class EmpresaConfigRepository:
    def get(self, db: Session) -> EmpresaConfig | None:
        return db.get(EmpresaConfig, _EMPRESA_ID)

    def get_or_create(self, db: Session) -> EmpresaConfig:
        empresa = db.get(EmpresaConfig, _EMPRESA_ID)
        if empresa is None:
            empresa = EmpresaConfig(id=_EMPRESA_ID)
            db.add(empresa)
            db.flush()
        return empresa


empresa_repo = EmpresaConfigRepository()
