"""Histórico de deploys e cache do status do CI.

`deploys` é escrita pelos DOIS lados: a aplicação insere a solicitação (status
`solicitado`) e o **agente do host** — que roda fora do Docker, via psql — atualiza o
andamento e o resultado. Por isso os campos de estado são `String` e não `sa.Enum`: um
tipo ENUM do Postgres obrigaria o agente a conhecer o catálogo de tipos para escrever.

O container `db` é o único que NÃO é recriado durante uma atualização, então esta tabela
é a única testemunha do que aconteceu enquanto o `app` estava morto — é daqui que a aba
lê o log depois que o WebSocket cai.

`origem` e `imagem_digest` são **somente-agente**: a aplicação nunca os escreve e o
agente nunca os lê daqui (resolve tudo na allowlist dele). Se o agente confiasse nestes
campos, um INSERT forjado escolheria a imagem a rodar e pularia a verificação do cosign.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.usuario import Usuario


class Deploy(Base):
    """Uma atualização (ou rollback). Status é monotônico: nunca volta, nunca é apagado."""

    __tablename__ = "deploys"

    id: Mapped[int] = mapped_column(primary_key=True)

    # atualizacao | rollback
    acao: Mapped[str] = mapped_column(String(20))
    # solicitado -> executando -> sucesso | falha | falhou_revertido | recusado | cancelado
    status: Mapped[str] = mapped_column(String(20), default="solicitado", index=True)

    versao_anterior: Mapped[str | None] = mapped_column(String(60))
    versao_nova: Mapped[str] = mapped_column(String(60))

    # Somente-agente (ver docstring do módulo).
    imagem_digest: Mapped[str | None] = mapped_column(String(120))
    origem: Mapped[str | None] = mapped_column(String(20))

    # Copiados dos labels OCI da imagem: alimentam o aviso vermelho de rollback.
    alembic_head: Mapped[str | None] = mapped_column(String(40))
    rollback_seguro: Mapped[bool | None] = mapped_column(Boolean)

    # Nulo quando o próprio agente iniciou (reconciliação no boot, por exemplo).
    solicitado_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    usuario: Mapped[Usuario | None] = relationship(lazy="raise")

    solicitado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    iniciado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Batido pelo agente enquanto trabalha: sem isso, agente morto é indistinguível
    # de deploy demorado.
    heartbeat_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    duracao_seg: Mapped[int | None] = mapped_column(Integer)
    log: Mapped[str | None] = mapped_column(Text)

    @property
    def em_voo(self) -> bool:
        return self.status in ("solicitado", "executando")

    @property
    def falhou(self) -> bool:
        return self.status in ("falha", "falhou_revertido", "recusado")


class CiStatusCache(Base):
    """Linha única (id=1) com a última consulta ao GitHub.

    Mora no Postgres, e não em memória, porque são 3 workers Gunicorn: cache em memória
    triplicaria as chamadas e faria o card piscar entre 3 estados a cada F5.
    """

    __tablename__ = "ci_status_cache"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)

    consultado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    erro: Mapped[str | None] = mapped_column(String(200))

    # [{workflow, status, conclusion, url, sha, criado_em}, ...]
    runs: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    release_tag: Mapped[str | None] = mapped_column(String(60))
    release_url: Mapped[str | None] = mapped_column(String(300))
    release_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Circuit breaker: sem internet, para de tentar a cada tick e recua.
    proxima_tentativa_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    falhas_seguidas: Mapped[int] = mapped_column(Integer, default=0)
