from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.core.security import hash_senha, senha_fraca
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
            # Mensagem genérica: não confirma se o e-mail já existe (evita enumeração).
            raise RegraNegocioError("Não foi possível cadastrar com esse e-mail.")
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
                raise RegraNegocioError("Não foi possível salvar com esse e-mail.")
            payload["email"] = novo_email
        if "perfil" in payload and payload["perfil"] is not None:
            payload["perfil"] = str(payload["perfil"])
        # Desativar ou trocar o perfil invalida sessões ativas (tokens já emitidos).
        desativou = payload.get("ativo") is False and usuario.ativo
        trocou_perfil = "perfil" in payload and payload["perfil"] != usuario.perfil
        for campo, valor in payload.items():
            setattr(usuario, campo, valor)
        if desativou or trocou_perfil:
            usuario.token_version += 1
        usuario_repo.flush(db)
        return usuario

    def resetar_senha(self, db: Session, usuario_id: int, nova_senha: str) -> Usuario:
        erro = senha_fraca(nova_senha)
        if erro:
            raise RegraNegocioError(erro)
        usuario = self.obter(db, usuario_id)
        usuario.senha_hash = hash_senha(nova_senha)
        # Reset de senha invalida sessões antigas (token roubado/compartilhado).
        usuario.token_version += 1
        usuario_repo.flush(db)
        return usuario


usuario_service = UsuarioService()
