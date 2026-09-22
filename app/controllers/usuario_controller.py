from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import RegraNegocioError
from app.models.enums import Perfil
from app.models.usuario import Usuario
from app.schemas.usuario import PreferenciasUpdate, UsuarioCreate, UsuarioUpdate
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

    def salvar_preferencias(self, db: Session, usuario: Usuario, campo: str, valor: str) -> Usuario:
        """Uma chave do "Meu perfil" (ou o alternador Lista | Planilha) por vez.

        Sempre sobre o PRÓPRIO usuário logado — não há id na rota para trocar.
        """
        if campo not in PreferenciasUpdate.model_fields:
            raise RegraNegocioError("Preferência desconhecida.")
        dados = PreferenciasUpdate(**{campo: valor in ("on", "true", "1")})
        return usuario_service.salvar_preferencias(db, usuario.id, dados)


usuario_controller = UsuarioController()
