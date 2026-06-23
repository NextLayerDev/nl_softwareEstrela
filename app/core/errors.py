from __future__ import annotations


class DominioError(Exception):
    """Base de todas as exceções de domínio. status_code define a resposta HTTP."""

    status_code: int = 400

    def __init__(self, mensagem: str) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem


class RegraNegocioError(DominioError):
    """Violação de regra de negócio (ex.: estoque insuficiente)."""

    status_code = 422


class NaoEncontradoError(DominioError):
    """Entidade não encontrada."""

    status_code = 404


class PermissaoNegadaError(DominioError):
    """Usuário sem permissão para a ação (RBAC)."""

    status_code = 403


class NaoAutenticadoError(DominioError):
    """Sem credenciais válidas."""

    status_code = 401
