from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.enums import Perfil
from app.models.usuario import Usuario
from app.schemas.usuario import UsuarioCreate, UsuarioUpdate
from app.services.usuario_service import usuario_service


class UsuarioController:
    def listar(self, db: Session) -> list[Usuario]:
        return usuario_service.listar(db)

    def obter(self, db: Session, usuario_id: int) -> Usuario:
        return usuario_service.obter(db, usuario_id)

    def criar(self, db: Session, form: dict) -> Usuario:
        dados = UsuarioCreate(
            nome=form.get("nome", ""),
            email=form.get("email", ""),
            senha=form.get("senha", ""),
            perfil=Perfil(form.get("perfil", "")),
            ativo=form.get("ativo") in ("on", "true", "1", True),
        )
        return usuario_service.criar(db, dados)

    def atualizar(self, db: Session, usuario_id: int, form: dict) -> Usuario:
        dados = UsuarioUpdate(
            nome=form.get("nome") or None,
            email=form.get("email") or None,
            perfil=Perfil(form["perfil"]) if form.get("perfil") else None,
            ativo=form.get("ativo") in ("on", "true", "1", True),
        )
        return usuario_service.atualizar(db, usuario_id, dados)

    def resetar_senha(self, db: Session, usuario_id: int, nova_senha: str) -> Usuario:
        return usuario_service.resetar_senha(db, usuario_id, nova_senha)


usuario_controller = UsuarioController()
