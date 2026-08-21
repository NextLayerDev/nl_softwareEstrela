"""Valida entrada e chama o service do catálogo."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.catalogo import CatalogoDoc
from app.services.catalogo_service import catalogo_service


class CatalogoController:
    def montar(self, db: Session, categoria_id: int | None = None) -> CatalogoDoc:
        return catalogo_service.montar(db, categoria_id)


catalogo_controller = CatalogoController()
