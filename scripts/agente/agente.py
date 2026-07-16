#!/usr/bin/env python
"""Agente de deploy do Estrela Gestão. Roda no HOST (systemd), FORA do Docker.

POR QUE ELE EXISTE
------------------
O container `app` jamais recebe /var/run/docker.sock. Montar o socket dentro de uma web
app que aceita upload de XLSX e renderiza PDF com WeasyPrint é entregar root no host para
qualquer RCE — o socket do Docker não tem noção de permissão: quem fala com ele pode
subir um container com `-v /:/host` e acabou. Então a app só INSERE uma linha em `deploys`
e toca a campainha (`pg_notify` no canal estrela_deploy); quem executa `docker` é este
processo, que não tem porta aberta, não é acessível de fora e roda como um usuário sem
privilégio (só o grupo `docker`).

O agente também precisa sobreviver ao `up -d` que mata o próprio `app`. Por isso ele mora
fora do Docker e o log de cada deploy vai para `deploys.log`: o container `db` é o único
que NÃO é recriado numa atualização, então é a única testemunha do que aconteceu enquanto
o `app` estava morto.

REGRAS QUE NÃO SE NEGOCIAM
--------------------------
* Não importa `app.*`. Ele precisa funcionar exatamente quando a aplicação está quebrada.
  Só stdlib + psycopg.
* Não confia em NADA vindo de `deploys` além do id, da ação e da tag — e a tag é
  revalidada aqui com a mesma regex da app. A app valida para dar erro bonito; o agente
  valida porque não confia na app.
* NUNCA lê `deploys.origem` / `deploys.imagem_digest`. Esses campos são de escrita do
  agente, para exibição. Se ele os lesse, um INSERT forjado (SQL injection, bug de RBAC,
  qualquer coisa) escolheria a imagem a rodar e pularia o cosign — a assinatura viraria
  enfeite. A imagem sai SEMPRE da allowlist (`agente.releases_disponiveis`) + cosign.
* NUNCA faz downgrade do banco e NUNCA restaura backup sozinho. Restaurar apagaria em
  silêncio os pedidos digitados entre o backup e a falha. Auto-reversão reverte só a
  IMAGEM, uma vez.
* NUNCA `sys.exit(1)` no laço principal. Com `Restart=always`, sair vira crashloop e o
  agente some justo quando é preciso. Erro = logar, dormir, tentar de novo.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

import psycopg

log = logging.getLogger("estrela.agente")

CANAL_DEPLOY = "estrela_deploy"
# Mesmo canal do app/core/eventos.py: a aba /deploy escuta o realtime normal.
CANAL_EVENTOS = "estrela_eventos"

# Instância única. Sessão-level (não xact): o agente segura enquanto viver.
LOCK_AGENTE = 815_100

ESPERA_ERRO_SEG = 30
ESPERA_NOTIFY_SEG = 60  # também é o passo do polling de fallback
HEARTBEAT_SEG = 30

TIMEOUT_COSIGN = 120
TIMEOUT_PULL = 900
TIMEOUT_BACKUP = 1800
# As fotos são bytea; um rewrite de tabela leva minutos de verdade.
TIMEOUT_PREFLIGHT = 1800
TIMEOUT_UP = 600
TIMEOUT_GATE_SEG = 180

PARAR = threading.Event()
DRY_RUN = False


# ===========================================================================
# Lógica pura — sem I/O, sem banco, sem docker. É o que dá para testar.
# ===========================================================================

# Regex ANCORADA com \A e \Z. NÃO use ^/$: em Python, `$` casa TAMBÉM antes de um \n
# final, então `re.fullmatch(r"^v[\d.]+$", "v1.2.3\nrm -rf /")` ... passa. A tag sai de um
# <input> na web e termina como argumento de um comando docker no host; um \n é o começo
# de uma segunda linha em qualquer coisa que interprete a string.
_NUM = r"(?:0|[1-9][0-9]{0,3})"
_RE_TAG = re.compile(rf"\Av{_NUM}\.{_NUM}\.{_NUM}(?:-[0-9A-Za-z][0-9A-Za-z.]{{0,15}})?\Z")
_TAG_MAX = 32

# Referência de repositório OCI: minúsculo (GHCR exige), sem espaço, sem `..`.
_RE_ORIGEM = re.compile(r"\A[a-z0-9][a-z0-9._\-]*(?:[./][a-z0-9][a-z0-9._\-]*)*\Z")
_RE_DIGEST = re.compile(r"\Asha256:[0-9a-f]{64}\Z")

ACOES = ("atualizacao", "rollback")


def validar_tag(tag: object) -> bool:
    """A tag é confiável? Teto de tamanho ANTES da regex; fullmatch com regex ancorada.

    Isto é a fronteira entre "texto que um humano digitou num navegador" e "argv de um
    processo rodando no host". Nada além de vX.Y.Z[-pre] atravessa.
    """
    if not isinstance(tag, str):
        return False
    if len(tag) > _TAG_MAX:
        return False
    return _RE_TAG.fullmatch(tag) is not None


def validar_origem(origem: object) -> bool:
    """Repositório da imagem (vem da allowlist, mas a allowlist também é banco)."""
    if not isinstance(origem, str) or not origem or len(origem) > 200:
        return False
    return _RE_ORIGEM.fullmatch(origem) is not None


def validar_digest(digest: object) -> bool:
    if not isinstance(digest, str) or len(digest) != 71:
        return False
    return _RE_DIGEST.fullmatch(digest) is not None


def chave_versao(tag: str) -> tuple[int, int, int, int, str]:
    """Chave ordenável de uma tag já validada. Pré-release ordena ANTES do release."""
    m = _RE_TAG.fullmatch(tag)
    if m is None:
        raise ValueError(f"tag inválida: {tag!r}")
    nums = re.findall(r"[0-9]+", tag[1:].split("-", 1)[0])
    pre = tag.split("-", 1)[1] if "-" in tag else ""
    return (int(nums[0]), int(nums[1]), int(nums[2]), 0 if pre else 1, pre)


def comparar_versoes(a: str, b: str) -> int:
    """-1 se a < b, 0 se iguais, 1 se a > b."""
    ka, kb = chave_versao(a), chave_versao(b)
    return (ka > kb) - (ka < kb)


# --- Redator de segredos ---------------------------------------------------
# `deploys.log` é lido pelo NAVEGADOR, na aba /deploy. O que cai aqui dentro sem filtro:
# `docker compose logs` (DATABASE_URL com senha no ambiente do container), traceback do
# psycopg (a DSN aparece no repr da conexão), e o eco de qualquer variável do .env.prod.
# Um log de deploy é a última coisa que alguém pensa em tratar como dado sensível, e é
# exatamente por isso que ele vaza.
_MASCARA = "***REDIGIDO***"

# (padrão, índice do grupo que contém o segredo). Só o grupo é mascarado; o resto do
# match sobrevive, para o log continuar legível ("DATABASE_URL=postgresql://estrela:
# ***REDIGIDO***@db:5432/..." ainda diz tudo que um humano precisa saber).
_PADROES_REDACAO: tuple[tuple[re.Pattern[str], int], ...] = (
    # DSN: postgresql://usuario:SENHA@host (também postgres://, +psycopg, amqp://, ...)
    (re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:([^\s@/]+)@"), 1),
    # chave=valor / chave: valor, para nomes notoriamente sensíveis
    (
        re.compile(
            r"(?i)\b(?:password|passwd|senha|secret|token|jwt[_-]?secret|api[_-]?key|"
            r"access[_-]?key|secret[_-]?key|db[_-]?password|authorization)"
            r"\s*[=:]\s*([^\s,;\"']+)"
        ),
        1,
    ),
    # Authorization: Bearer xxx
    (re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._\-]{8,})"), 1),
)


def redigir(texto: object, segredos: Sequence[str] = ()) -> str:
    """Tira segredos do texto ANTES de ele virar linha em `deploys.log`.

    `deploys.log` é renderizado no NAVEGADOR. O que cai aqui sem filtro, na prática:
    `docker compose logs` (que ecoa o ambiente do container, DATABASE_URL inclusa),
    traceback do psycopg (a DSN aparece no repr da conexão) e o eco de qualquer variável
    do .env.prod. Log de deploy é a última coisa que alguém trata como dado sensível — é
    exatamente por isso que ele vaza.

    Duas camadas, de propósito:
      1. `segredos`: os valores LITERAIS lidos do .env.prod. É a camada que realmente
         funciona, porque não depende de adivinhar formato nenhum.
      2. padrões genéricos: pega o que a camada 1 não conhece (DSN de terceiro, token que
         apareceu no meio de um traceback).
    """
    if texto is None:
        return ""
    s = str(texto)
    for segredo in segredos:
        # Segredo curto demais daria falso positivo em todo lugar — e um segredo de 4
        # caracteres tem problema bem pior do que aparecer num log.
        if segredo and len(segredo) >= 6:
            s = s.replace(segredo, _MASCARA)
    for padrao, grupo in _PADROES_REDACAO:

        def _mascarar(m: re.Match[str], _g: int = grupo) -> str:
            achado = m.group(_g)
            if not achado:
                return m.group(0)
            return m.group(0).replace(achado, _MASCARA, 1)

        s = padrao.sub(_mascarar, s)
    return s


def parse_env_file(conteudo: str) -> dict[str, str]:
    """Parser bobo de .env, só para alimentar o redator. Não expande, não interpola."""
    saida: dict[str, str] = {}
    for linha in conteudo.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        saida[chave.strip()] = valor
    return saida


@dataclass(frozen=True)
class Release:
    """Uma linha da allowlist `agente.releases_disponiveis`.

    Os nomes das colunas seguem o que o app/services/deploy_service.py já consulta
    (`publicado_em`, `git_sha`) — a app é a parte já escrita e testada; quem se adapta é
    o agente.
    """

    tag: str
    origem: str
    imagem_digest: str | None = None
    alembic_head: str | None = None
    rollback_seguro: bool | None = None
    git_sha: str | None = None


@dataclass(frozen=True)
class Solicitacao:
    """O que a app pediu. Só isto do que veio do banco é sequer considerado."""

    id: int
    acao: str
    versao_nova: str


@dataclass(frozen=True)
class Plano:
    """Decisão do agente. `ok=False` vira status `recusado` — nunca `falha`.

    Recusar é diferente de falhar: recusa acontece ANTES de qualquer efeito colateral,
    então o sistema no ar continua intocado e não há o que reverter.
    """

    ok: bool
    motivo: str = ""
    release: Release | None = None


def decidir_plano(
    sol: Solicitacao,
    releases: dict[str, Release],
    versao_minima: str | None,
    permitir_downgrade: bool,
) -> Plano:
    """Toda a política de "esta tag pode rodar?" num só lugar, puro e testável."""
    if sol.acao not in ACOES:
        return Plano(False, f"Ação desconhecida: {sol.acao!r}.")

    if not validar_tag(sol.versao_nova):
        # Não ecoa a tag inteira: ela é entrada não confiável e o log vai para o HTML.
        amostra = repr(str(sol.versao_nova)[:40])
        return Plano(False, f"Tag recusada pela validação do agente: {amostra}.")

    release = releases.get(sol.versao_nova)
    if release is None:
        return Plano(
            False,
            f"Versão {sol.versao_nova} não está na allowlist do agente "
            "(agente.releases_disponiveis). Nada é executado a partir de uma linha "
            "de `deploys` sozinha.",
        )
    if not validar_origem(release.origem):
        return Plano(False, f"Origem inválida na allowlist para {sol.versao_nova}.")
    if release.imagem_digest is not None and not validar_digest(release.imagem_digest):
        return Plano(False, f"Digest inválido na allowlist para {sol.versao_nova}.")

    if versao_minima and validar_tag(versao_minima):
        if comparar_versoes(sol.versao_nova, versao_minima) < 0:
            if not permitir_downgrade:
                return Plano(
                    False,
                    f"Versão {sol.versao_nova} é anterior ao piso {versao_minima}. "
                    "O piso sobe a cada deploy bem-sucedido: sem ele, quem conseguisse "
                    "um RCE efêmero forçaria um downgrade para uma versão vulnerável e "
                    "transformaria acesso momentâneo em acesso permanente. Para reverter "
                    "de propósito, crie /etc/estrela-agente/permitir-downgrade no "
                    "servidor (vale uma vez).",
                )
    return Plano(True, release=release)


def ref_imagem(origem: str, digest: str) -> str:
    """`repo@sha256:...`. Sempre por digest — tag é ponteiro móvel e pode ser reapontada."""
    return f"{origem}@{digest}"


def disco_suficiente(livre: int, total: int, minimo_bytes: int, minimo_pct: float) -> bool:
    if total <= 0:
        return False
    return livre >= minimo_bytes and (livre / total * 100) >= minimo_pct


# ===========================================================================
# Configuração
# ===========================================================================


@dataclass
class Config:
    dsn: str
    projeto_dir: Path
    compose_file: Path
    env_file: Path
    env_tag: Path
    cosign_pub: Path
    cosign_bin: str
    backup_script: Path
    flag_downgrade: Path
    saude_url: str
    saude_ca: str
    saude_insecure: bool
    alerta_url: str
    disco_path: Path
    disco_min_bytes: int
    disco_min_pct: float
    segredos: tuple[str, ...] = ()

    @staticmethod
    def do_ambiente() -> Config:
        g = os.environ.get
        projeto = Path(g("ESTRELA_PROJETO_DIR", "/opt/estrela"))
        cfg = Config(
            dsn=g("ESTRELA_DSN", "").strip(),
            projeto_dir=projeto,
            compose_file=Path(g("ESTRELA_COMPOSE_FILE", str(projeto / "docker-compose.prod.yml"))),
            env_file=Path(g("ESTRELA_ENV_FILE", str(projeto / ".env.prod"))),
            env_tag=Path(g("ESTRELA_ENV_TAG", "/var/lib/estrela-agente/env.tag")),
            cosign_pub=Path(g("ESTRELA_COSIGN_PUB", "/etc/estrela-agente/cosign.pub")),
            cosign_bin=g("ESTRELA_COSIGN_BIN", "/usr/local/bin/cosign"),
            backup_script=Path(
                g("ESTRELA_BACKUP_SCRIPT", str(projeto / "scripts/backup-estrela.sh"))
            ),
            flag_downgrade=Path(
                g("ESTRELA_FLAG_DOWNGRADE", "/etc/estrela-agente/permitir-downgrade")
            ),
            saude_url=g("ESTRELA_SAUDE_URL", "https://sistema.local/health/ready"),
            saude_ca=g("ESTRELA_SAUDE_CA", "").strip(),
            saude_insecure=g("ESTRELA_SAUDE_TLS_INSECURE", "").strip() in ("1", "true", "sim"),
            alerta_url=g("ESTRELA_ALERTA_URL", "").strip(),
            disco_path=Path(g("ESTRELA_DISCO_PATH", "/var/lib")),
            disco_min_bytes=int(g("ESTRELA_DISCO_MIN_BYTES", str(3 * 1024**3))),
            disco_min_pct=float(g("ESTRELA_DISCO_MIN_PCT", "10")),
        )
        cfg.segredos = cfg._coletar_segredos()
        return cfg

    def _coletar_segredos(self) -> tuple[str, ...]:
        """Valores do .env.prod, só para o redator saber o que apagar do log.

        O agente PRECISA ler o .env.prod de qualquer forma (o `docker compose` o recebe
        via --env-file e roda como este usuário), então não há segredo novo sendo exposto
        aqui — o que há é o redator finalmente sabendo quais strings jamais podem chegar
        ao navegador.
        """
        valores: list[str] = []
        try:
            env = parse_env_file(self.env_file.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return ()
        for chave, valor in env.items():
            if re.search(r"(?i)(password|senha|secret|token|key)", chave) and valor:
                valores.append(valor)
        if self.dsn:
            m = re.search(r"://[^\s:/@]+:([^\s@]+)@", self.dsn)
            if m:
                valores.append(m.group(1))
        return tuple(valores)


# ===========================================================================
# Execução de processos
# ===========================================================================


@dataclass
class Resultado:
    rc: int
    saida: str
    erro: str

    @property
    def ok(self) -> bool:
        return self.rc == 0

    @property
    def tudo(self) -> str:
        return (self.saida + ("\n" + self.erro if self.erro else "")).strip()


def executar(argv: Sequence[str], timeout: int = 120, muta: bool = False) -> Resultado:
    """subprocess com argv e shell=False. SEMPRE.

    `shell=True` aqui significaria concatenar uma tag vinda de um <input> numa linha do
    /bin/sh. Não existe hipótese em que valha a pena.
    """
    linha = " ".join(argv)
    if DRY_RUN and muta:
        print(f"[dry-run] {linha}")
        return Resultado(0, "", "")
    log.debug("exec: %s", linha)
    try:
        p = subprocess.run(  # noqa: S603 - argv fixo/validado, shell=False
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Resultado(124, "", f"timeout após {timeout}s: {linha}")
    except OSError as e:
        return Resultado(127, "", f"não foi possível executar {argv[0]!r}: {e}")
    return Resultado(p.returncode, p.stdout or "", p.stderr or "")


def compose(cfg: Config, *args: str, env_tag: Path | None = None) -> list[str]:
    """argv base do `docker compose` deste projeto.

    Dois --env-file: o .env.prod (segredos) e o env.tag (só APP_IMAGEM). O segundo vence
    o primeiro, e é o único arquivo que o agente escreve — ele NÃO tem escrita em
    /opt/estrela, senão poderia reescrever o próprio docker-compose.prod.yml e a
    separação de privilégio inteira viraria decoração.
    """
    tag = env_tag or cfg.env_tag
    argv = [
        "docker",
        "compose",
        "--project-directory",
        str(cfg.projeto_dir),
        "-f",
        str(cfg.compose_file),
        "--env-file",
        str(cfg.env_file),
    ]
    if tag.exists() or DRY_RUN:
        argv += ["--env-file", str(tag)]
    return argv + list(args)


# ===========================================================================
# Rede / alertas
# ===========================================================================


def tem_internet(host: str = "ghcr.io", porta: int = 443, timeout: float = 5.0) -> bool:
    """Sonda de saída para a internet.

    NÃO usa ping de propósito: a unidade systemd zera o CapabilityBoundingSet, e sem
    CAP_NET_RAW o ping não abre socket — a sonda diria "sem internet" num servidor
    perfeitamente conectado e todo deploy morreria antes de começar.
    """
    try:
        with socket.create_connection((host, porta), timeout=timeout):
            return True
    except OSError:
        return False


def alertar(cfg: Config, titulo: str, corpo: str, prioridade: str = "high") -> None:
    """Alerta fora de banda (ntfy). Best-effort: nunca atrapalha o deploy.

    Fora de banda porque o canal normal de aviso é a própria aba /deploy — que fica
    inacessível exatamente quando o alerta importa.
    """
    if not cfg.alerta_url:
        return
    try:
        dados = redigir(corpo, cfg.segredos).encode("utf-8")[:3000]
        req = urllib.request.Request(  # noqa: S310 - URL fixa de config, não de usuário
            cfg.alerta_url,
            data=dados,
            method="POST",
            headers={
                "Title": redigir(titulo, cfg.segredos)[:100],
                "Priority": prioridade,
                "Tags": "warning",
            },
        )
        with urllib.request.urlopen(req, timeout=8):  # noqa: S310
            pass
    except (urllib.error.URLError, OSError, ValueError):
        log.warning("Não foi possível enviar o alerta ntfy (seguindo mesmo assim).")


def _contexto_tls(cfg: Config) -> ssl.SSLContext | None:
    if cfg.saude_insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if cfg.saude_ca and Path(cfg.saude_ca).is_file():
        return ssl.create_default_context(cafile=cfg.saude_ca)
    return None


def sondar_pronto(cfg: Config) -> tuple[bool, str]:
    """O GATE do deploy: GET /health/ready ATRAVÉS do Caddy.

    Nunca /health: aquele endpoint é estático por construção (responde 200 com o processo
    de pé e o banco em chamas). Ele existe para o HEALTHCHECK do container e é
    falso-positivo perfeito como gate de deploy. /health/ready roda uma query canônica e
    prova que o ORM casa com o schema.

    Pelo Caddy, e não direto no container, porque é assim que os 10 terminais chegam: um
    app saudável atrás de um proxy quebrado é um sistema fora do ar.
    """
    ctx = _contexto_tls(cfg)
    try:
        req = urllib.request.Request(cfg.saude_url, method="GET")  # noqa: S310
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:  # noqa: S310
            corpo = r.read(500).decode("utf-8", "replace")
            return (200 <= r.status < 300), f"HTTP {r.status} {corpo[:200]}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, ssl.SSLError, OSError) as e:
        return False, f"sem resposta: {e}"


def esperar_pronto(cfg: Config, diario: Diario, limite: int = TIMEOUT_GATE_SEG) -> tuple[bool, str]:
    fim = time.monotonic() + limite
    ultimo = "não sondado"
    while time.monotonic() < fim and not PARAR.is_set():
        ok, detalhe = sondar_pronto(cfg)
        ultimo = detalhe
        if ok:
            return True, detalhe
        PARAR.wait(5)
    return False, ultimo


# ===========================================================================
# Banco
# ===========================================================================


def conectar(cfg: Config) -> psycopg.Connection:
    return psycopg.connect(cfg.dsn, autocommit=True, connect_timeout=10)


def emitir_evento(
    conn: psycopg.Connection,
    tipo: str,
    dados: dict[str, Any],
) -> None:
    """Mesmo envelope do app/core/eventos.py — a aba /deploy usa o realtime de sempre.

    Audiência ["dev"]: deploy não é assunto de vendedor. Payload só com ids/primitivos,
    nunca HTML nem trecho de log (o log tem senha até o redator passar).
    """
    envelope = {
        "tipo": tipo,
        "audiencia": ["dev"],
        "vendedor_id": None,
        "target_usuario_id": None,
        "silencioso": False,
        "dados": dados,
        "ts": datetime.now(UTC).isoformat(),
    }
    try:
        conn.execute(
            "SELECT pg_notify(%s, %s)",
            (CANAL_EVENTOS, json.dumps(envelope, ensure_ascii=False, default=str)),
        )
    except psycopg.Error:
        log.warning("Falha ao emitir o evento %s (seguindo).", tipo, exc_info=True)


class Diario:
    """Acumula o log de um deploy e o descarrega em `deploys.log` — já redigido."""

    def __init__(self, cfg: Config, deploy_id: int) -> None:
        self.cfg = cfg
        self.deploy_id = deploy_id
        self._linhas: list[str] = []
        self._trava = threading.Lock()

    def linha(self, texto: str, *args: object) -> None:
        msg = texto % args if args else texto
        log.info("[deploy %s] %s", self.deploy_id, msg)
        carimbo = datetime.now(UTC).strftime("%H:%M:%S")
        with self._trava:
            self._linhas.append(f"{carimbo} {redigir(msg, self.cfg.segredos)}")

    def bloco(self, titulo: str, conteudo: str, limite: int = 4000) -> None:
        texto = redigir(conteudo, self.cfg.segredos).strip()
        if len(texto) > limite:
            texto = texto[:limite] + f"\n... ({len(texto) - limite} caracteres suprimidos)"
        self.linha("%s:\n%s", titulo, texto)

    def texto(self) -> str:
        with self._trava:
            return "\n".join(self._linhas)


def gravar_log(conn: psycopg.Connection, deploy_id: int, diario: Diario) -> None:
    try:
        conn.execute("UPDATE deploys SET log = %s WHERE id = %s", (diario.texto(), deploy_id))
    except psycopg.Error:
        log.warning("Não foi possível gravar o log do deploy %s.", deploy_id, exc_info=True)


def carregar_releases(conn: psycopg.Connection) -> dict[str, Release]:
    """A allowlist. É a ÚNICA fonte de "que imagem existe" — `deploys` não opina."""
    linhas = conn.execute(
        "SELECT tag, origem, imagem_digest, alembic_head, rollback_seguro, git_sha "
        "FROM agente.releases_disponiveis"
    ).fetchall()
    saida: dict[str, Release] = {}
    for tag, origem, digest, head, rb, sha in linhas:
        saida[str(tag)] = Release(
            tag=str(tag),
            origem=origem,
            imagem_digest=digest,
            alembic_head=head,
            rollback_seguro=rb,
            git_sha=sha,
        )
    return saida


def status_servidor(conn: psycopg.Connection) -> dict[str, Any]:
    linha = conn.execute(
        "SELECT versao_atual, versao_minima, imagem_atual, imagem_anterior "
        "FROM agente.servidor_status WHERE id = 1"
    ).fetchone()
    if linha is None:
        return {}
    return {
        "versao_atual": linha[0],
        "versao_minima": linha[1],
        "imagem_atual": linha[2],
        "imagem_anterior": linha[3],
    }


def bater_status(cfg: Config, conn: psycopg.Connection) -> None:
    """Heartbeat + disco. A sonda `_agente` do saude_service lê exatamente esta linha."""
    livre = total = 0
    try:
        uso = shutil.disk_usage(cfg.disco_path)
        livre, total = uso.free, uso.total
    except OSError:
        pass
    try:
        conn.execute(
            "INSERT INTO agente.servidor_status (id, heartbeat_em, disco_livre_bytes) "
            "VALUES (1, now(), %s) "
            "ON CONFLICT (id) DO UPDATE SET heartbeat_em = now(), disco_livre_bytes = %s",
            (livre, livre),
        )
    except psycopg.Error:
        log.warning("Não foi possível bater o heartbeat.", exc_info=True)
    if total and not disco_suficiente(livre, total, cfg.disco_min_bytes, cfg.disco_min_pct):
        pct = livre / total * 100
        if pct < 10:
            alertar(
                cfg,
                "Estrela: disco quase cheio",
                f"Apenas {livre / 1024**3:.1f} GB livres ({pct:.0f}%). "
                "Sem espaço não há pull nem backup — o deploy vai começar a ser recusado.",
            )


class Pulso:
    """Bate `deploys.heartbeat_em` a cada 30s enquanto o deploy roda.

    Sem isso, um agente morto no meio de um `docker pull` é indistinguível de um deploy
    lento: a tela mostraria "executando" para sempre e ninguém saberia se pode mexer.
    """

    def __init__(self, cfg: Config, deploy_id: int, diario: Diario) -> None:
        self.cfg = cfg
        self.deploy_id = deploy_id
        self.diario = diario
        self._parar = threading.Event()
        self._t = threading.Thread(target=self._laco, name="pulso", daemon=True)

    def __enter__(self) -> Pulso:
        self._t.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._parar.set()
        self._t.join(timeout=5)

    def _laco(self) -> None:
        # Conexão própria: a principal está ocupada e o LISTEN não pode ser interrompido.
        while not self._parar.wait(HEARTBEAT_SEG):
            try:
                with conectar(self.cfg) as c:
                    c.execute(
                        "UPDATE deploys SET heartbeat_em = now(), log = %s WHERE id = %s",
                        (self.diario.texto(), self.deploy_id),
                    )
                    c.execute("UPDATE agente.servidor_status SET heartbeat_em = now() WHERE id = 1")
            except psycopg.Error:
                log.warning("Heartbeat falhou (seguindo).", exc_info=True)


# ===========================================================================
# Docker
# ===========================================================================


def container_db(cfg: Config) -> str | None:
    """Id do container do banco, resolvido pelo compose.

    NUNCA por nome fixo. O default do backup-estrela.sh é
    "estrela_softwarelocal-db-1" — o nome do diretório de DESENVOLVIMENTO. No servidor o
    projeto vive em /opt/estrela e o container chama-se estrela-db-1, então o `docker exec`
    do backup diário nunca encontrou o container. É por isso que o backup provavelmente
    nunca rodou uma única vez, e um `docker exec` num nome inexistente sai com erro que
    ninguém lê num cron.
    """
    r = executar(compose(cfg, "ps", "-q", "db"), timeout=60)
    if not r.ok:
        return None
    cid = r.saida.strip().splitlines()
    return cid[0].strip() if cid and cid[0].strip() else None


def digest_por_cosign(cfg: Config, origem: str, tag: str, diario: Diario) -> str | None:
    """Verifica a assinatura e devolve o digest que ela cobre.

    Esta é a única fonte do digest. É deliberado: a corrente inteira de confiança do
    deploy é "a chave pública em /etc/estrela-agente/cosign.pub assinou este digest".
    Se o digest viesse de outro lugar, o cosign estaria verificando uma coisa e o docker
    rodando outra.
    """
    if not cfg.cosign_pub.is_file():
        diario.linha("ERRO: chave pública do cosign ausente em %s.", cfg.cosign_pub)
        return None
    alvo = f"{origem}:{tag}"
    diario.linha("Verificando a assinatura de %s...", alvo)
    r = executar(
        [
            cfg.cosign_bin,
            "verify",
            "--key",
            str(cfg.cosign_pub),
            "--output",
            "json",
            alvo,
        ],
        timeout=TIMEOUT_COSIGN,
    )
    if not r.ok:
        diario.bloco("cosign verify FALHOU", r.tudo)
        return None
    try:
        payload = json.loads(r.saida)
        digest = payload[0]["critical"]["image"]["docker-manifest-digest"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        diario.bloco("cosign devolveu algo que não consigo interpretar", r.saida)
        return None
    if not validar_digest(digest):
        diario.linha("ERRO: digest fora do formato esperado.")
        return None
    diario.linha("Assinatura confere. Digest: %s", digest)
    return digest


def ler_labels(imagem: str, diario: Diario) -> dict[str, str] | None:
    """Labels OCI da imagem. FALHA FECHADO: None = aborta o deploy.

    Tratar "não consegui ler" como `{}` e seguir seria o pior dos dois mundos — o agente
    perderia a única chance de saber se aquela imagem é segura de reverter, e seguiria
    assim mesmo, silenciosamente.
    """
    r = executar(["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", imagem])
    if not r.ok:
        diario.bloco("Não foi possível inspecionar a imagem", r.tudo)
        return None
    try:
        labels = json.loads(r.saida.strip() or "null")
    except json.JSONDecodeError:
        diario.bloco("Labels da imagem não são JSON válido", r.saida)
        return None
    if labels is None:
        # Imagem sem nenhum label: não é a nossa, ou o build saiu errado.
        diario.linha("ERRO: a imagem não tem labels OCI. Abortando (falha fechado).")
        return None
    if not isinstance(labels, dict):
        diario.linha("ERRO: labels da imagem em formato inesperado. Abortando.")
        return None
    return {str(k): str(v) for k, v in labels.items()}


def podar_imagens(cfg: Config, origem: str, manter: Sequence[str], diario: Diario) -> None:
    """Apaga imagens antigas do repositório, PRESERVANDO a atual e a anterior.

    Nunca `docker image prune -a`. O rollback offline depende de a imagem anterior estar
    no disco: o mini PC pode estar sem internet exatamente no dia em que a atualização dá
    errado, e aí `prune -a` teria apagado a única saída.
    """
    r = executar(["docker", "images", "--no-trunc", "--format", "{{.Digest}}", origem])
    if not r.ok:
        return
    guardados = {m for m in manter if m}
    for digest in {ln.strip() for ln in r.saida.splitlines() if ln.strip()}:
        if not validar_digest(digest) or ref_imagem(origem, digest) in guardados:
            continue
        rm = executar(["docker", "rmi", ref_imagem(origem, digest)], timeout=120, muta=True)
        if rm.ok:
            diario.linha("Imagem antiga removida: %s", digest[:19])


def escrever_env_tag(destino: Path, imagem: str) -> None:
    """Grava APP_IMAGEM atomicamente.

    `os.replace` no MESMO diretório: um `up -d` que leia o arquivo pela metade subiria com
    APP_IMAGEM vazio, o compose cairia no default do docker-compose.prod.yml e o servidor
    voltaria para uma versão que ninguém pediu. A troca é atômica ou não é.
    """
    tmp = destino.with_suffix(destino.suffix + ".tmp")
    conteudo = f"# Gerado pelo agente de deploy. NÃO edite à mão.\nAPP_IMAGEM={imagem}\n"
    tmp.write_text(conteudo, encoding="utf-8")
    os.replace(tmp, destino)


def subir(cfg: Config, diario: Diario) -> Resultado:
    r = executar(compose(cfg, "up", "-d", "--wait"), timeout=TIMEOUT_UP, muta=True)
    if not r.ok:
        diario.bloco("docker compose up FALHOU", r.tudo)
    return r


# ===========================================================================
# Etapas do deploy
# ===========================================================================


def etapa_backup(cfg: Config, diario: Diario) -> bool:
    """Backup ANTES de qualquer coisa que mexa no banco. Falhou = aborta. Regra dura.

    A tentação de seguir "só desta vez" é exatamente o caminho para descobrir que não há
    backup no dia em que uma migration corrompe uma tabela. Um deploy adiado custa
    minutos; um deploy sem backup pode custar o dia inteiro de pedidos.
    """
    if not cfg.backup_script.is_file():
        diario.linha("ERRO: script de backup não encontrado em %s.", cfg.backup_script)
        return False
    cid = container_db(cfg)
    if not cid:
        diario.linha("ERRO: container do banco não encontrado via `docker compose ps -q db`.")
        return False

    diario.linha("Backup do banco antes de tocar em qualquer coisa (container %s)...", cid[:12])
    if DRY_RUN:
        print(f"[dry-run] DB_CONTAINER={cid} {cfg.backup_script}")
        return True
    env = dict(os.environ)
    env["DB_CONTAINER"] = cid  # resolvido agora; o default do script está errado
    try:
        p = subprocess.run(  # noqa: S603 - caminho de config, shell=False
            ["/bin/bash", str(cfg.backup_script)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_BACKUP,
            check=False,
            env=env,
        )
        r = Resultado(p.returncode, p.stdout or "", p.stderr or "")
    except (subprocess.TimeoutExpired, OSError) as e:
        diario.linha("ERRO ao executar o backup: %s", e)
        return False
    if not r.ok:
        diario.bloco("BACKUP FALHOU — deploy abortado", r.tudo)
        return False
    diario.bloco("Backup concluído", r.saida, limite=800)
    return True


def etapa_preflight(cfg: Config, env_tag_novo: Path, diario: Diario) -> tuple[bool, bool]:
    """Roda o migrar_seguro.py num container EFÊMERO com a imagem nova.

    Este é o ganho central do desenho: a migration sai do caminho crítico do app. Se ela
    falhar aqui, o `env.tag` continua INTOCADO, o `up -d` nunca acontece, o app ANTIGO
    segue no ar e o pessoal do balcão não percebe absolutamente nada. Sem o pré-flight, a
    migration só falharia dentro do entrypoint do container novo — ou seja, com o app
    velho já derrubado e o `restart: always` batendo a cabeça em loop.

    Usa `compose run` (e não `docker run` cru) porque assim o DATABASE_URL, a rede e o
    depends_on saem do próprio compose: o agente não precisa conhecer a senha do banco
    para montar a DSN.

    :returns: (sucesso, schema_a_frente)
    """
    diario.linha("Pré-flight: aplicando migrations com a imagem nova, sem trocar o app...")
    r = executar(
        compose(
            cfg,
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "app",
            "/app/scripts/migrar_seguro.py",
            env_tag=env_tag_novo,
        ),
        timeout=TIMEOUT_PREFLIGHT,
        muta=True,
    )
    diario.bloco("Saída do migrar_seguro", r.tudo)
    if not r.ok:
        diario.linha(
            "PRÉ-FLIGHT FALHOU (rc=%s). O env.tag NÃO foi tocado e a versão anterior "
            "continua no ar — ninguém no balcão percebeu nada.",
            r.rc,
        )
        return False, False
    a_frente = "ESTADO=a_frente" in r.saida
    if a_frente:
        diario.linha("Aviso: o banco está À FRENTE deste código (rollback sob expand/contract).")
    return True, a_frente


def reverter(
    cfg: Config,
    conn: psycopg.Connection,
    diario: Diario,
    imagem_anterior: str,
) -> bool:
    """Auto-reversão: volta a IMAGEM anterior. UMA vez. Nunca o banco.

    O banco fica onde está de propósito. Um downgrade de schema ou um restore de backup
    apagaria os pedidos digitados entre o backup e a falha — e ninguém ficaria sabendo,
    porque o sistema voltaria funcionando. Perder pedido em silêncio é pior que ficar
    fora do ar: fora do ar todo mundo vê.
    """
    diario.linha("REVERTENDO para a imagem anterior: %s", imagem_anterior)
    try:
        escrever_env_tag(cfg.env_tag, imagem_anterior)
    except OSError as e:
        diario.linha("ERRO ao escrever o env.tag na reversão: %s", e)
        return False
    if not subir(cfg, diario).ok:
        return False
    ok, detalhe = esperar_pronto(cfg, diario)
    diario.linha("Sonda após a reversão: %s", detalhe)
    return ok


def processar(cfg: Config, conn: psycopg.Connection, deploy_id: int) -> None:
    """Executa um deploy do começo ao fim. Nunca levanta para fora."""
    linha = conn.execute(
        "SELECT id, acao, versao_nova, status FROM deploys WHERE id = %s", (deploy_id,)
    ).fetchone()
    if linha is None or linha[3] != "solicitado":
        return
    sol = Solicitacao(id=linha[0], acao=linha[1], versao_nova=linha[2])

    diario = Diario(cfg, sol.id)
    status = status_servidor(conn)
    releases = carregar_releases(conn)

    # Override de piso: some ao ser lido. Uma flag que fica no disco é um piso que não
    # existe — e o piso é o que impede um RCE efêmero de virar acesso permanente via
    # downgrade para uma versão vulnerável conhecida.
    permitir_downgrade = cfg.flag_downgrade.exists()
    if permitir_downgrade and not DRY_RUN:
        try:
            cfg.flag_downgrade.unlink()
        except OSError:
            permitir_downgrade = False

    plano = decidir_plano(sol, releases, status.get("versao_minima"), permitir_downgrade)
    if not plano.ok or plano.release is None:
        diario.linha("RECUSADO: %s", plano.motivo)
        conn.execute(
            "UPDATE deploys SET status = 'recusado', concluido_em = now(), log = %s "
            "WHERE id = %s AND status = 'solicitado'",
            (diario.texto(), sol.id),
        )
        emitir_evento(conn, "deploy.falhou", {"id": sol.id, "status": "recusado"})
        return

    # Só agora reivindica. O UPDATE condicional é a trava contra dois agentes (não deveria
    # haver, mas o advisory lock pode ter acabado de trocar de dono num restart).
    tomado = conn.execute(
        "UPDATE deploys SET status = 'executando', iniciado_em = now(), heartbeat_em = now() "
        "WHERE id = %s AND status = 'solicitado' RETURNING id",
        (sol.id,),
    ).fetchone()
    if tomado is None:
        return

    emitir_evento(conn, "deploy.em_andamento", {"id": sol.id, "versao": sol.versao_nova})
    inicio = time.monotonic()
    release = plano.release
    imagem_atual = status.get("imagem_atual")

    with Pulso(cfg, sol.id, diario):
        resultado, imagem_nova, a_frente = _executar_deploy(
            cfg, conn, sol, release, imagem_atual, diario
        )

    duracao = int(time.monotonic() - inicio)

    if resultado == "sucesso":
        conn.execute(
            "UPDATE deploys SET status = 'sucesso', concluido_em = now(), duracao_seg = %s, "
            "imagem_digest = %s, origem = %s, alembic_head = %s, rollback_seguro = %s, "
            "versao_anterior = %s, log = %s WHERE id = %s",
            (
                duracao,
                imagem_nova,
                release.origem,
                release.alembic_head,
                release.rollback_seguro,
                status.get("versao_atual"),
                diario.texto(),
                sol.id,
            ),
        )
        # O piso NUNCA desce: só sobe quando a versão nova é maior. Um rollback legítimo
        # não pode baixar o piso, senão o override viraria permanente na prática.
        minima = status.get("versao_minima")
        nova_minima = sol.versao_nova
        if minima and validar_tag(minima) and comparar_versoes(nova_minima, minima) < 0:
            nova_minima = minima
        conn.execute(
            "UPDATE agente.servidor_status SET versao_atual = %s, versao_minima = %s, "
            "imagem_atual = %s, imagem_anterior = %s, schema_a_frente = %s, "
            "heartbeat_em = now() WHERE id = 1",
            (
                sol.versao_nova,
                nova_minima,
                imagem_nova,
                imagem_atual,
                a_frente,
            ),
        )
        emitir_evento(
            conn, "deploy.concluido", {"id": sol.id, "versao": sol.versao_nova, "ok": True}
        )
        podar_imagens(cfg, release.origem, [imagem_nova, imagem_atual or ""], diario)
        gravar_log(conn, sol.id, diario)
        return

    conn.execute(
        "UPDATE deploys SET status = %s, concluido_em = now(), duracao_seg = %s, "
        "origem = %s, log = %s WHERE id = %s",
        (resultado, duracao, release.origem, diario.texto(), sol.id),
    )
    emitir_evento(conn, "deploy.falhou", {"id": sol.id, "status": resultado})
    if resultado == "falhou_revertido":
        alertar(
            cfg,
            "Estrela: deploy revertido automaticamente",
            f"O deploy #{sol.id} para {sol.versao_nova} falhou no gate de saúde e o "
            f"servidor voltou para a versão anterior. O sistema está no ar. Veja a aba "
            f"/deploy.",
            prioridade="high",
        )
    else:
        alertar(
            cfg,
            "Estrela: DEPLOY FALHOU — sistema possivelmente fora do ar",
            f"O deploy #{sol.id} para {sol.versao_nova} falhou. Veja o deploy #{sol.id} "
            f"em /deploy ou entre no servidor pelo Tailscale.",
            prioridade="urgent",
        )


def _executar_deploy(
    cfg: Config,
    conn: psycopg.Connection,
    sol: Solicitacao,
    release: Release,
    imagem_atual: str | None,
    diario: Diario,
) -> tuple[str, str, bool]:
    """A sequência, na ordem. :returns: (status, imagem_nova, schema_a_frente)"""
    diario.linha("Deploy #%s: %s -> %s", sol.id, sol.acao, sol.versao_nova)
    diario.linha("Release encontrada na allowlist: origem=%s", release.origem)

    # 1-2. cosign é a ÚNICA fonte do digest.
    if not tem_internet():
        diario.linha("ERRO: sem saída para a internet; não é possível verificar nem baixar.")
        return "falha", "", False

    digest = digest_por_cosign(cfg, release.origem, sol.versao_nova, diario)
    if digest is None:
        return "falha", "", False

    # Defesa em profundidade: se a allowlist anotou um digest, ele tem que bater.
    if release.imagem_digest and release.imagem_digest != digest:
        diario.linha(
            "ERRO: o digest assinado não bate com o registrado na allowlist. "
            "A tag foi reapontada para outra imagem depois do cadastro. Abortando."
        )
        return "falha", "", False

    imagem = ref_imagem(release.origem, digest)

    # Disco antes do pull: acabar o espaço no meio do pull deixa camada pela metade e o
    # backup (que vem depois) também morreria.
    try:
        uso = shutil.disk_usage(cfg.disco_path)
        if not disco_suficiente(uso.free, uso.total, cfg.disco_min_bytes, cfg.disco_min_pct):
            diario.linha(
                "ERRO: espaço insuficiente (%.1f GB livres). Abortando antes do pull.",
                uso.free / 1024**3,
            )
            alertar(cfg, "Estrela: deploy abortado por falta de disco", diario.texto()[-500:])
            return "falha", "", False
    except OSError:
        diario.linha("Aviso: não foi possível medir o disco.")

    # 3. Pull por DIGEST, nunca por tag: tag é ponteiro móvel e pode ser reapontada
    # depois da verificação (TOCTOU). O digest é o conteúdo.
    diario.linha("Baixando a imagem por digest...")
    r = executar(["docker", "pull", imagem], timeout=TIMEOUT_PULL, muta=True)
    if not r.ok:
        diario.bloco("docker pull FALHOU", r.tudo)
        return "falha", "", False

    # 4. Labels — falha fechado.
    labels = ler_labels(imagem, diario)
    if labels is None:
        return "falha", "", False
    versao_label = labels.get("org.opencontainers.image.version", "")
    if versao_label:
        diario.linha(
            "Imagem declara versão %s (revision %s).",
            versao_label,
            labels.get("org.opencontainers.image.revision", "?")[:7],
        )

    # 5. Backup — falhou, aborta.
    if not etapa_backup(cfg, diario):
        alertar(
            cfg,
            "Estrela: backup falhou, deploy abortado",
            f"O deploy #{sol.id} parou porque o backup não passou. O sistema continua no "
            "ar na versão atual, mas o backup precisa ser investigado AGORA — sem ele, "
            "não há rede de segurança.",
        )
        return "falha", "", False

    # 6. Pré-flight com a imagem nova, sem trocar o app.
    env_tag_novo = cfg.env_tag.with_suffix(".novo")
    try:
        escrever_env_tag(env_tag_novo, imagem)
    except OSError as e:
        diario.linha("ERRO ao preparar o env.tag: %s", e)
        return "falha", "", False

    ok, a_frente = etapa_preflight(cfg, env_tag_novo, diario)
    if not ok:
        return "falha", "", False

    # 7. Troca a imagem. Daqui em diante o app novo está no caminho.
    diario.linha("Trocando a imagem do app e subindo...")
    try:
        os.replace(env_tag_novo, cfg.env_tag)
    except OSError as e:
        diario.linha("ERRO ao publicar o env.tag: %s", e)
        return "falha", "", False

    subiu = subir(cfg, diario)

    # 8. GATE: /health/ready pelo Caddy.
    pronto, detalhe = (False, "up falhou") if not subiu.ok else esperar_pronto(cfg, diario)
    diario.linha("Gate (%s): %s", cfg.saude_url, detalhe)
    if pronto:
        diario.linha("Deploy concluído com sucesso em %s.", sol.versao_nova)
        return "sucesso", imagem, a_frente

    diario.linha("O sistema não ficou pronto após a troca. Iniciando reversão automática.")
    if not imagem_atual:
        diario.linha(
            "SEM IMAGEM ANTERIOR REGISTRADA: não há para onde reverter. "
            "Intervenção manual necessária."
        )
        return "falha", imagem, a_frente

    if reverter(cfg, conn, diario, imagem_atual):
        diario.linha("Reversão concluída: o sistema está no ar na versão anterior.")
        return "falhou_revertido", imagem, a_frente

    diario.linha("A REVERSÃO TAMBÉM FALHOU. Intervenção manual necessária no servidor.")
    return "falha", imagem, a_frente


# ===========================================================================
# Reconciliação e laço principal
# ===========================================================================


def reconciliar(cfg: Config, conn: psycopg.Connection) -> None:
    """Limpa deploys órfãos ao subir.

    O advisory lock garante instância única. Logo, se existe um `executando` no banco no
    instante em que este processo acabou de pegar o lock, o agente que o escreveu não
    existe mais — o deploy é órfão POR DEFINIÇÃO, não por heurística de tempo. Não há por
    que esperar 30 minutos de timeout: esperar só mantém o sistema quebrado por mais 30
    minutos enquanto a tela mostra uma barrinha de progresso mentirosa.
    """
    orfaos = conn.execute(
        "SELECT id, versao_nova FROM deploys WHERE status = 'executando'"
    ).fetchall()
    if not orfaos:
        return

    for deploy_id, versao in orfaos:
        log.warning("Deploy órfão #%s (%s) encontrado no boot.", deploy_id, versao)
        conn.execute(
            "UPDATE deploys SET status = 'falha', concluido_em = now(), "
            "log = coalesce(log, '') || %s WHERE id = %s",
            (
                "\nO agente reiniciou no meio deste deploy. Como só existe um agente por "
                "vez, este deploy foi dado como interrompido e marcado como falha.",
                deploy_id,
            ),
        )
        emitir_evento(conn, "deploy.falhou", {"id": deploy_id, "status": "falha"})

    pronto, detalhe = sondar_pronto(cfg)
    if pronto:
        log.info("Sistema saudável apesar do deploy órfão; nada a reverter.")
        return

    status = status_servidor(conn)
    anterior = status.get("imagem_anterior")
    alertar(
        cfg,
        "Estrela: agente reiniciou no meio de um deploy",
        f"Sistema não responde ao gate ({detalhe}). "
        + (
            "Tentando reverter para a imagem anterior."
            if anterior
            else "SEM imagem anterior para reverter."
        ),
        prioridade="urgent",
    )
    if not anterior:
        log.error("Sistema não saudável e sem imagem anterior. Intervenção manual.")
        return

    diario = Diario(cfg, orfaos[0][0])
    diario.linha("Reconciliação no boot: sistema fora do ar (%s).", detalhe)
    if reverter(cfg, conn, diario, anterior):
        diario.linha("Revertido para a imagem anterior com sucesso.")
    else:
        diario.linha("A reversão no boot falhou. Intervenção manual necessária.")
    gravar_log(conn, orfaos[0][0], diario)


def pendentes(conn: psycopg.Connection) -> list[int]:
    linhas = conn.execute(
        "SELECT id FROM deploys WHERE status = 'solicitado' ORDER BY id"
    ).fetchall()
    return [ln[0] for ln in linhas]


def _sinal(_s: int, _f: FrameType | None) -> None:
    log.info("Sinal recebido; encerrando após a etapa atual.")
    PARAR.set()


def laco(cfg: Config, uma_vez: bool) -> None:
    while not PARAR.is_set():
        try:
            with conectar(cfg) as conn:
                travado = conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_AGENTE,)).fetchone()
                if not travado or not travado[0]:
                    # Não é erro fatal: pode ser um restart em que a sessão antiga ainda
                    # não caiu. Sair aqui com Restart=always seria um crashloop — e o
                    # agente sumiria justamente quando alguém precisa dele.
                    log.warning(
                        "Outro agente já detém o lock. Nova tentativa em %ss.", ESPERA_ERRO_SEG
                    )
                    PARAR.wait(ESPERA_ERRO_SEG)
                    continue

                log.info("Agente ativo. Escutando o canal %s.", CANAL_DEPLOY)
                reconciliar(cfg, conn)
                # Identificador fixo, não interpolação de entrada do usuário.
                conn.execute(f"LISTEN {CANAL_DEPLOY}")
                bater_status(cfg, conn)

                while not PARAR.is_set():
                    for deploy_id in pendentes(conn):
                        processar(cfg, conn, deploy_id)
                    if uma_vez:
                        return
                    # A campainha vem com payload VAZIO de propósito: qualquer coisa que
                    # viesse dentro do NOTIFY seria entrada não autenticada. O agente
                    # re-lê e revalida tudo do banco. O timeout de 60s é, de graça, o
                    # fallback de polling para o caso de a notificação se perder.
                    try:
                        for _ in conn.notifies(timeout=ESPERA_NOTIFY_SEG, stop_after=1):
                            pass
                    except TypeError:  # psycopg antigo sem timeout/stop_after
                        PARAR.wait(ESPERA_NOTIFY_SEG)
                    bater_status(cfg, conn)
        except psycopg.Error:
            log.warning(
                "Banco indisponível; nova tentativa em %ss.", ESPERA_ERRO_SEG, exc_info=True
            )
            PARAR.wait(ESPERA_ERRO_SEG)
        except Exception:
            # Rede de segurança final: NADA justifica o agente morrer.
            log.exception("Erro inesperado no laço; nova tentativa em %ss.", ESPERA_ERRO_SEG)
            PARAR.wait(ESPERA_ERRO_SEG)


def main(argv: Sequence[str] | None = None) -> int:
    global DRY_RUN
    p = argparse.ArgumentParser(description="Agente de deploy do Estrela Gestão.")
    p.add_argument("--dry-run", action="store_true", help="Imprime o argv que rodaria.")
    p.add_argument("--once", action="store_true", help="Processa a fila uma vez e sai.")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv)
    DRY_RUN = args.dry_run

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    signal.signal(signal.SIGTERM, _sinal)
    signal.signal(signal.SIGINT, _sinal)

    cfg = Config.do_ambiente()
    if not cfg.dsn:
        log.error("ESTRELA_DSN não configurado (ver /etc/estrela-agente/agente.env).")
        return 2
    if DRY_RUN:
        log.info("MODO DRY-RUN: nenhum comando que altera o sistema será executado.")

    laco(cfg, uma_vez=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
