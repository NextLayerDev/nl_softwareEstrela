from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.core.errors import RegraNegocioError
from app.models.cliente import Cliente
from app.schemas.cliente import ClienteCreate, ClienteUpdate
from app.services.cliente_service import cliente_service


def _dec_opt(valor: str | None) -> Decimal | None:
    if valor is None or str(valor).strip() == "":
        return None
    try:
        bruto = str(valor)
        bruto = bruto.replace(".", "").replace(",", ".") if "," in bruto else bruto
        return Decimal(bruto)
    except InvalidOperation as exc:
        raise RegraNegocioError(f"Valor numérico inválido: {valor}") from exc


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
            endereco=(form.get("endereco") or None),
            condicao_pagto_padrao=(form.get("condicao_pagto_padrao") or None),
            limite_credito=_dec_opt(form.get("limite_credito")),
            ativo=form.get("ativo") in ("on", "true", "1", True),
        )
        return cliente_service.criar(db, dados)

    def atualizar(self, db: Session, cliente_id: int, form: dict) -> Cliente:
        dados = ClienteUpdate(
            nome=form.get("nome") or None,
            cnpj_cpf=(form.get("cnpj_cpf") or None),
            telefone=(form.get("telefone") or None),
            endereco=(form.get("endereco") or None),
            condicao_pagto_padrao=(form.get("condicao_pagto_padrao") or None),
            limite_credito=_dec_opt(form.get("limite_credito")),
            ativo=form.get("ativo") in ("on", "true", "1", True),
        )
        return cliente_service.atualizar(db, cliente_id, dados)

    def inativar(self, db: Session, cliente_id: int) -> Cliente:
        return cliente_service.inativar(db, cliente_id)


cliente_controller = ClienteController()
