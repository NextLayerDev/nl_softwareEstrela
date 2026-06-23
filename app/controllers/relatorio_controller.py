from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.services.relatorio_service import relatorio_service


def _data_opt(valor: str | None) -> date | None:
    if not valor or not str(valor).strip():
        return None
    return date.fromisoformat(str(valor).strip())


def _int_opt(valor: str | None) -> int | None:
    if not valor or not str(valor).strip():
        return None
    return int(valor)


class RelatorioController:
    def vendas(self, db: Session, args: dict, vendedor_forcado: int | None = None) -> dict:
        return relatorio_service.vendas(
            db,
            de=_data_opt(args.get("de")),
            ate=_data_opt(args.get("ate")),
            vendedor_id=vendedor_forcado
            if vendedor_forcado is not None
            else _int_opt(args.get("vendedor_id")),
            cliente_id=_int_opt(args.get("cliente_id")),
        )

    def vendas_xlsx(self, db: Session, args: dict, vendedor_forcado: int | None = None) -> bytes:
        return relatorio_service.vendas_xlsx(
            db,
            de=_data_opt(args.get("de")),
            ate=_data_opt(args.get("ate")),
            vendedor_id=vendedor_forcado
            if vendedor_forcado is not None
            else _int_opt(args.get("vendedor_id")),
            cliente_id=_int_opt(args.get("cliente_id")),
        )

    def curva_abc(self, db: Session, args: dict) -> dict:
        return relatorio_service.curva_abc(
            db, de=_data_opt(args.get("de")), ate=_data_opt(args.get("ate"))
        )

    def curva_abc_xlsx(self, db: Session, args: dict) -> bytes:
        return relatorio_service.curva_abc_xlsx(
            db, de=_data_opt(args.get("de")), ate=_data_opt(args.get("ate"))
        )

    def valorizacao(self, db: Session) -> dict:
        return relatorio_service.valorizacao(db)

    def valorizacao_xlsx(self, db: Session) -> bytes:
        return relatorio_service.valorizacao_xlsx(db)


relatorio_controller = RelatorioController()
