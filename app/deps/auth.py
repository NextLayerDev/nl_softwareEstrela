from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NaoAutenticadoError, PermissaoNegadaError
from app.core.security import decodificar_token
from app.deps.db import get_db
from app.models.enums import tem_perfil
from app.models.usuario import Usuario

COOKIE_NOME = "estrela_token"


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Usuario:
    """Lê o JWT do cookie httpOnly, valida e devolve o usuário ativo."""
    token = request.cookies.get(COOKIE_NOME)
    if not token:
        raise NaoAutenticadoError("Sessão não encontrada. Faça login.")
    payload = decodificar_token(token)
    if not payload or "sub" not in payload:
        raise NaoAutenticadoError("Sessão inválida ou expirada.")
    usuario = db.scalar(select(Usuario).where(Usuario.id == int(payload["sub"])))
    if usuario is None or not usuario.ativo:
        raise NaoAutenticadoError("Usuário inativo ou inexistente.")
    # Revogação de sessão: se a senha foi resetada ou o usuário desativado/reativado,
    # o token_version muda e os tokens antigos deixam de valer.
    if int(payload.get("tv", 0)) != usuario.token_version:
        raise NaoAutenticadoError("Sessão expirada. Faça login novamente.")
    return usuario


def require_role(*roles: str) -> Callable[..., Usuario]:
    """Dependency factory de RBAC: garante que o usuário tem um dos perfis.

    O perfil `dev` (manutenção) passa em qualquer conjunto — é superusuário. A exceção
    é justamente `require_role("dev")`, usado por /deploy: como "admin" não está no
    conjunto, o admin da empresa leva 403 mesmo sabendo a URL.
    """

    def _checker(usuario: Usuario = Depends(get_current_user)) -> Usuario:
        if not tem_perfil(usuario.perfil, *roles):
            raise PermissaoNegadaError("Você não tem permissão para acessar este recurso.")
        return usuario

    return _checker


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> Usuario | None:
    """Versão que não lança erro — útil em páginas públicas (login) e no layout."""
    try:
        return get_current_user(request, db)
    except NaoAutenticadoError:
        return None
