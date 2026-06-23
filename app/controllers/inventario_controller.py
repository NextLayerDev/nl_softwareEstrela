from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.inventario import Inventario, InventarioItem
from app.repositories.estoque_repo import inventario_repo
from app.schemas.estoque import ContagemCreate, InventarioCreate
from app.services.inventario_service import inventario_service


class InventarioController:
    def listar(self, db: Session) -> list[Inventario]:
        return inventario_repo.listar(db)

    def get(self, db: Session, inventario_id: int) -> Inventario | None:
        return inventario_repo.get(db, inventario_id)

    def criar(self, db: Session, dados: InventarioCreate, usuario_id: int) -> Inventario:
        return inventario_service.abrir(
            db, usuario_id, descricao=dados.descricao, variacao_ids=dados.variacao_ids
        )

    def contar(self, db: Session, inventario_id: int, dados: ContagemCreate) -> InventarioItem:
        return inventario_service.registrar_contagem(
            db, inventario_id, dados.item_id, dados.qtd_contada
        )

    def aplicar(self, db: Session, inventario_id: int, usuario_id: int) -> Inventario:
        return inventario_service.aplicar(db, inventario_id, usuario_id)


inventario_controller = InventarioController()
