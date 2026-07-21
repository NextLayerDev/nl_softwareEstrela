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

DESCOBERTA DE RELEASES E AUTO-ATUALIZAÇÃO
-----------------------------------------
O agente consulta a API pública do GitHub para saber que releases existem
(`sincronizar_releases`) e, se `ESTRELA_AUTO_UPDATE` estiver ligado, ele mesmo enfileira a
atualização (`auto_atualizar`). Duas regras dão o tom:

* A allowlist continua sendo a fronteira, e o cosign continua sendo o único jeito de
  entrar nela. O GitHub só diz "existe uma tag chamada v0.1.4"; quem decide que aquilo é
  uma imagem legítima é `cosign verify --key /etc/estrela-agente/cosign.pub`. Uma conta do
  GitHub comprometida consegue criar uma Release; não consegue assinar a imagem.
* Auto-update só ANDA PARA FRENTE e só FORA DO EXPEDIENTE. Rollback continua 100% manual:
  voltar versão é decisão de gente, porque o banco não volta junto.
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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from datetime import time as hora_do_dia
from pathlib import Path
from types import FrameType
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
# `buildx imagetools inspect` baixa só o manifesto e o blob de config (alguns KB), nunca
# as layers. É por isso que dá para ler os labels de uma imagem que ainda não veio.
TIMEOUT_INSPECT = 120
TIMEOUT_GITHUB = 10
# As fotos são bytea; um rewrite de tabela leva minutos de verdade.
TIMEOUT_PREFLIGHT = 1800
TIMEOUT_UP = 600
TIMEOUT_GATE_SEG = 180

# Folga mínima de janela para ENFILEIRAR um auto-deploy: a soma dos timeouts do pior caso
# (pull + backup + pré-flight + up + gate) mais uma reversão completa (up + gate). Sem
# exigir isto, um deploy começado às 07:59 estaria trocando o sistema às 09:30 — com a
# empresa vendendo, que é exatamente o que a janela existe para evitar.
MARGEM_JANELA_SEG = (
    TIMEOUT_PULL + TIMEOUT_BACKUP + TIMEOUT_PREFLIGHT + TIMEOUT_UP + TIMEOUT_GATE_SEG
) + (TIMEOUT_UP + TIMEOUT_GATE_SEG)

PARAR = threading.Event()
DRY_RUN = False

# --- Descoberta de releases ------------------------------------------------
SYNC_INTERVALO_SEG = 300  # não bater na API do GitHub a cada volta de 60s
SYNC_BACKOFF_MAX_SEG = 3600
SYNC_MAX_RELEASES = 20  # quantas releases pedir por consulta
# Teto de tags NOVAS verificadas por ciclo: cada uma custa um `cosign verify` (rede) e um
# `imagetools inspect`. Sem teto, um repositório com 30 releases inéditas prenderia o laço
# por minutos — e o laço é quem processa os deploys pedidos na tela.
SYNC_MAX_NOVAS_POR_CICLO = 3
SYNC_TETO_RESPOSTA = 2 * 1024 * 1024  # a resposta do GitHub é dado hostil até prova

# Labels OCI de onde saem alembic_head/rollback_seguro. Hoje o Dockerfile ainda NÃO os
# emite: nesse caso as colunas ficam NULL, e NULL já é tratado como "arriscada" pela aba
# /deploy. O agente não inventa valor — dizer "rollback seguro" sem saber é pior do que
# admitir que não sabe.
LABELS_ALEMBIC = ("br.com.estrela.alembic.head", "br.com.estrela.alembic-head")
LABELS_ROLLBACK = ("br.com.estrela.rollback.seguro", "br.com.estrela.rollback-seguro")
LABEL_REVISION = "org.opencontainers.image.revision"

# --- Auto-atualização ------------------------------------------------------
# Anti-loop. Ver `escolher_alvo`: uma versão que já falhou (ou que alguém cancelou) não é
# tentada de novo antes de AUTO_BACKOFF_SEG, e depois de AUTO_TENTATIVAS desfechos ruins
# ela é abandonada de vez pelo automático — só na mão.
AUTO_TENTATIVAS = 2
AUTO_BACKOFF_SEG = 6 * 3600
# Desfechos que contam como tentativa queimada. `cancelado` entra de propósito: cancelar é
# um humano dizendo "esta versão não, agora não" — se o agente reenfileirasse 60 segundos
# depois, o humano perderia a briga contra o próprio servidor.
STATUS_RUINS = ("falha", "falhou_revertido", "recusado", "cancelado")

_RE_REPO_GITHUB = re.compile(
    r"\A[A-Za-z0-9][A-Za-z0-9._\-]{0,38}/[A-Za-z0-9][A-Za-z0-9._\-]{0,99}\Z"
)


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


# --- Janela de manutenção --------------------------------------------------
# O mini PC atende 10 terminais e 80-100 pedidos por dia. Um `up -d` derruba o app por
# alguns segundos e o pré-flight de migration pode levar minutos: no meio do expediente
# isso é uma vendedora com o pedido pela metade e o cliente no balcão. Então o automático
# só age quando a loja está fechada. A janela é o COMPLEMENTO do expediente — pensar
# "quando a loja está aberta" é mais fácil de configurar certo do que enumerar madrugadas.


@dataclass(frozen=True)
class Janela:
    """Configuração da janela de manutenção. Pura: dá para testar sem relógio nem banco."""

    inicio_expediente: hora_do_dia = hora_do_dia(8, 0)
    fim_expediente: hora_do_dia = hora_do_dia(19, 0)
    # isoweekday: segunda=1 ... domingo=7. Fim de semana inteiro é janela.
    dias_uteis: frozenset[int] = frozenset({1, 2, 3, 4, 5})
    fuso: str = "America/Sao_Paulo"

    def tz(self) -> Any:
        """O fuso do CLIENTE, não o do servidor.

        O mini PC pode estar em UTC (é o default de muita instalação de Ubuntu), e um
        agente que pensa em UTC acharia que 19:00 de Recife é 22:00 e faria deploy às
        16:00 do horário de quem está vendendo. Se o tzdata não existir na máquina, cai
        para UTC-3 fixo em vez de estourar — errar por uma hora num feriado de horário de
        verão que o Brasil não tem mais é infinitamente melhor do que o agente morrer.
        """
        try:
            return ZoneInfo(self.fuso)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            log.warning("Fuso %r indisponível; usando UTC-3 fixo.", self.fuso)
            return timezone(timedelta(hours=-3))


def _janela_de(cfg: Any) -> Janela:
    """Aceita tanto a `Janela` quanto a `Config` inteira — os testes usam a primeira."""
    return cfg if isinstance(cfg, Janela) else cfg.janela


def _no_fuso(agora: datetime, j: Janela) -> datetime:
    """Datetime ingênuo é tratado como UTC: é o que `datetime.now(UTC)` produz aqui."""
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=UTC)
    return agora.astimezone(j.tz())


def dentro_da_janela(agora: datetime, cfg: Any) -> bool:
    """Dá para mexer no sistema agora? True = fora do expediente (ou fim de semana)."""
    j = _janela_de(cfg)
    local = _no_fuso(agora, j)
    if local.isoweekday() not in j.dias_uteis:
        return True
    t = local.time()
    if j.inicio_expediente <= j.fim_expediente:
        expediente = j.inicio_expediente <= t < j.fim_expediente
    else:
        # Expediente que cruza a meia-noite (turno da noite). Não é o caso da Estrela, mas
        # a configuração aceita e o resultado precisa continuar fazendo sentido.
        expediente = t >= j.inicio_expediente or t < j.fim_expediente
    return not expediente


def proxima_janela(agora: datetime, cfg: Any) -> datetime:
    """Quando é a próxima oportunidade de mexer. É o "agendada para" que a tela mostra.

    Se já estamos na janela, a resposta é "agora". Senão, testa os únicos instantes em que
    a janela pode ABRIR (o fim do expediente e a meia-noite de cada dia) e devolve o
    primeiro que de fato esteja dentro da janela — assim uma configuração esquisita
    (expediente cruzando meia-noite, sexta com o fim de semana emendado) continua dando
    uma resposta correta sem lógica especial para cada caso.
    """
    j = _janela_de(cfg)
    local = _no_fuso(agora, j)
    if dentro_da_janela(local, j):
        return local
    tz = local.tzinfo
    candidatos: list[datetime] = []
    for i in range(9):  # uma semana inteira + folga
        d = (local + timedelta(days=i)).date()
        candidatos.append(datetime.combine(d, j.fim_expediente, tzinfo=tz))
        candidatos.append(datetime.combine(d, hora_do_dia(0, 0), tzinfo=tz))
    for c in sorted(candidatos):
        if c > local and dentro_da_janela(c, j):
            return c
    # Janela impossível (expediente 24h todos os dias). Devolve algo no futuro em vez de
    # levantar: quem chama está no laço, e o laço não pode morrer.
    return local + timedelta(days=1)


def fim_da_janela(agora: datetime, cfg: Any) -> datetime | None:
    """Quando a janela ATUAL fecha (o expediente volta). None = não estamos em janela.

    Fim de semana pode emendar vários dias; procuramos o próximo instante em que a janela
    deixa de valer, minuto a minuto seria caro — então testamos os únicos instantes em que
    ela pode FECHAR: o início do expediente de cada dia.
    """
    j = _janela_de(cfg)
    local = _no_fuso(agora, j)
    if not dentro_da_janela(local, j):
        return None
    tz = local.tzinfo
    for i in range(9):
        d = (local + timedelta(days=i)).date()
        c = datetime.combine(d, j.inicio_expediente, tzinfo=tz)
        if c > local and not dentro_da_janela(c, j):
            return c
    return None  # janela sem fim previsto (nenhum dia útil configurado)


def janela_com_folga(agora: datetime, cfg: Any, margem_seg: int) -> bool:
    """Estamos em janela E ainda cabe um deploy inteiro antes do expediente voltar?

    `dentro_da_janela` sozinha é um booleano ingênuo: às 07:59 de uma terça ela diz "pode",
    o automático enfileira, e o pior caso do deploy (pull 900 + backup 1800 + pré-flight
    1800 + up 600 + gate 180, mais uma reversão de 780) leva ~100 min — ou seja, o sistema
    estaria sendo trocado às 09:30, com a loja vendendo. Que é exatamente o que a janela
    existe para impedir.

    Só vale para ENFILEIRAR o automático. O botão manual não passa por aqui: se o operador
    está com o dedo no gatilho às 10h da manhã, ele sabe o que está fazendo.
    """
    fim = fim_da_janela(agora, cfg)
    if fim is None:
        return False
    j = _janela_de(cfg)
    return (fim - _no_fuso(agora, j)).total_seconds() >= margem_seg


# --- Escolha da versão-alvo ------------------------------------------------


@dataclass(frozen=True)
class TentativaAuto:
    """Histórico de desfechos ruins de uma versão (falha, recusa ou cancelamento)."""

    versao: str
    tentativas: int
    ultima_em: datetime | None = None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def escolher_alvo(
    versao_atual: str | None,
    tags: Iterable[str],
    falhas: Mapping[str, TentativaAuto],
    agora: datetime,
    tentativas_max: int = AUTO_TENTATIVAS,
    espera_seg: int = AUTO_BACKOFF_SEG,
) -> tuple[str | None, str]:
    """Qual versão o automático deve instalar? Puro. :returns: (tag, motivo)

    Regras, em ordem:

    1. **Só sobe.** Nunca escolhe versão menor ou igual à que está rodando. Downgrade é
       rollback, e rollback é decisão humana — o banco não volta junto com a imagem.
    2. **Sem base, sem automático.** Se `versao_atual` é desconhecida (agente recém
       instalado, nenhum deploy ainda), ele não age: um agente que não sabe o que está
       rodando não tem o direito de trocar o que está rodando. Um deploy manual estabelece
       a base e libera o automático dali em diante.
    3. **Pré-release nunca.** `v1.2.3-rc1` só na mão.
    4. **ANTI-LOOP.** Uma versão com desfecho ruim (falha, recusa ou cancelamento) fica de
       quarentena por `espera_seg` — cobre a falha transitória (internet caiu no meio do
       pull) sem transformar uma imagem quebrada em derrubada de hora em hora. Ao chegar
       em `tentativas_max` desfechos ruins, o automático desiste DAQUELA versão para
       sempre; a próxima release (ou um clique na tela) resolve. É por isso que o
       automático nunca "bate a cabeça": cada ciclo ou avança, ou espera, ou desiste.
       Sem isso, uma migration ruim viraria backup + downtime a cada 60 segundos.
    """
    if not versao_atual or not validar_tag(versao_atual):
        return None, "versão atual desconhecida — o automático espera um deploy manual"

    candidatos = [
        t for t in tags if validar_tag(t) and "-" not in t and comparar_versoes(t, versao_atual) > 0
    ]
    if not candidatos:
        return None, "nenhuma versão mais nova na allowlist"
    candidatos.sort(key=chave_versao, reverse=True)

    agora = _aware(agora) or datetime.now(UTC)
    motivo = ""
    for tag in candidatos:
        f = falhas.get(tag)
        if f is None:
            return tag, "versão nova, sem tentativa anterior"
        if f.tentativas >= tentativas_max:
            motivo = f"{tag} foi abandonada pelo automático após {f.tentativas} tentativas"
            continue
        ultima = _aware(f.ultima_em)
        if ultima is not None and (agora - ultima).total_seconds() < espera_seg:
            motivo = f"{tag} está em quarentena após uma tentativa malsucedida"
            continue
        return tag, f"nova tentativa de {tag} ({f.tentativas} desfecho(s) ruim(ns) antes)"
    return None, motivo or "nenhuma versão elegível"


# ===========================================================================
# Configuração
# ===========================================================================


def _inteiro(chave: str, padrao: int) -> int:
    """Config malformada NÃO derruba o agente.

    `Config.do_ambiente()` roda antes do laço; um ValueError aqui sai com código != 0 e,
    com `Restart=always`, vira crashloop — o agente sumiria por causa de uma vírgula no
    agente.env, justamente quando alguém precisa dele para consertar algo.
    """
    bruto = os.environ.get(chave, "").strip()
    if not bruto:
        return padrao
    try:
        return int(bruto)
    except ValueError:
        log.warning("%s=%r não é um inteiro; usando %s.", chave, bruto[:40], padrao)
        return padrao


def _booleano(chave: str, padrao: bool) -> bool:
    bruto = os.environ.get(chave, "").strip().lower()
    if not bruto:
        return padrao
    if bruto in ("1", "true", "sim", "yes", "on"):
        return True
    if bruto in ("0", "false", "nao", "não", "no", "off"):
        return False
    log.warning("%s=%r não é booleano; usando %s.", chave, bruto[:20], padrao)
    return padrao


def _hora(chave: str, padrao: hora_do_dia) -> hora_do_dia:
    """ "HH:MM" -> time. Valor inválido cai no padrão, com aviso no journal."""
    bruto = os.environ.get(chave, "").strip()
    if not bruto:
        return padrao
    m = re.fullmatch(r"([01]?[0-9]|2[0-3]):([0-5][0-9])", bruto)
    if not m:
        log.warning("%s=%r não é HH:MM; usando %s.", chave, bruto[:20], padrao)
        return padrao
    return hora_do_dia(int(m.group(1)), int(m.group(2)))


def _dias(chave: str, padrao: frozenset[int]) -> frozenset[int]:
    """ "1,2,3,4,5" -> {1..5} (isoweekday: segunda=1, domingo=7)."""
    bruto = os.environ.get(chave, "").strip()
    if not bruto:
        return padrao
    dias = {int(p) for p in re.findall(r"[1-7]", bruto)}
    if not dias or len(dias) != len([p for p in bruto.split(",") if p.strip()]):
        log.warning("%s=%r não é uma lista de dias 1-7; usando o padrão.", chave, bruto[:30])
        return padrao
    return frozenset(dias)


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
    # --- descoberta de releases ---
    github_repo: str = ""
    github_api: str = "https://api.github.com"
    github_token: str = ""
    imagem_origem: str = ""
    sync_intervalo_seg: int = SYNC_INTERVALO_SEG
    # --- auto-atualização ---
    auto_update: bool = False
    janela: Janela = field(default_factory=Janela)
    auto_tentativas: int = AUTO_TENTATIVAS
    auto_backoff_seg: int = AUTO_BACKOFF_SEG
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
            disco_min_bytes=_inteiro("ESTRELA_DISCO_MIN_BYTES", 3 * 1024**3),
            disco_min_pct=float(g("ESTRELA_DISCO_MIN_PCT", "10") or 10),
            github_repo=g("ESTRELA_GITHUB_REPO", "NextLayerDev/nl_softwareEstrela").strip(),
            github_api=(g("ESTRELA_GITHUB_API", "https://api.github.com").strip().rstrip("/")),
            github_token=g("ESTRELA_GITHUB_TOKEN", "").strip(),
            imagem_origem=g(
                "ESTRELA_IMAGEM_ORIGEM", "ghcr.io/nextlayerdev/nl_softwareestrela"
            ).strip(),
            sync_intervalo_seg=max(60, _inteiro("ESTRELA_SYNC_INTERVALO_SEG", SYNC_INTERVALO_SEG)),
            # Default DESLIGADO no código, LIGADO no agente.env que o instalador gera.
            # Um agente que sobe sem configuração não pode decidir sozinho trocar a versão
            # do sistema que a empresa usa para vender: se o arquivo de config sumiu ou
            # não foi lido, o comportamento seguro é ficar parado esperando um clique.
            auto_update=_booleano("ESTRELA_AUTO_UPDATE", False),
            janela=Janela(
                inicio_expediente=_hora("ESTRELA_EXPEDIENTE_INICIO", hora_do_dia(8, 0)),
                fim_expediente=_hora("ESTRELA_EXPEDIENTE_FIM", hora_do_dia(19, 0)),
                dias_uteis=_dias("ESTRELA_DIAS_UTEIS", frozenset({1, 2, 3, 4, 5})),
                fuso=g("ESTRELA_FUSO", "America/Sao_Paulo").strip() or "America/Sao_Paulo",
            ),
            auto_tentativas=max(1, _inteiro("ESTRELA_AUTO_TENTATIVAS", AUTO_TENTATIVAS)),
            auto_backoff_seg=max(60, _inteiro("ESTRELA_AUTO_BACKOFF_SEG", AUTO_BACKOFF_SEG)),
        )

        # DEAD-MAN'S SWITCH OBRIGATÓRIO: auto-update sem canal de alerta fora de banda é um
        # servidor que se atualiza sozinho às 23h, quebra, e só é descoberto quando a loja
        # abre às 8h — porque o único lugar que contaria a falha é a própria aba /deploy,
        # que está fora do ar junto. Recusamos ligar o automático em vez de fingir que ele
        # está seguro. O manual (botão da aba, com humano olhando) segue funcionando.
        if cfg.auto_update and not cfg.alerta_url:
            log.error(
                "AUTO-UPDATE DESLIGADO: ESTRELA_AUTO_UPDATE=true exige ESTRELA_ALERTA_URL. "
                "Sem canal fora de banda, uma falha de madrugada só aparece quando a loja "
                "abre. Preencha a URL do ntfy em %s e reinicie o agente.",
                cfg.env_file.parent / "agente.env",
            )
            cfg.auto_update = False

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
        # O token do GitHub (opcional) vira header Authorization; um erro de urllib pode
        # ecoar a requisição inteira no log — que a aba /deploy renderiza no navegador.
        if self.github_token:
            valores.append(self.github_token)
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


def _labels_de_config(bruto: object) -> dict[str, str] | None:
    """Extrai `config.Labels` do JSON do `imagetools inspect`, sem confiar no formato.

    Imagem de uma plataforma só devolve o objeto do config; imagem multiplataforma devolve
    um mapa `plataforma -> config`. Aceitamos os dois, e preferimos linux/amd64 (o mini PC).
    """
    if not isinstance(bruto, dict):
        return None
    if "config" in bruto or "Config" in bruto:
        cfg_img = bruto.get("config") or bruto.get("Config") or {}
        if not isinstance(cfg_img, dict):
            return None
        labels = cfg_img.get("Labels") or cfg_img.get("labels") or {}
        if not isinstance(labels, dict):
            return None
        return {str(k): str(v) for k, v in labels.items()}
    ordem = sorted(bruto, key=lambda p: (p != "linux/amd64", str(p)))
    for plataforma in ordem:
        achado = _labels_de_config(bruto[plataforma])
        if achado:
            return achado
    return None


def labels_remotos(imagem: str, diario: Diario) -> dict[str, str] | None:
    """Labels da imagem SEM baixar as layers (só manifesto + blob de config, uns KB).

    Serve à sincronização da allowlist: cadastrar 5 releases não pode significar baixar 5
    imagens de centenas de MB num link doméstico que também precisa atender a loja.

    Diferente de `ler_labels`, aqui `None` NÃO aborta nada: significa "não sei", e não
    saber é gravado como NULL — que a aba /deploy já mostra como versão arriscada. O
    fail-closed continua valendo onde importa, na hora de trocar a imagem de verdade.
    """
    r = executar(
        ["docker", "buildx", "imagetools", "inspect", "--format", "{{json .Image}}", imagem],
        timeout=TIMEOUT_INSPECT,
    )
    if not r.ok:
        diario.linha("Não consegui ler os labels remotos (seguindo com NULL).")
        log.debug("imagetools inspect falhou: %s", r.tudo[:500])
        return None
    try:
        return _labels_de_config(json.loads(r.saida.strip() or "null"))
    except json.JSONDecodeError:
        diario.linha("Labels remotos não vieram em JSON (seguindo com NULL).")
        return None


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
        "SELECT id, acao, versao_nova, status, solicitado_por_id FROM deploys WHERE id = %s",
        (deploy_id,),
    ).fetchone()
    if linha is None or linha[3] != "solicitado":
        return

    # A janela é reconferida AQUI, não só na hora de enfileirar. Cenário real: o automático
    # insere às 07:58, o mini PC reinicia, e a linha 'solicitado' sobrevive (a reconciliação
    # só limpa 'executando'). Sem esta checagem o agente pegaria essa linha às 9h da manhã e
    # trocaria o sistema no meio do expediente — a janela teria sido respeitada no papel e
    # violada na prática.
    #
    # `solicitado_por_id IS NULL` identifica a origem automática. É leitura usada APENAS
    # para ADIAR, nunca para escolher imagem: a regra de nunca confiar em `deploys` para
    # decidir o que executar continua valendo.
    automatico = linha[4] is None
    if automatico and not janela_com_folga(datetime.now(UTC), cfg, MARGEM_JANELA_SEG):
        log.info(
            "Deploy automático #%s adiado: fora da janela (ou sem folga). "
            "Fica 'solicitado' e será executado na próxima janela.",
            deploy_id,
        )
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

    # 4b. A TAG TEM DE CASAR COM O CONTEÚDO. O cosign prova que ALGUM digest foi assinado
    # pela nossa chave; NÃO prova que aquele digest é o da versão pedida. Sem esta
    # comparação, quem controlasse o registry (ou a resolução de tag) poderia apontar
    # `v9.9.9` para o conteúdo de uma release ANTIGA — legitimamente assinada — e o agente
    # aplicaria um downgrade achando que avançou, inclusive furando o piso de versão.
    #
    # O release.yml grava org.opencontainers.image.version com a tag SEM o "v", dentro da
    # imagem que assina — então o label é conteúdo assinado, não metadado do registry.
    esperada = sol.versao_nova.lstrip("v")
    if not versao_label:
        diario.linha(
            "ABORTADO: a imagem não declara org.opencontainers.image.version, então não há "
            "como provar que este conteúdo é a versão %s. Falha fechado.",
            sol.versao_nova,
        )
        return "falha", "", False
    if versao_label.lstrip("v") != esperada:
        diario.linha(
            "ABORTADO: pedi %s, mas a imagem assinada declara ser a versão %s. Tag e "
            "conteúdo não batem — possível retag/downgrade forçado.",
            sol.versao_nova,
            versao_label,
        )
        alertar(
            cfg,
            "Estrela: tag não bate com o conteúdo da imagem",
            f"O deploy #{sol.id} pediu {sol.versao_nova} e recebeu uma imagem que se declara "
            f"{versao_label}. Nada foi trocado. Investigue o registry antes de tentar de novo.",
            prioridade="urgent",
        )
        return "falha", "", False
    diario.linha(
        "Versão confirmada: a imagem assinada declara %s (revision %s).",
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
# Descoberta de releases (allowlist) e auto-atualização
# ===========================================================================


class _FalhaSync(Exception):
    """Sinaliza ao `Manutencao` que vale a pena esperar mais antes de tentar de novo."""


def buscar_releases_github(cfg: Config) -> list[dict[str, Any]] | None:
    """Releases publicadas no GitHub. NUNCA levanta. `None` = não deu para consultar.

    Repositório público: o token é opcional e só serve para o limite de requisições. Sem
    internet é estado NORMAL neste servidor (é um sistema offline-first que fica meses
    sozinho), então "não consegui consultar" é rotina, não incidente.

    O que volta daqui é dado HOSTIL, mesmo vindo do GitHub: só o `tag_name` é aproveitado,
    e só depois de passar pelo `validar_tag`. Nada da resposta vira argumento de comando
    nem escolhe imagem — quem escolhe imagem é o cosign.
    """
    if not _RE_REPO_GITHUB.fullmatch(cfg.github_repo or ""):
        log.warning("ESTRELA_GITHUB_REPO inválido; sincronização desligada.")
        return None
    if not cfg.github_api.startswith("https://"):
        log.warning("ESTRELA_GITHUB_API precisa ser https; sincronização desligada.")
        return None

    url = f"{cfg.github_api}/repos/{cfg.github_repo}/releases?per_page={SYNC_MAX_RELEASES}"
    cabecalhos = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "estrela-agente",
    }
    if cfg.github_token:
        cabecalhos["Authorization"] = f"Bearer {cfg.github_token}"
    try:
        req = urllib.request.Request(url, headers=cabecalhos, method="GET")  # noqa: S310
        with urllib.request.urlopen(req, timeout=TIMEOUT_GITHUB) as r:  # noqa: S310
            bruto = r.read(SYNC_TETO_RESPOSTA)
        dados = json.loads(bruto.decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as e:
        log.debug("Consulta de releases falhou: %s", redigir(e, cfg.segredos))
        return None
    if not isinstance(dados, list):
        return None
    return [d for d in dados if isinstance(d, dict)]


def _publicado_em(release: Mapping[str, Any]) -> datetime | None:
    bruto = release.get("published_at") or release.get("created_at")
    if not isinstance(bruto, str):
        return None
    try:
        return datetime.fromisoformat(bruto.replace("Z", "+00:00"))
    except ValueError:
        return None


def _rollback_do_label(valor: str | None) -> bool | None:
    """NULL quando o label não existe ou não é claramente booleano. Nada de chute."""
    if valor is None:
        return None
    v = valor.strip().lower()
    if v in ("1", "true", "sim", "yes"):
        return True
    if v in ("0", "false", "nao", "não", "no"):
        return False
    return None


def _primeiro_label(labels: Mapping[str, str], chaves: Sequence[str]) -> str | None:
    for c in chaves:
        valor = labels.get(c)
        if valor:
            return valor
    return None


def sincronizar_releases(cfg: Config, conn: psycopg.Connection) -> int:
    """Descobre releases no GitHub e cadastra na allowlist as que o cosign aprovar.

    CONSERTA O BUG DE ORIGEM: nada populava `agente.releases_disponiveis`, então até o
    botão manual da aba /deploy recusava qualquer tag como "fora da allowlist".

    A fronteira de confiança continua exatamente onde estava. O GitHub só sugere nomes de
    tags; a tag só entra na allowlist se `cosign verify --key` aceitar a assinatura da
    imagem correspondente no GHCR. Uma conta do GitHub comprometida cria Releases à
    vontade — sem a chave privada, nenhuma delas atravessa.

    :returns: quantas tags novas entraram (0 também é sucesso).
    """
    if not validar_origem(cfg.imagem_origem):
        log.warning("ESTRELA_IMAGEM_ORIGEM inválida; sincronização desligada.")
        return 0
    if not tem_internet("api.github.com", 443):
        log.debug("Sem internet; sincronização de releases adiada (estado normal).")
        return 0

    dados = buscar_releases_github(cfg)
    if dados is None:
        raise _FalhaSync("não foi possível consultar as releases do GitHub")

    conhecidas = carregar_releases(conn)
    # deploy_id 0: o Diário aqui só existe para reaproveitar o log das funções de cosign e
    # de labels. Ele nunca é gravado em `deploys` — não há deploy nenhum acontecendo.
    diario = Diario(cfg, 0)
    novas = 0
    # Orçamento de VERIFICAÇÕES, não de sucessos: uma release sem assinatura válida também
    # custou um `cosign verify`. Contar só os acertos deixaria o laço preso verificando as
    # mesmas tags recusadas a cada ciclo, e o laço é quem atende o botão da tela.
    orcamento = SYNC_MAX_NOVAS_POR_CICLO
    for item in dados:
        if PARAR.is_set() or orcamento <= 0:
            break
        if item.get("draft") or item.get("prerelease"):
            continue
        tag = item.get("tag_name")
        if not isinstance(tag, str) or not validar_tag(tag):
            continue
        ja = conhecidas.get(tag)
        if ja is not None and ja.imagem_digest:
            # Já verificada uma vez, e o que está gravado é um DIGEST — conteúdo imutável.
            # Reverificar a cada 5 minutos só gastaria rede: o caminho do deploy roda o
            # cosign de novo, na hora, antes de trocar qualquer coisa.
            continue

        orcamento -= 1
        digest = digest_por_cosign(cfg, cfg.imagem_origem, tag, diario)
        if digest is None:
            log.warning("Release %s ignorada: assinatura não confere (ou não existe).", tag)
            continue

        labels = labels_remotos(ref_imagem(cfg.imagem_origem, digest), diario) or {}
        alembic_head = _primeiro_label(labels, LABELS_ALEMBIC)
        rollback_seguro = _rollback_do_label(_primeiro_label(labels, LABELS_ROLLBACK))
        git_sha = labels.get(LABEL_REVISION) or None

        if DRY_RUN:
            print(f"[dry-run] allowlist += {tag} ({digest[:19]})")
            novas += 1
            continue
        try:
            conn.execute(
                # COALESCE do lado do que JÁ ESTÁ GRAVADO: a allowlist é write-once por
                # campo. Se o digest gravado mudasse, uma tag reapontada para outra imagem
                # entraria por aqui com cara de atualização de rotina — exatamente o ataque
                # que o `_executar_deploy` compara depois.
                # O alias `AS r` existe para o DO UPDATE poder falar da linha ANTIGA sem
                # ambiguidade: com a tabela qualificada por schema, escrever
                # `agente.releases_disponiveis.origem` aqui é pedir para o Postgres
                # reclamar de referência à cláusula FROM.
                "INSERT INTO agente.releases_disponiveis AS r "
                "  (tag, origem, imagem_digest, git_sha, alembic_head, rollback_seguro, "
                "   publicado_em) "
                "VALUES (%s, %s, %s, %s, %s, %s, coalesce(%s, now())) "
                "ON CONFLICT (tag) DO UPDATE SET "
                "  origem = coalesce(r.origem, EXCLUDED.origem), "
                "  imagem_digest = coalesce(r.imagem_digest, EXCLUDED.imagem_digest), "
                "  git_sha = coalesce(r.git_sha, EXCLUDED.git_sha), "
                "  alembic_head = coalesce(r.alembic_head, EXCLUDED.alembic_head), "
                "  rollback_seguro = coalesce(r.rollback_seguro, EXCLUDED.rollback_seguro)",
                (
                    tag,
                    cfg.imagem_origem,
                    digest,
                    git_sha,
                    alembic_head,
                    rollback_seguro,
                    _publicado_em(item),
                ),
            )
        except psycopg.Error:
            log.warning("Não foi possível gravar %s na allowlist.", tag, exc_info=True)
            continue
        novas += 1
        log.info("Allowlist: %s cadastrada (digest %s).", tag, digest[:19])
    return novas


def falhas_automaticas(conn: psycopg.Connection) -> dict[str, TentativaAuto]:
    """Desfechos ruins por versão — a memória que impede o loop de retentativa.

    Conta tentativa de QUALQUER origem, não só as do agente. Se uma pessoa tentou a
    v0.1.5 na mão e ela falhou, o automático repetir a mesma coisa dez minutos depois não
    ajuda ninguém.
    """
    linhas = conn.execute(
        "SELECT versao_nova, count(*), max(coalesce(concluido_em, solicitado_em)) "
        "FROM deploys WHERE acao = 'atualizacao' AND status = ANY(%s) "
        "GROUP BY versao_nova",
        (list(STATUS_RUINS),),
    ).fetchall()
    return {
        str(versao): TentativaAuto(str(versao), int(qtd), ultima) for versao, qtd, ultima in linhas
    }


def ha_deploy_em_voo(conn: psycopg.Connection) -> bool:
    linha = conn.execute(
        "SELECT 1 FROM deploys WHERE status IN ('solicitado', 'executando') LIMIT 1"
    ).fetchone()
    return linha is not None


def anotar_disponibilidade(
    conn: psycopg.Connection,
    ativo: bool,
    tag: str | None,
    agendada_para: datetime | None,
) -> None:
    """Publica em `agente.servidor_status` o estado do automático, para a aba /deploy.

    Nomes das colunas (`auto_update_ativo`, `versao_disponivel`, `proxima_janela`) são os
    que o app/services/deploy_service.py consulta — a app é a parte já escrita; quem se
    adapta é o agente, exatamente como já acontece com `publicado_em`/`git_sha`.

    Tolera as colunas não existirem: quem copiou o agente.py novo e não rodou o
    instalar-agente.sh de novo fica com agente novo contra schema velho, e aí a resposta
    certa é reclamar no journal — não parar de fazer deploy.
    """
    try:
        conn.execute(
            "UPDATE agente.servidor_status SET auto_update_ativo = %s, "
            "versao_disponivel = %s, proxima_janela = %s, releases_sync_em = now() "
            "WHERE id = 1",
            (ativo, tag, agendada_para),
        )
    except psycopg.Error:
        log.warning(
            "Não foi possível publicar o estado do auto-update — as colunas novas de "
            "agente.servidor_status podem não existir. Rode deploy/instalar-agente.sh "
            "(ele é idempotente) para criá-las.",
            exc_info=True,
        )


@dataclass(frozen=True)
class ResultadoAuto:
    """O que o automático decidiu neste ciclo. `estado` é o que vai para o journal."""

    estado: str
    tag: str | None = None
    quando: datetime | None = None
    detalhe: str = ""


def auto_atualizar(
    cfg: Config, conn: psycopg.Connection, agora: datetime | None = None
) -> ResultadoAuto:
    """Decide (e enfileira) a atualização automática. NÃO executa deploy: só pede.

    Enfileirar em vez de executar é o ponto do desenho: a linha em `deploys` passa pelo
    MESMO `processar` do botão da tela — mesma allowlist, mesmo cosign, mesmo backup, mesmo
    pré-flight, mesmo gate de saúde, mesma auto-reversão. O automático não ganha nenhum
    atalho; ele só aperta o botão sozinho, e só quando a loja está fechada.
    """
    agora = agora or datetime.now(UTC)
    status = status_servidor(conn)
    releases = carregar_releases(conn)
    falhas = falhas_automaticas(conn)
    alvo, motivo = escolher_alvo(
        status.get("versao_atual"),
        releases.keys(),
        falhas,
        agora,
        tentativas_max=cfg.auto_tentativas,
        espera_seg=cfg.auto_backoff_seg,
    )
    na_janela = dentro_da_janela(agora, cfg)
    # Publica ANTES de qualquer decisão: mesmo com o automático desligado, a tela ganha o
    # "existe a versão X" — e o botão manual passa a ter o que fazer. `proxima_janela` só
    # é preenchida quando o automático está ligado; senão a tela prometeria um horário em
    # que nada vai acontecer.
    quando = None if (na_janela or not cfg.auto_update) else proxima_janela(agora, cfg)
    if cfg.auto_update:
        publicada = alvo
    else:
        # Com o automático desligado, a quarentena não vem ao caso (ela é uma regra do
        # automático). O que a tela precisa mostrar é simplesmente "existe versão nova" —
        # quem decide o que fazer com ela é a pessoa no botão.
        publicada, _ = escolher_alvo(status.get("versao_atual"), releases.keys(), {}, agora)
    anotar_disponibilidade(conn, cfg.auto_update, publicada, quando)

    if not cfg.auto_update:
        return ResultadoAuto("desligado", tag=publicada, detalhe=motivo)
    if alvo is None:
        return ResultadoAuto("sem_novidade", detalhe=motivo)
    if ha_deploy_em_voo(conn):
        return ResultadoAuto(
            "em_voo", tag=alvo, detalhe="já existe um deploy pendente ou em execução"
        )
    if not na_janela:
        return ResultadoAuto("agendado", tag=alvo, quando=quando, detalhe=motivo)
    # Estar na janela não basta: tem de CABER. Às 07:59 a janela ainda vale, mas o pior
    # caso do deploy (~100 min com reversão) terminaria depois das 9h, com a loja aberta.
    # Sem esta folga, a janela viraria teatro — ver janela_com_folga().
    if not janela_com_folga(agora, cfg, MARGEM_JANELA_SEG):
        return ResultadoAuto(
            "agendado",
            tag=alvo,
            quando=proxima_janela(agora, cfg),
            detalhe="a janela atual não tem folga suficiente para um deploy inteiro",
        )

    if DRY_RUN:
        print(f"[dry-run] INSERT deploys (atualizacao, {alvo}, automático)")
        return ResultadoAuto("solicitado", tag=alvo, detalhe="dry-run")

    try:
        linha = conn.execute(
            # solicitado_por_id NULL = originado pelo agente. É o que a tela usa para
            # escrever "automático" em vez de um nome de pessoa.
            "INSERT INTO deploys (acao, status, versao_nova, versao_anterior, "
            "                     solicitado_por_id, solicitado_em) "
            "VALUES ('atualizacao', 'solicitado', %s, %s, NULL, now()) RETURNING id",
            (alvo, status.get("versao_atual")),
        ).fetchone()
    except psycopg.Error:
        log.warning("Não foi possível enfileirar a atualização automática.", exc_info=True)
        return ResultadoAuto("erro", tag=alvo, detalhe="INSERT em deploys falhou")
    deploy_id = int(linha[0]) if linha else 0

    emitir_evento(
        conn,
        "deploy.solicitado",
        {"id": deploy_id, "acao": "atualizacao", "versao": alvo, "automatico": True},
    )
    alertar(
        cfg,
        "Estrela: atualização automática iniciada",
        f"O servidor está aplicando sozinho a versão {alvo} (deploy #{deploy_id}), dentro "
        f"da janela de manutenção. Backup, pré-flight de migration e gate de saúde valem "
        f"igual ao botão manual: se algo falhar, ele volta sozinho para a versão anterior "
        f"e avisa aqui.",
        prioridade="default",
    )
    log.info("Auto-update: deploy #%s enfileirado para %s (%s).", deploy_id, alvo, motivo)
    return ResultadoAuto("solicitado", tag=alvo, detalhe=motivo)


class Manutencao:
    """Tarefas periódicas do laço: sincronizar a allowlist e decidir o auto-update.

    Guarda o instante da última sincronização (relógio MONOTÔNICO — `time.time()` daria
    salto se alguém acertasse a hora do mini PC) e faz backoff exponencial quando a API do
    GitHub não responde. O laço roda a cada 60s; a sincronização, a cada 5 minutos.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._proxima_sync = 0.0
        self._falhas = 0
        self._ultimo_estado: tuple[str, str | None] = ("", None)

    def rodar(self, conn: psycopg.Connection) -> None:
        """Um ciclo. Só deixa passar erro de BANCO, que é assunto do laço (ele reconecta).

        Se houver deploy na fila, a sincronização é adiada: cada tag nova custa um `cosign
        verify` e um `imagetools inspect`, e ninguém quer clicar em "atualizar" e esperar
        minutos porque o agente escolheu conversar com o GitHub primeiro.
        """
        if ha_deploy_em_voo(conn):
            return
        try:
            self._sincronizar(conn)
        except psycopg.Error:
            raise
        except Exception:  # noqa: BLE001 - rede/JSON/docker; nada aqui derruba o agente
            log.warning("Sincronização de releases falhou (seguindo).", exc_info=True)
        try:
            self._auto(conn)
        except psycopg.Error:
            raise
        except Exception:  # noqa: BLE001
            log.warning("Auto-atualização falhou (seguindo).", exc_info=True)

    def _sincronizar(self, conn: psycopg.Connection) -> None:
        agora = time.monotonic()
        if agora < self._proxima_sync:
            return
        try:
            sincronizar_releases(self.cfg, conn)
        except _FalhaSync as e:
            self._falhas += 1
            espera = min(self.cfg.sync_intervalo_seg * (2**self._falhas), SYNC_BACKOFF_MAX_SEG)
            self._proxima_sync = time.monotonic() + espera
            log.debug("Sincronização adiada por %ss (%s).", espera, e)
            return
        self._falhas = 0
        self._proxima_sync = time.monotonic() + self.cfg.sync_intervalo_seg

    def _auto(self, conn: psycopg.Connection) -> None:
        r = auto_atualizar(self.cfg, conn)
        chave = (r.estado, r.tag)
        if chave == self._ultimo_estado:
            return  # o journal não precisa da mesma linha a cada 60 segundos
        self._ultimo_estado = chave
        if r.estado == "agendado" and r.quando is not None:
            log.info(
                "Versão %s disponível; fora da janela de manutenção. Agendada para %s.",
                r.tag,
                r.quando.strftime("%d/%m %H:%M %Z"),
            )
        elif r.estado in ("sem_novidade", "em_voo"):
            log.debug("Auto-update: %s (%s).", r.estado, r.detalhe)


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
    # `imagem_atual` e NÃO `imagem_anterior`: um deploy órfão morreu tentando ir para B,
    # então quem estava no ar (e é boa) é a A registrada em `imagem_atual`. A
    # `imagem_anterior` é uma versão a MAIS para trás — reverter para ela jogaria o
    # cliente dois passos atrás, possivelmente cruzando outra migration. É a mesma
    # semântica que _executar_deploy usa quando reverte no gate.
    alvo = status.get("imagem_atual")
    alertar(
        cfg,
        "Estrela: agente reiniciou no meio de um deploy",
        f"Sistema não responde ao gate ({detalhe}). "
        + (
            "Tentando voltar para a última imagem boa conhecida."
            if alvo
            else "SEM imagem conhecida para voltar."
        ),
        prioridade="urgent",
    )
    if not alvo:
        log.error("Sistema não saudável e sem imagem conhecida. Intervenção manual.")
        return

    diario = Diario(cfg, orfaos[0][0])
    diario.linha("Reconciliação no boot: sistema fora do ar (%s).", detalhe)
    if reverter(cfg, conn, diario, alvo):
        diario.linha("Voltou para a última imagem boa (%s) com sucesso.", alvo)
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
    # Vive FORA do while: o intervalo de sincronização e o backoff da API do GitHub não
    # podem ser zerados por uma reconexão ao banco, senão uma piscada do Postgres viraria
    # uma rajada de requisições ao GitHub.
    manutencao = Manutencao(cfg)
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
                    # ANTES de olhar a fila, de propósito: se o automático decidir que está
                    # na hora, a linha que ele insere é vista pelo `pendentes` logo abaixo
                    # e o deploy começa nesta mesma volta, sem esperar mais 60 segundos.
                    manutencao.rodar(conn)
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

    j = cfg.janela
    log.info(
        "Auto-update: %s | expediente %s-%s nos dias %s (%s) | releases de %s a cada %ss.",
        "LIGADO" if cfg.auto_update else "desligado",
        j.inicio_expediente.strftime("%H:%M"),
        j.fim_expediente.strftime("%H:%M"),
        ",".join(str(d) for d in sorted(j.dias_uteis)),
        j.fuso,
        cfg.github_repo or "(não configurado)",
        cfg.sync_intervalo_seg,
    )

    laco(cfg, uma_vez=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
