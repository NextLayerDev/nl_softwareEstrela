from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.cliente import Cliente
from app.models.pedido import Pedido


class ClienteRepository:
    def get(self, db: Session, cliente_id: int) -> Cliente | None:
        return db.get(Cliente, cliente_id)

    def contar_pedidos(self, db: Session, cliente_id: int) -> int:
        return db.scalar(select(func.count(Pedido.id)).where(Pedido.cliente_id == cliente_id)) or 0

    def listar(
        self, db: Session, incluir_inativos: bool = False, limit: int = 100
    ) -> list[Cliente]:
        stmt = select(Cliente).order_by(Cliente.nome).limit(limit)
        if not incluir_inativos:
            stmt = stmt.where(Cliente.ativo.is_(True))
        return list(db.scalars(stmt))

    def busca_rapida(self, db: Session, termo: str, limit: int = 30) -> list[Cliente]:
        termo = termo.strip()
        stmt = (
            select(Cliente)
            .where(
                Cliente.ativo.is_(True),
                or_(
                    Cliente.nome.ilike(f"%{termo}%"),
                    Cliente.cnpj_cpf.ilike(f"%{termo}%"),
                    Cliente.telefone.ilike(f"%{termo}%"),
                    Cliente.telefone2.ilike(f"%{termo}%"),
                ),
            )
            .order_by(Cliente.nome)
            .limit(limit)
        )
        return list(db.scalars(stmt))

    def add(self, db: Session, cliente: Cliente) -> Cliente:
        db.add(cliente)
        db.flush()
        return cliente


cliente_repo = ClienteRepository()
