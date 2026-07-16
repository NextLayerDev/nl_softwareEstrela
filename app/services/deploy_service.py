r"""Solicitação de atualização e de reversão (rollback) pela aba /deploy.

Este service **não executa nada**. Ele insere uma linha em `deploys` com status
`solicitado` e toca uma campainha (`pg_notify`). Quem roda `docker` é o agente que vive
no HOST, sob systemd, fora do Docker: o container do app nunca recebe o
`/var/run/docker.sock` — seria root no host a partir de uma web app que aceita upload de
XLSX e renderiza PDF.

A tag digitada na tela termina, do outro lado da campainha, dentro de um comando `docker`
no host. Daí as três travas:

1. **Regex ancorada com `\A`/`\Z`, nunca `^`/`$`.** Em Python o `$` casa também *antes* de
   um `\n` final, então `^v[0-9.]+$` aceita alegremente ``"v1.2.3\nrm -rf /"``. `\Z` é o
   fim absoluto da string.
2. **Rejeição ANTES do INSERT.** Uma tag inválida não pode nem virar linha no banco: a
   linha é justamente o que o agente vai ler.
3. **Allowlist.** A tag precisa existir em `agente.releases_disponiveis`, publicada pelo
   agente. A app não escolhe imagem — apenas aponta para uma que o agente já conhece.

Esta validação existe **de novo** dentro do agente, de propósito. Não é redundância
preguiçosa: o agente lê uma tabela que qualquer coisa com acesso ao Postgres pode
escrever, então ele não confia na app. Se um dia um INSERT forjado pular este arquivo, o
agente ainda recusa.

Por isso, também, `imagem_digest` e `origem` são somente-agente: se o agente lesse de
`deploys` qual imagem rodar, um INSERT forjado escolheria a imagem e pularia o cosign.

Contrato de `agente.releases_disponiveis` (criada pelo instalar-agente.sh, FORA do
Alembic — por isso a tabela ausente é um estado normal, não um erro):

    tag             text primary key   -- v1.2.3
    alembic_head    text               -- label OCI da imagem; NULL = desconhecida
    rollback_seguro boolean            -- label OCI da imagem
    git_sha         text
    publicado_em    timestamptz
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.core.eventos import emitir
from app.core.versao import versao_build
from app.models.deploy import Deploy
from app.models.enums import Perfil
from app.models.usuario import Usuario
from app.repositories.deploy_repo import deploy_repo
from app.services.saude_service import saude_service

logger = logging.getLogger("estrela.deploy")

# Canal DEDICADO do agente. Não é o `estrela_eventos` do realtime de propósito: aquele
# canal carrega todo pedido confirmado e toda baixa de estoque do sistema, e um pedido
# faturado não pode acordar o único componente que tem docker no host. Payload sempre
# VAZIO — o agente relê tudo do banco; nada que ele decide viaja dentro do NOTIFY.
CANAL_DEPLOY = "estrela_deploy"

# Audiência dos eventos desta tela. Só o dev enxerga /deploy — nem o admin.
AUD_DEV: tuple[str, ...] = (Perfil.DEV.value,)

# Teto curto: uma tag de release não passa nem perto disto, e o teto limita o estrago de
# qualquer coisa que escape da regex.
TAG_MAX = 32

# Âncoras absolutas (\A/\Z) + classe fechada. \Z e NÃO $: o "$" casa antes de um \n final
# e deixaria passar "v1.2.3\nrm -rf /". Sem `/`, sem espaço, sem `;`, sem `$`, sem crase.
#
# SemVer estrito, IDÊNTICO ao _RE_TAG do scripts/agente/agente.py — e não uma classe de
# caracteres genérica. A validação é duplicada de propósito (o agente não confia na app),
# mas as duas precisam CONCORDAR sobre o que é válido: uma app mais permissiva aceitaria
# "latest" ou "v1.2", gravaria a linha, e o agente recusaria depois — pedido morto no
# banco e o usuário vendo "versão indisponível" em vez de "versão inválida". Além disso
# "latest" é tag MÓVEL, que este desenho proíbe (destrói saber o que está rodando e o
# rollback). Ver tests/test_deploy.py::test_regex_da_app_e_do_agente_concordam.
_NUM = r"(?:0|[1-9][0-9]{0,3})"
_TAG_RE = re.compile(rf"\Av{_NUM}\.{_NUM}\.{_NUM}(?:-[0-9A-Za-z][0-9A-Za-z.]{{0,15}})?\Z")

_SQL_RELEASES = text(
    "SELECT tag, alembic_head, rollback_seguro, git_sha, publicado_em "
    "FROM agente.releases_disponiveis ORDER BY publicado_em DESC NULLS LAST"
)


@dataclass(frozen=True)
class Release:
    """Uma versão que o agente já baixou e verificou, e para a qual ele topa apontar."""

    tag: str
    alembic_head: str | None
    rollback_seguro: bool | None
    git_sha: str | None
    publicado_em: datetime | None
    atual: bool
    head_atual: str | None

    @property
    def git_sha_curto(self) -> str:
        return self.git_sha[:7] if self.git_sha else ""

    @property
    def cruza_migration(self) -> bool:
        """A versão espera um schema diferente do que está no banco AGORA."""
        return bool(self.alembic_head and self.head_atual and self.alembic_head != self.head_atual)

    @property
    def arriscada(self) -> bool:
        """Voltar para cá NÃO é o rollback instantâneo da Vercel.

        Lá não há banco: trocar a imagem é reversível de graça. Aqui, se a versão cruzar
        migration, voltar a imagem roda código velho contra um schema novo. Head
        desconhecida também conta como arriscada — não dá para afirmar que é seguro, e
        mentir na tela é pior que não ter o botão.
        """
        return self.rollback_seguro is False or self.cruza_migration or self.alembic_head is None

    @property
    def motivo_risco(self) -> str:
        if not self.arriscada:
            return ""
        if self.rollback_seguro is False:
            return (
                "Esta versão foi publicada marcada como reversão insegura: quem a "
                "construiu sinalizou que voltar para ela quebra algo."
            )
        if self.cruza_migration:
            return (
                f"O banco está na migration {self.head_atual} e esta versão foi feita "
                f"para a {self.alembic_head}. O banco NÃO volta — o agente nunca faz "
                "downgrade nem restaura backup, porque isso apagaria em silêncio os "
                "pedidos feitos desde então. O código antigo vai rodar contra o banco "
                "novo."
            )
        return (
            "Não dá para saber contra qual migration esta versão foi construída, então "
            "não dá para garantir que ela funciona com o banco atual."
        )


@dataclass(frozen=True)
class Releases:
    agente_instalado: bool
    itens: list[Release]
    versao_atual: str
    head_atual: str | None


class DeployService:
    # ------------------------------------------------------------- validação
    def validar_tag(self, tag: str) -> str:
        """Única porta de entrada da tag. Levanta ANTES de qualquer escrita.

        Pública porque é ela que os testes apontam: é a fronteira entre um <input> e um
        comando no host.
        """
        limpa = (tag or "").strip()
        if not limpa:
            raise RegraNegocioError("Informe a versão.")
        if len(limpa) > TAG_MAX:
            raise RegraNegocioError(f"Versão inválida: no máximo {TAG_MAX} caracteres.")
        if not _TAG_RE.fullmatch(limpa):
            raise RegraNegocioError(
                "Versão inválida. Use o formato vX.Y.Z (exemplo: v1.2.3). "
                "Versões móveis como 'latest' não são aceitas."
            )
        return limpa

    # ------------------------------------------------------------- allowlist
    def _linhas_allowlist(self, db: Session) -> list[dict] | None:
        """Lê a allowlist do agente. `None` = agente não instalado (estado normal).

        SAVEPOINT porque o schema `agente` não existe até o instalar-agente.sh rodar: sem
        ele, a primeira consulta que falha aborta a transação inteira e tudo que vier
        depois no mesmo request passa a mentir com InFailedSqlTransaction.
        """
        try:
            with db.begin_nested():
                return [dict(r) for r in db.execute(_SQL_RELEASES).mappings()]
        except Exception:  # noqa: BLE001 - tabela ausente é normal; qualquer erro = sem agente
            logger.debug("agente.releases_disponiveis indisponível.", exc_info=True)
            return None

    def releases(self, db: Session) -> Releases:
        """Versões que o agente aceita, marcando a que está rodando e as arriscadas."""
        v = versao_build()
        # A imagem carrega a tag no APP_TAG e o número no APP_VERSION; em dev os dois
        # ficam vazios. Aceita os dois para o selo "Atual" não depender de qual foi
        # injetado no build.
        atuais = {x for x in (v.tag, v.versao) if x and x != "desconhecida"}
        versao_atual = v.tag if v.tag and v.tag != "desconhecida" else v.versao

        # Head APLICADA no banco (não a esperada pelo código): é contra ela que o código
        # da versão-alvo vai rodar.
        head_atual = saude_service._head_aplicada(db)

        linhas = self._linhas_allowlist(db)
        if linhas is None:
            return Releases(False, [], versao_atual, head_atual)

        itens = [
            Release(
                tag=str(linha["tag"]),
                alembic_head=linha.get("alembic_head"),
                rollback_seguro=linha.get("rollback_seguro"),
                git_sha=linha.get("git_sha"),
                publicado_em=linha.get("publicado_em"),
                atual=str(linha["tag"]) in atuais,
                head_atual=head_atual,
            )
            for linha in linhas
        ]
        return Releases(True, itens, versao_atual, head_atual)

    # -------------------------------------------------------------- campainha
    def _tocar_campainha(self, db: Session) -> None:
        """Acorda o agente. Transacional como todo NOTIFY do Postgres.

        Se o request der rollback, a campainha não toca — o agente jamais acorda para uma
        solicitação que não existe. E se o agente estiver dormindo, nada se perde: a
        solicitação está no banco e ele relê tudo ao acordar.
        """
        db.execute(text("SELECT pg_notify(:canal, '')"), {"canal": CANAL_DEPLOY})

    # ------------------------------------------------------------ solicitação
    def _solicitar(
        self,
        db: Session,
        *,
        acao: str,
        tag: str,
        usuario: Usuario,
        confirmacao: str = "",
    ) -> Deploy:
        # PRIMEIRA linha do fluxo, antes de tocar no banco.
        alvo = self.validar_tag(tag)

        em_voo = deploy_repo.em_voo(db)
        if em_voo is not None:
            rotulo = "atualização" if em_voo.acao == "atualizacao" else "reversão"
            raise RegraNegocioError(
                f"Já existe uma {rotulo} em andamento (#{em_voo.id} → {em_voo.versao_nova}). "
                "Aguarde ela terminar ou cancele antes de solicitar outra."
            )

        info = self.releases(db)
        if not info.agente_instalado:
            raise RegraNegocioError(
                "O agente de deploy não está instalado neste servidor. Enquanto isso, as "
                "atualizações continuam sendo feitas pelo terminal."
            )

        rel = next((r for r in info.itens if r.tag == alvo), None)
        if rel is None:
            raise RegraNegocioError(
                f"A versão {alvo} não está na lista de versões liberadas pelo agente."
            )
        if rel.atual:
            raise RegraNegocioError(f"A versão {alvo} já é a que está rodando.")

        # A trava de digitar a versão NÃO é enfeite da tela: uma confirmação que só existe
        # no HTML é contornada por qualquer POST direto.
        if acao == "rollback" and rel.arriscada and (confirmacao or "").strip() != alvo:
            raise RegraNegocioError(
                f"Esta reversão não é segura e exige confirmação: digite {alvo} "
                "exatamente como aparece na tela."
            )

        d = Deploy(
            acao=acao,
            status="solicitado",
            versao_anterior=info.versao_atual,
            versao_nova=alvo,
            # Copiados da ALLOWLIST, nunca do formulário: aqui é só registro para a tela.
            alembic_head=rel.alembic_head,
            rollback_seguro=rel.rollback_seguro,
            solicitado_por_id=usuario.id,
        )
        db.add(d)
        db.flush()

        emitir(
            db,
            "deploy.solicitado",
            {"id": d.id, "acao": acao, "versao": alvo},
            audiencia=AUD_DEV,
        )
        self._tocar_campainha(db)
        logger.info("Deploy #%s solicitado por %s: %s -> %s", d.id, usuario.email, acao, alvo)
        return d

    def solicitar_atualizacao(self, db: Session, tag: str, usuario: Usuario) -> Deploy:
        return self._solicitar(db, acao="atualizacao", tag=tag, usuario=usuario)

    def solicitar_rollback(
        self, db: Session, tag: str, usuario: Usuario, confirmacao: str = ""
    ) -> Deploy:
        return self._solicitar(
            db, acao="rollback", tag=tag, usuario=usuario, confirmacao=confirmacao
        )

    # -------------------------------------------------------------- cancelar
    def cancelar(self, db: Session, deploy_id: int, usuario: Usuario) -> Deploy:
        """Destrava um deploy órfão (agente morto entre o INSERT e a execução).

        Só mexe na linha: quem obedece é o agente, que relê o status antes de agir. Um
        deploy já concluído não volta — o status é monotônico.
        """
        d = deploy_repo.get(db, deploy_id)
        if d is None:
            raise NaoEncontradoError("Deploy não encontrado.")
        if not d.em_voo:
            raise RegraNegocioError(f"Este deploy já terminou ({d.status}); não há o que cancelar.")

        agora = datetime.now(UTC)
        d.status = "cancelado"
        d.concluido_em = agora
        carimbo = agora.strftime("%d/%m/%Y %H:%M")
        d.log = f"{d.log or ''}\n[{carimbo}] Cancelado na tela por {usuario.nome}.".strip()
        # A SessionLocal é autoflush=False: sem este flush o UPDATE só sairia no commit e
        # qualquer leitura no mesmo request (a tela é reconstruída logo abaixo, no redirect)
        # ainda veria o deploy como em voo.
        db.flush()

        emitir(db, "deploy.cancelado", {"id": d.id, "versao": d.versao_nova}, audiencia=AUD_DEV)
        # Toca de novo: se o agente ainda não tinha começado, ele relê e desiste.
        self._tocar_campainha(db)
        logger.info("Deploy #%s cancelado por %s.", d.id, usuario.email)
        return d


deploy_service = DeployService()
