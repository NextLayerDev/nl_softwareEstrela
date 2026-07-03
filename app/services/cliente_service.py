from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.models.cliente import Cliente
from app.repositories.cliente_repo import cliente_repo
from app.schemas.cliente import ClienteCreate, ClienteUpdate

# Campos de texto que o cliente pediu em CAIXA ALTA.
_CAMPOS_MAIUSCULOS = {"nome", "endereco", "observacao", "vendedor", "condicao_pagto_padrao"}


def _maiuscula(valor: str | None) -> str | None:
    return valor.upper() if isinstance(valor, str) else valor


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
        cliente = Cliente(
            nome=_maiuscula(dados.nome),
            cnpj_cpf=dados.cnpj_cpf,
            telefone=dados.telefone,
            telefone2=dados.telefone2,
            endereco=_maiuscula(dados.endereco),
            condicao_pagto_padrao=_maiuscula(dados.condicao_pagto_padrao),
            vendedor=_maiuscula(dados.vendedor),
            categoria=dados.categoria,
            observacao=_maiuscula(dados.observacao),
            ativo=dados.ativo,
        )
        return cliente_repo.add(db, cliente)

    def atualizar(self, db: Session, cliente_id: int, dados: ClienteUpdate) -> Cliente:
        cliente = self.obter(db, cliente_id)
        for campo, valor in dados.model_dump(exclude_unset=True).items():
            if campo in _CAMPOS_MAIUSCULOS:
                valor = _maiuscula(valor)
            setattr(cliente, campo, valor)
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
