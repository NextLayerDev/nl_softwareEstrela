"""Sondas de saúde do sistema, coletadas de dentro do container.

REGRA DESTE MÓDULO: nenhuma sonda pode derrubar a página. A aba /deploy é justamente a
tela que precisa funcionar quando o resto está quebrado — uma sonda que levanta exceção
transformaria o diagnóstico em erro 500 e esconderia o que o admin precisa ver.

Cada sonda que toca o banco roda em SAVEPOINT (`db.begin_nested()`). Sem isso, a primeira
sonda que falha aborta a transação e TODAS as seguintes passam a mentir com
"InFailedSqlTransaction" — a tela diria que o alembic_version é ilegível num banco
perfeitamente saudável.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.versao import VersaoBuild, versao_build

logger = logging.getLogger("estrela.saude")

# Bind mount read-only do diretório de backups do host (ver docker-compose.prod.yml).
# Em dev não existe, e a sonda simplesmente diz que não sabe.
DIR_BACKUP = Path("/backup")

# Estados do schema em relação ao código.
SCHEMA_EM_DIA = "em_dia"
SCHEMA_ATRAS = "atras"  # banco atrás do código: migration pendente
SCHEMA_A_FRENTE = "a_frente"  # banco à frente: rollback sob expand/contract
SCHEMA_DESCONHECIDO = "desconhecido"

# Estado do auto-deploy, publicado pelo agente do host em `agente.servidor_status`.
# Estas três colunas são MAIS NOVAS que a tabela: um servidor com o agente anterior a
# este recurso tem a tabela mas não as colunas, e a tela precisa dizer "não sei" em vez
# de estourar. Por isso a consulta é isolada em SAVEPOINT e qualquer erro vira
# `suportado=False` — inclusive UndefinedColumn.
_SQL_AUTO = text(
    "SELECT auto_update_ativo, versao_disponivel, proxima_janela "
    "FROM agente.servidor_status ORDER BY id LIMIT 1"
)


@dataclass(frozen=True)
class EstadoAuto:
    """O que o agente diz sobre a atualização automática. Tudo pode ser desconhecido."""

    suportado: bool
    ativo: bool = False
    versao_disponivel: str | None = None
    proxima_janela: datetime | None = None


@dataclass(frozen=True)
class Sonda:
    """Resultado de uma verificação. `nivel` mapeia direto nos selos da UI."""

    rotulo: str
    valor: str
    nivel: str = "ok"  # ok | aviso | critico | neutro
    detalhe: str | None = None


@dataclass
class Saude:
    versao: VersaoBuild
    sondas: list[Sonda]
    schema_estado: str

    @property
    def tem_problema(self) -> bool:
        return any(s.nivel in ("aviso", "critico") for s in self.sondas)


class SaudeService:
    # ---------------------------------------------------------------- banco
    def _banco(self, db: Session) -> tuple[Sonda, float | None]:
        inicio = time.perf_counter()
        try:
            with db.begin_nested():
                db.execute(text("SELECT 1"))
        except Exception:
            logger.warning("Sonda de banco falhou.", exc_info=True)
            return Sonda("Banco de dados", "sem resposta", "critico"), None
        ms = (time.perf_counter() - inicio) * 1000
        nivel = "ok" if ms < 250 else "aviso"
        return Sonda("Banco de dados", f"respondendo ({ms:.0f} ms)", nivel), ms

    # ------------------------------------------------------------ migration
    def _head_aplicada(self, db: Session) -> str | None:
        try:
            with db.begin_nested():
                return db.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
        except Exception:
            logger.warning("Não foi possível ler alembic_version.", exc_info=True)
            return None

    def _head_esperada(self) -> str | None:
        """Head que ESTE código espera, lida do diretório de migrations em runtime."""
        try:
            from alembic.config import Config
            from alembic.script import ScriptDirectory

            raiz = Path(__file__).resolve().parent.parent.parent
            cfg = Config(str(raiz / "alembic.ini"))
            cfg.set_main_option("script_location", str(raiz / "alembic"))
            heads = ScriptDirectory.from_config(cfg).get_heads()
            return heads[0] if len(heads) == 1 else None
        except Exception:
            logger.warning("Não foi possível resolver a head esperada do Alembic.", exc_info=True)
            return None

    def _migration(self, db: Session) -> tuple[Sonda, str]:
        aplicada = self._head_aplicada(db)
        esperada = self._head_esperada()

        if not aplicada or not esperada:
            return (
                Sonda("Migrations", "não foi possível determinar", "aviso"),
                SCHEMA_DESCONHECIDO,
            )
        if aplicada == esperada:
            return Sonda("Migrations", f"em dia ({aplicada[:8]})", "ok"), SCHEMA_EM_DIA

        # Diferente: descobrir de que lado. Se a head aplicada existe no histórico deste
        # código, o banco está ATRÁS (falta migrar). Se não existe, o banco foi migrado
        # por uma versão mais nova — estamos sob rollback (expand/contract).
        try:
            from alembic.config import Config
            from alembic.script import ScriptDirectory

            raiz = Path(__file__).resolve().parent.parent.parent
            cfg = Config(str(raiz / "alembic.ini"))
            cfg.set_main_option("script_location", str(raiz / "alembic"))
            script = ScriptDirectory.from_config(cfg)
            script.get_revision(aplicada)
        except Exception:
            return (
                Sonda(
                    "Migrations",
                    f"banco à frente do código ({aplicada[:8]})",
                    "aviso",
                    "Rollback em vigor. O código tolera o schema novo (expand/contract).",
                ),
                SCHEMA_A_FRENTE,
            )

        return (
            Sonda(
                "Migrations",
                f"migration pendente (banco em {aplicada[:8]}, código espera {esperada[:8]})",
                "critico",
                "O app subiu sem aplicar as migrations.",
            ),
            SCHEMA_ATRAS,
        )

    # ---------------------------------------------------------------- disco
    def _disco(self) -> Sonda:
        try:
            uso = shutil.disk_usage("/")
        except OSError:
            return Sonda("Disco", "não foi possível ler", "neutro")
        livre_gb = uso.free / 1024**3
        pct_livre = uso.free / uso.total * 100
        nivel = "ok" if pct_livre >= 20 else ("aviso" if pct_livre >= 10 else "critico")
        return Sonda("Disco", f"{livre_gb:.1f} GB livres ({pct_livre:.0f}%)", nivel)

    # -------------------------------------------------------------- backups
    def _backup(self) -> Sonda:
        if not DIR_BACKUP.is_dir():
            return Sonda(
                "Último backup",
                "não visível deste container",
                "neutro",
                "Requer o bind mount read-only de /backup (produção).",
            )
        try:
            dumps = sorted(
                DIR_BACKUP.glob("estrela_*.sql.gz"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return Sonda("Último backup", "não foi possível ler", "aviso")
        if not dumps:
            return Sonda("Último backup", "nenhum encontrado", "critico")

        ultimo = dumps[0]
        quando = datetime.fromtimestamp(ultimo.stat().st_mtime, tz=UTC)
        horas = (datetime.now(UTC) - quando).total_seconds() / 3600
        tamanho_mb = ultimo.stat().st_size / 1024**2
        nivel = "ok" if horas <= 36 else "critico"
        return Sonda(
            "Último backup",
            f"há {horas:.0f} h ({tamanho_mb:.1f} MB)",
            nivel,
            ultimo.name,
        )

    # ------------------------------------------------------------- processo
    def _uptime(self) -> Sonda:
        try:
            # /proc só existe no Linux; em dev (macOS) a sonda se cala.
            with open("/proc/uptime") as f:
                segundos = float(f.read().split()[0])
        except (OSError, ValueError, IndexError):
            return Sonda("Uptime do servidor", "indisponível", "neutro")
        dias, resto = divmod(int(segundos), 86400)
        horas, resto = divmod(resto, 3600)
        return Sonda("Uptime do servidor", f"{dias}d {horas}h {resto // 60}min", "ok")

    # ---------------------------------------------------------------- agente
    def _agente(self, db: Session) -> Sonda:
        """O agente de deploy vive no host e publica um heartbeat.

        A tabela mora no schema `agente`, criado pelo instalar-agente.sh — fora do
        Alembic. Enquanto o agente não for instalado, ela não existe, e isso é um
        estado normal, não um erro.
        """
        try:
            with db.begin_nested():
                visto = db.scalar(text("SELECT max(heartbeat_em) FROM agente.servidor_status"))
        except Exception:
            return Sonda(
                "Agente de deploy",
                "não instalado",
                "neutro",
                "As atualizações continuam sendo feitas pelo terminal do servidor.",
            )
        if visto is None:
            return Sonda("Agente de deploy", "nunca reportou", "aviso")
        minutos = (datetime.now(UTC) - visto).total_seconds() / 60
        if minutos > 10:
            return Sonda(
                "Agente de deploy",
                f"sem sinal há {minutos:.0f} min",
                "critico",
                "Um agente parado deixa os botões de atualizar sem efeito.",
            )
        return Sonda("Agente de deploy", "ativo", "ok")

    def auto_update(self, db: Session) -> EstadoAuto:
        """Estado da atualização automática, como o agente o reportou.

        Três estados diferentes desabam no mesmo `suportado=False`, e de propósito: o
        schema `agente` ausente (agente não instalado), as colunas ausentes (agente
        anterior a este recurso) e a tabela sem linha (agente instalado que ainda não
        reportou) têm a mesma consequência para quem lê a tela — não dá para afirmar
        nada sobre o auto-deploy. Fingir "desligado" nesses casos seria mentir, e esta é
        a tela de diagnóstico.
        """
        try:
            with db.begin_nested():
                linha = db.execute(_SQL_AUTO).mappings().first()
        except Exception:  # noqa: BLE001 - tabela/coluna ausente é estado normal
            logger.debug("agente.servidor_status sem o estado do auto-deploy.", exc_info=True)
            return EstadoAuto(False)
        if linha is None:
            return EstadoAuto(False)

        versao = linha.get("versao_disponivel")
        return EstadoAuto(
            suportado=True,
            # `is True` e não bool(): NULL vira desligado, mas sem prometer que alguém
            # desligou de fato.
            ativo=linha.get("auto_update_ativo") is True,
            versao_disponivel=str(versao).strip() or None if versao else None,
            proxima_janela=linha.get("proxima_janela"),
        )

    # ----------------------------------------------------------------- API
    def coletar(self, db: Session) -> Saude:
        sonda_banco, _ = self._banco(db)
        sonda_mig, estado = self._migration(db)
        return Saude(
            versao=versao_build(),
            schema_estado=estado,
            sondas=[
                sonda_banco,
                sonda_mig,
                self._agente(db),
                self._backup(),
                self._disco(),
                self._uptime(),
            ],
        )

    def pronto(self, db: Session) -> tuple[bool, str]:
        """Gate usado pelo agente após um deploy (`/health/ready`).

        Não basta o processo responder: um app com schema incompatível responde `ok` no
        /health estático e o agente marcaria sucesso enquanto toda página real dá 500.
        Por isso aqui há uma query canônica de verdade, provando que o ORM casa com o
        schema.
        """
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            return False, "banco sem resposta"

        _, estado = self._migration(db)
        if estado == SCHEMA_ATRAS:
            return False, "migration pendente"

        try:
            db.execute(text("SELECT count(*) FROM produtos"))
        except Exception:
            return False, "schema incompatível com o código"
        return True, "ok"


saude_service = SaudeService()
