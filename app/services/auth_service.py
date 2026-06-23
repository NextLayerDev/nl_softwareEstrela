from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import NaoAutenticadoError
from app.core.security import verificar_senha
from app.models.usuario import Usuario
from app.repositories.usuario_repo import usuario_repo


class AuthService:
    def autenticar(self, db: Session, email: str, senha: str) -> Usuario:
        usuario = usuario_repo.get_by_email(db, email)
        if usuario is None or not usuario.ativo:
            raise NaoAutenticadoError("E-mail ou senha inválidos.")
        if not verificar_senha(senha, usuario.senha_hash):
            raise NaoAutenticadoError("E-mail ou senha inválidos.")
        return usuario


auth_service = AuthService()
