from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.cliente import Cliente
from app.models.enums import CategoriaCliente
from app.schemas.cliente import ClienteCreate, ClienteUpdate
from app.services.cliente_service import cliente_service

_CATEGORIAS_VALIDAS = {c.value for c in CategoriaCliente}


def _categoria_opt(valor: str | None) -> str | None:
    valor = (valor or "").strip()
    return valor if valor in _CATEGORIAS_VALIDAS else None


def _condicao(form: dict) -> str | None:
    """Monta a condição de pagamento a partir dos campos do form (radio + descrição).

    "À vista" -> "À VISTA"; "Outro" -> o texto digitado (o service normaliza p/ maiúsculas).
    """
    tipo = (form.get("cond_tipo") or "avista").strip().lower()
    if tipo == "outro":
        return (form.get("cond_desc") or "").strip() or None
    return "À VISTA"


class ClienteController:
    def listar(self, db: Session, termo: str | None) -> list[Cliente]:
        return cliente_service.listar(db, termo)

    def obter(self, db: Session, cliente_id: int) -> Cliente:
        return cliente_service.obter(db, cliente_id)

    def criar(self, db: Session, form: dict) -> Cliente:
        dados = ClienteCreate(
            nome=form.get("nome", ""),
            cnpj_cpf=(form.get("cnpj_cpf") or None),
            telefone=(form.get("telefone") or None),
            telefone2=(form.get("telefone2") or None),
            endereco=(form.get("endereco") or None),
            condicao_pagto_padrao=_condicao(form),
            vendedor=(form.get("vendedor") or None),
            categoria=_categoria_opt(form.get("categoria")),
            observacao=(form.get("observacao") or None),
            ativo=form.get("ativo") in ("on", "true", "1", True),
        )
        return cliente_service.criar(db, dados)

    def atualizar(self, db: Session, cliente_id: int, form: dict) -> Cliente:
        dados = ClienteUpdate(
            nome=form.get("nome") or None,
            cnpj_cpf=(form.get("cnpj_cpf") or None),
            telefone=(form.get("telefone") or None),
            telefone2=(form.get("telefone2") or None),
            endereco=(form.get("endereco") or None),
            condicao_pagto_padrao=_condicao(form),
            vendedor=(form.get("vendedor") or None),
            categoria=_categoria_opt(form.get("categoria")),
            observacao=(form.get("observacao") or None),
            ativo=form.get("ativo") in ("on", "true", "1", True),
        )
        return cliente_service.atualizar(db, cliente_id, dados)

    def inativar(self, db: Session, cliente_id: int) -> Cliente:
        return cliente_service.inativar(db, cliente_id)

    def excluir(self, db: Session, cliente_id: int) -> None:
        cliente_service.excluir(db, cliente_id)


cliente_controller = ClienteController()
