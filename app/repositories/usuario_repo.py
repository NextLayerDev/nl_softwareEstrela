from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.usuario import Usuario


class UsuarioRepository:
    def get(self, db: Session, usuario_id: int) -> Usuario | None:
        return db.get(Usuario, usuario_id)

    def get_by_email(self, db: Session, email: str) -> Usuario | None:
        return db.scalar(select(Usuario).where(Usuario.email == email.lower().strip()))

    def listar(self, db: Session, incluir_inativos: bool = False) -> list[Usuario]:
        stmt = select(Usuario).order_by(Usuario.nome)
        if not incluir_inativos:
            stmt = stmt.where(Usuario.ativo.is_(True))
        return list(db.scalars(stmt))

    def add(self, db: Session, usuario: Usuario) -> Usuario:
        db.add(usuario)
        db.flush()
        return usuario


usuario_repo = UsuarioRepository()
