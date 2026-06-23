from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.core.security import hash_senha
from app.models.usuario import Usuario
from app.repositories.usuario_repo import usuario_repo
from app.schemas.usuario import UsuarioCreate, UsuarioUpdate


class UsuarioService:
    def listar(self, db: Session) -> list[Usuario]:
        return usuario_repo.listar(db, incluir_inativos=True)

    def obter(self, db: Session, usuario_id: int) -> Usuario:
        usuario = usuario_repo.get(db, usuario_id)
        if usuario is None:
            raise NaoEncontradoError("Usuário não encontrado.")
        return usuario

    def criar(self, db: Session, dados: UsuarioCreate) -> Usuario:
        email = dados.email.lower().strip()
        if usuario_repo.get_by_email(db, email) is not None:
            raise RegraNegocioError(f"Já existe um usuário com o e-mail {email}.")
        usuario = Usuario(
            nome=dados.nome,
            email=email,
            senha_hash=hash_senha(dados.senha),
            perfil=str(dados.perfil),
            ativo=dados.ativo,
        )
        return usuario_repo.add(db, usuario)

    def atualizar(self, db: Session, usuario_id: int, dados: UsuarioUpdate) -> Usuario:
        usuario = self.obter(db, usuario_id)
        payload = dados.model_dump(exclude_unset=True)
        novo_email = payload.get("email")
        if novo_email:
            novo_email = novo_email.lower().strip()
            existente = usuario_repo.get_by_email(db, novo_email)
            if existente is not None and existente.id != usuario_id:
                raise RegraNegocioError(f"Já existe um usuário com o e-mail {novo_email}.")
            payload["email"] = novo_email
        if "perfil" in payload and payload["perfil"] is not None:
            payload["perfil"] = str(payload["perfil"])
        for campo, valor in payload.items():
            setattr(usuario, campo, valor)
        usuario_repo.flush(db)
        return usuario

    def resetar_senha(self, db: Session, usuario_id: int, nova_senha: str) -> Usuario:
        if len(nova_senha) < 6:
            raise RegraNegocioError("A senha deve ter ao menos 6 caracteres.")
        usuario = self.obter(db, usuario_id)
        usuario.senha_hash = hash_senha(nova_senha)
        usuario_repo.flush(db)
        return usuario


usuario_service = UsuarioService()
