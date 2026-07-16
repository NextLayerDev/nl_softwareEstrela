from __future__ import annotations

from sqlalchemy.orm import Session

from app.core import eventos
from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.core.security import hash_senha, senha_fraca
from app.models.usuario import Usuario
from app.repositories.usuario_repo import usuario_repo
from app.schemas.usuario import UsuarioCreate, UsuarioUpdate


def _dados_usuario(usuario: Usuario) -> dict:
    """Payload dos eventos de usuário. Jamais inclui senha_hash."""
    return {
        "usuario_id": usuario.id,
        "nome": usuario.nome,
        "perfil": usuario.perfil,
        "ativo": usuario.ativo,
    }


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
        usuario_repo.add(db, usuario)
        eventos.emitir(db, "usuario.criado", _dados_usuario(usuario), audiencia=eventos.ADMIN_AUD)
        return usuario

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
        eventos.emitir(
            db, "usuario.atualizado", _dados_usuario(usuario), audiencia=eventos.ADMIN_AUD
        )
        if desativou or trocou_perfil:
            self._invalidar_sessao(db, usuario)
        return usuario

    def _invalidar_sessao(self, db: Session, usuario: Usuario) -> None:
        """Derruba os terminais abertos deste usuário — o token dele acabou de morrer.

        Sem isso, a tela aberta continuaria parecendo válida até a próxima requisição HTTP.
        """
        eventos.emitir(
            db,
            "sessao.invalidada",
            {"usuario_id": usuario.id, "token_version": usuario.token_version},
            audiencia=(),
            target_usuario_id=usuario.id,
        )

    def resetar_senha(self, db: Session, usuario_id: int, nova_senha: str) -> Usuario:
        erro = senha_fraca(nova_senha)
        if erro:
            raise RegraNegocioError(erro)
        usuario = self.obter(db, usuario_id)
        usuario.senha_hash = hash_senha(nova_senha)
        # Reset de senha invalida sessões antigas (token roubado/compartilhado).
        usuario.token_version += 1
        usuario_repo.flush(db)
        self._invalidar_sessao(db, usuario)
        return usuario


usuario_service = UsuarioService()
