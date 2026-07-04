from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.models.cliente import Cliente
from app.repositories.cliente_repo import cliente_repo
from app.schemas.cliente import ClienteCreate, ClienteUpdate

# Campos de texto gravados em CAIXA ALTA (pedido do cliente + padronização do endereço fiscal).
_CAMPOS_MAIUSCULOS = {
    "nome",
    "endereco",
    "observacao",
    "vendedor",
    "condicao_pagto_padrao",
    "fisc_logradouro",
    "fisc_bairro",
    "fisc_cidade",
    "fisc_complemento",
}


def _norm(campo: str, valor: object) -> object:
    """Normaliza por campo: e-mail em minúsculas, UF com 2 letras, demais textos em maiúsculas."""
    if not isinstance(valor, str):
        return valor
    if campo == "email":
        return valor.strip().lower() or None
    if campo == "fisc_uf":
        return valor.strip().upper()[:2] or None
    if campo in _CAMPOS_MAIUSCULOS:
        return valor.upper()
    return valor


class ClienteService:
    def listar(self, db: Session, termo: str | None = None) -> list[Cliente]:
        if termo:
            return cliente_repo.busca_rapida(db, termo)
        return cliente_repo.listar(db)

    def obter(self, db: Session, cliente_id: int) -> Cliente:
        cliente = cliente_repo.get(db, cliente_id)
        if cliente is None:
            raise NaoEncontradoError("Cliente não encontrado.")
        return cliente

    def criar(self, db: Session, dados: ClienteCreate) -> Cliente:
        # Os campos do schema batem 1:1 com as colunas do model; normaliza cada um por nome.
        dados_norm = {campo: _norm(campo, valor) for campo, valor in dados.model_dump().items()}
        return cliente_repo.add(db, Cliente(**dados_norm))

    def atualizar(self, db: Session, cliente_id: int, dados: ClienteUpdate) -> Cliente:
        cliente = self.obter(db, cliente_id)
        for campo, valor in dados.model_dump(exclude_unset=True).items():
            setattr(cliente, campo, _norm(campo, valor))
        db.flush()
        return cliente

    def inativar(self, db: Session, cliente_id: int) -> Cliente:
        cliente = self.obter(db, cliente_id)
        cliente.ativo = False
        db.flush()
        return cliente

    def excluir(self, db: Session, cliente_id: int) -> None:
        """Exclui de vez — só quando o cliente não tem pedidos (senão, protege o histórico)."""
        cliente = self.obter(db, cliente_id)
        if cliente_repo.contar_pedidos(db, cliente_id) > 0:
            raise RegraNegocioError(
                "Cliente possui pedidos no histórico; só é possível inativá-lo."
            )
        db.delete(cliente)
        db.flush()


cliente_service = ClienteService()
