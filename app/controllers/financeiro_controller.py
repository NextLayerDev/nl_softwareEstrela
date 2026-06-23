from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core.errors import RegraNegocioError
from app.models.conta_receber import ContaReceber
from app.models.enums import StatusConta
from app.repositories.conta_repo import conta_repo
from app.schemas.financeiro import BaixaInput, FiltroContas
from app.services.financeiro_service import financeiro_service


def _data_opt(valor: str | None) -> date | None:
    if not valor or not str(valor).strip():
        return None
    return date.fromisoformat(str(valor).strip())


def _int_opt(valor: str | None) -> int | None:
    if not valor or not str(valor).strip():
        return None
    return int(valor)


def _status_opt(valor: str | None) -> StatusConta | None:
    if not valor or not str(valor).strip():
        return None
    try:
        return StatusConta(valor.strip().lower())
    except ValueError as exc:
        raise RegraNegocioError(f"Status inválido: {valor}") from exc


class FinanceiroController:
    def listar(self, db: Session, args: dict) -> list[ContaReceber]:
        filtro = FiltroContas(
            status=_status_opt(args.get("status")),
            cliente_id=_int_opt(args.get("cliente_id")),
            venc_de=_data_opt(args.get("venc_de")),
            venc_ate=_data_opt(args.get("venc_ate")),
        )
        return financeiro_service.listar(db, filtro)

    def recebidas_hoje(self, db: Session) -> list[ContaReceber]:
        return conta_repo.recebidas_no_dia(db, date.today())

    def baixar(self, db: Session, conta_id: int, form: dict, usuario_id: int) -> ContaReceber:
        dados = BaixaInput(
            data_pagamento=_data_opt(form.get("data_pagamento")),
            forma_pagamento=form.get("forma_pagamento", ""),
        )
        return financeiro_service.baixar(db, conta_id, dados, usuario_id)

    def marcar_atrasados(self, db: Session) -> int:
        return financeiro_service.marcar_atrasados(db)


financeiro_controller = FinanceiroController()
