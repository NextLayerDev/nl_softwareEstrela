from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.enums import StatusConta

FORMAS_PAGAMENTO = ("pix", "boleto", "dinheiro")


class BaixaInput(BaseModel):
    """Entrada da baixa de um recebimento."""

    data_pagamento: date | None = None
    forma_pagamento: str

    @field_validator("forma_pagamento")
    @classmethod
    def _forma_valida(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in FORMAS_PAGAMENTO:
            raise ValueError(f"Forma de pagamento inválida. Use: {', '.join(FORMAS_PAGAMENTO)}.")
        return v


class FiltroContas(BaseModel):
    """Filtros da listagem de contas a receber."""

    status: StatusConta | None = None
    cliente_id: int | None = None
    venc_de: date | None = None
    venc_ate: date | None = None


class ContaReceberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pedido_id: int
    parcela: int
    valor: Decimal
    vencimento: date
    status: StatusConta
    forma_pagamento: str | None
    baixado_em: datetime | None
    baixado_por: int | None
