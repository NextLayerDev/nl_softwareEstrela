from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import NaoEncontradoError
from app.models.cliente import Cliente
from app.repositories.cliente_repo import cliente_repo
from app.schemas.cliente import ClienteCreate, ClienteUpdate


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
            nome=dados.nome,
            cnpj_cpf=dados.cnpj_cpf,
            telefone=dados.telefone,
            endereco=dados.endereco,
            condicao_pagto_padrao=dados.condicao_pagto_padrao,
            limite_credito=dados.limite_credito,
            ativo=dados.ativo,
        )
        return cliente_repo.add(db, cliente)

    def atualizar(self, db: Session, cliente_id: int, dados: ClienteUpdate) -> Cliente:
        cliente = self.obter(db, cliente_id)
        for campo, valor in dados.model_dump(exclude_unset=True).items():
            setattr(cliente, campo, valor)
        db.flush()
        return cliente

    def inativar(self, db: Session, cliente_id: int) -> Cliente:
        cliente = self.obter(db, cliente_id)
        cliente.ativo = False
        db.flush()
        return cliente


cliente_service = ClienteService()
