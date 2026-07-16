from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.core import eventos
from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.models.auditoria import Auditoria
from app.models.conta_receber import ContaReceber
from app.models.enums import StatusConta
from app.repositories.conta_repo import conta_repo
from app.schemas.financeiro import BaixaInput, FiltroContas


class FinanceiroService:
    def listar(self, db: Session, filtro: FiltroContas | None = None) -> list[ContaReceber]:
        f = filtro or FiltroContas()
        return conta_repo.listar(
            db,
            status=f.status,
            cliente_id=f.cliente_id,
            venc_de=f.venc_de,
            venc_ate=f.venc_ate,
        )

    def obter(self, db: Session, conta_id: int) -> ContaReceber:
        conta = conta_repo.get(db, conta_id)
        if conta is None:
            raise NaoEncontradoError("Conta a receber não encontrada.")
        return conta

    def baixar(
        self, db: Session, conta_id: int, dados: BaixaInput, usuario_id: int
    ) -> ContaReceber:
        """Registra o recebimento: status PAGO, baixado_em/por, forma de pagamento."""
        conta = self.obter(db, conta_id)
        if conta.status == StatusConta.PAGO:
            raise RegraNegocioError("Esta conta já foi baixada.")

        antes = {"status": conta.status.value, "forma_pagamento": conta.forma_pagamento}

        conta.status = StatusConta.PAGO
        conta.forma_pagamento = dados.forma_pagamento
        conta.baixado_em = datetime.now(UTC)
        conta.baixado_por = usuario_id

        db.add(
            Auditoria(
                usuario_id=usuario_id,
                entidade="contas_receber",
                entidade_id=conta.id,
                acao="baixar",
                antes=antes,
                depois={
                    "status": conta.status.value,
                    "forma_pagamento": conta.forma_pagamento,
                    "data_pagamento": (dados.data_pagamento or date.today()).isoformat(),
                },
            )
        )
        db.flush()
        eventos.emitir(
            db,
            "conta.baixada",
            {
                "conta_id": conta.id,
                "pedido_id": conta.pedido_id,
                "parcela": conta.parcela,
                "valor": str(conta.valor),
                "forma_pagamento": conta.forma_pagamento,
            },
            audiencia=eventos.FIN_AUD,
        )
        return conta

    def marcar_atrasados(self, db: Session, hoje: date | None = None) -> int:
        """Marca como ATRASADO toda conta PENDENTE com vencimento < hoje.

        Retorna a quantidade de contas atualizadas. Idempotente.
        """
        ref = hoje or date.today()
        contas = conta_repo.pendentes_vencidas(db, ref)
        for conta in contas:
            conta.status = StatusConta.ATRASADO
        if contas:
            db.flush()
            # Um resumo, não um evento por conta: o job noturno pode marcar centenas.
            # Emitir aqui (e não na rota) cobre também o APScheduler, que commita sozinho.
            eventos.emitir(
                db,
                "conta.atrasada",
                {"quantidade": len(contas)},
                audiencia=eventos.FIN_AUD,
            )
        return len(contas)


financeiro_service = FinanceiroService()
