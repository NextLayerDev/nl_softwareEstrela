"""Testes do agente de deploy (scripts/agente/agente.py).

O agente roda no HOST, fora do Docker, e NÃO importa `app.*` — ele precisa funcionar
exatamente quando a aplicação está quebrada. Por isso ele não é um pacote importável: aqui
o módulo é carregado por CAMINHO, com importlib.

Tudo o que se testa aqui é PURO: janela de manutenção, escolha da versão-alvo e os
validadores de entrada. Sem rede, sem docker, sem banco — de propósito. O que precisa de
`docker` ou de Postgres é justamente o que não dá para provar num runner de CI, e um teste
que finge testar isso só transmite confiança falsa.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest

_CAMINHO = Path(__file__).resolve().parents[1] / "scripts" / "agente" / "agente.py"


def _carregar() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agente_deploy", _CAMINHO)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    # Registrar ANTES do exec: o `@dataclass` procura o módulo em sys.modules durante a
    # criação da classe (para resolver as anotações) e estoura com AttributeError se não
    # encontrar. Um módulo carregado por caminho não entra sozinho no registro.
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


agente = _carregar()

# Fuso do cliente. Um datetime "às 14:00" só quer dizer alguma coisa com fuso junto — e o
# servidor pode perfeitamente estar em UTC.
SP = timezone(timedelta(hours=-3))
JANELA = agente.Janela()  # 08:00-19:00, segunda a sexta, America/Sao_Paulo


def em_sp(ano: int, mes: int, dia: int, hh: int, mm: int = 0) -> datetime:
    return datetime(ano, mes, dia, hh, mm, tzinfo=SP)


# ===========================================================================
# Janela de manutenção
# ===========================================================================

# 2026-07-20 é uma segunda-feira; 2026-07-25 é sábado e 2026-07-26 é domingo.


@pytest.mark.parametrize(
    ("quando", "esperado"),
    [
        (em_sp(2026, 7, 20, 3, 0), True),  # madrugada de segunda: pode
        (em_sp(2026, 7, 20, 7, 59), True),  # 1 minuto antes de abrir a loja
        (em_sp(2026, 7, 20, 8, 0), False),  # abriu: acabou a janela
        (em_sp(2026, 7, 20, 12, 0), False),  # meio do expediente
        (em_sp(2026, 7, 20, 18, 59), False),  # ainda tem gente vendendo
        (em_sp(2026, 7, 20, 19, 0), True),  # fechou: janela de novo
        (em_sp(2026, 7, 20, 23, 59), True),
        (em_sp(2026, 7, 24, 12, 0), False),  # sexta ao meio-dia: expediente
        (em_sp(2026, 7, 25, 12, 0), True),  # sábado inteiro é janela
        (em_sp(2026, 7, 26, 12, 0), True),  # domingo idem
    ],
)
def test_dentro_da_janela_nas_bordas(quando: datetime, esperado: bool) -> None:
    assert agente.dentro_da_janela(quando, JANELA) is esperado


def test_janela_respeita_o_fuso_do_cliente_e_nao_o_do_servidor() -> None:
    """14:00 em São Paulo é 17:00 UTC. Um agente que pensasse em UTC faria deploy no meio
    do expediente achando que já era noite — este é o erro que o zoneinfo evita."""
    meio_da_tarde_utc = datetime(2026, 7, 20, 17, 0, tzinfo=UTC)
    assert agente.dentro_da_janela(meio_da_tarde_utc, JANELA) is False

    # 23:00 UTC = 20:00 em São Paulo: loja fechada, pode mexer.
    noite_utc = datetime(2026, 7, 20, 23, 0, tzinfo=UTC)
    assert agente.dentro_da_janela(noite_utc, JANELA) is True


def test_datetime_ingenuo_e_tratado_como_utc() -> None:
    ingenuo = datetime(2026, 7, 20, 17, 0)  # noqa: DTZ001 - é exatamente o caso testado
    assert agente.dentro_da_janela(ingenuo, JANELA) is False


def test_dias_uteis_configuraveis() -> None:
    """Loja que abre sábado: sábado deixa de ser janela."""
    j = agente.Janela(dias_uteis=frozenset({1, 2, 3, 4, 5, 6}))
    assert agente.dentro_da_janela(em_sp(2026, 7, 25, 12, 0), j) is False
    assert agente.dentro_da_janela(em_sp(2026, 7, 26, 12, 0), j) is True


def test_proxima_janela_hoje_a_noite() -> None:
    agendada = agente.proxima_janela(em_sp(2026, 7, 20, 14, 30), JANELA)
    assert (agendada.year, agendada.month, agendada.day) == (2026, 7, 20)
    assert agendada.hour == 19 and agendada.minute == 0


def test_proxima_janela_e_agora_quando_ja_esta_na_janela() -> None:
    madrugada = em_sp(2026, 7, 20, 2, 0)
    assert agente.proxima_janela(madrugada, JANELA) == madrugada


def test_proxima_janela_no_ultimo_minuto_do_expediente() -> None:
    agendada = agente.proxima_janela(em_sp(2026, 7, 20, 18, 59), JANELA)
    assert agendada == em_sp(2026, 7, 20, 19, 0)


def test_proxima_janela_de_sexta_a_noite_e_a_propria_sexta() -> None:
    """Sexta 18:00 -> abre às 19:00 da própria sexta, e não segunda."""
    agendada = agente.proxima_janela(em_sp(2026, 7, 24, 18, 0), JANELA)
    assert agendada == em_sp(2026, 7, 24, 19, 0)


def test_proxima_janela_com_expediente_24h_nao_estoura() -> None:
    """Configuração impossível (expediente o dia todo): responde algo no futuro em vez de
    levantar. Quem chama está no laço, e o laço não pode morrer."""
    j = agente.Janela(
        inicio_expediente=time(0, 0),
        fim_expediente=time(23, 59),
        dias_uteis=frozenset({1, 2, 3, 4, 5, 6, 7}),
    )
    agora = em_sp(2026, 7, 20, 12, 0)
    assert agente.proxima_janela(agora, j) > agora


def test_janela_cai_para_utc_menos_3_quando_o_fuso_nao_existe() -> None:
    j = agente.Janela(fuso="Marte/Olympus_Mons")
    # Não levanta e continua respondendo coerentemente (UTC-3 é o mesmo offset de SP).
    assert agente.dentro_da_janela(datetime(2026, 7, 20, 17, 0, tzinfo=UTC), j) is False


# ===========================================================================
# Escolha da versão-alvo (auto-update)
# ===========================================================================

AGORA = datetime(2026, 7, 20, 20, 0, tzinfo=UTC)
TAGS = ["v0.1.2", "v0.1.3", "v0.1.4", "v0.2.0"]


def falha(versao: str, tentativas: int, horas_atras: float) -> agente.TentativaAuto:
    return agente.TentativaAuto(versao, tentativas, AGORA - timedelta(hours=horas_atras))


def test_escolhe_a_maior_versao_acima_da_atual() -> None:
    alvo, _ = agente.escolher_alvo("v0.1.3", TAGS, {}, AGORA)
    assert alvo == "v0.2.0"


def test_nunca_escolhe_versao_menor_ou_igual() -> None:
    """Auto-update SÓ ANDA PARA FRENTE. Downgrade é rollback, e rollback é humano."""
    alvo, motivo = agente.escolher_alvo("v0.2.0", TAGS, {}, AGORA)
    assert alvo is None
    assert "nenhuma versão mais nova" in motivo


def test_sem_versao_atual_conhecida_nao_age() -> None:
    """Agente que não sabe o que está rodando não troca o que está rodando."""
    for atual in (None, "", "desconhecida"):
        alvo, motivo = agente.escolher_alvo(atual, TAGS, {}, AGORA)
        assert alvo is None
        assert "desconhecida" in motivo


def test_ignora_pre_release() -> None:
    alvo, _ = agente.escolher_alvo("v0.1.3", ["v0.1.4-rc1", "v0.1.4"], {}, AGORA)
    assert alvo == "v0.1.4"
    alvo, _ = agente.escolher_alvo("v0.1.3", ["v0.9.0-rc1"], {}, AGORA)
    assert alvo is None


def test_ignora_tag_hostil_na_allowlist() -> None:
    """A allowlist é banco, e banco também pode ter lixo dentro."""
    alvo, _ = agente.escolher_alvo(
        "v0.1.3", ["v0.1.4\nrm -rf /", "latest", "v9.9.9; reboot", "v0.1.4"], {}, AGORA
    )
    assert alvo == "v0.1.4"


def test_versao_em_quarentena_e_pulada_e_a_anterior_assume() -> None:
    """v0.2.0 falhou há 1h (quarentena de 6h) -> tenta a v0.1.4, que é menor que ela mas
    ainda maior que a atual. O sistema avança sem insistir no que acabou de quebrar."""
    alvo, _ = agente.escolher_alvo("v0.1.3", TAGS, {"v0.2.0": falha("v0.2.0", 1, 1)}, AGORA)
    assert alvo == "v0.1.4"


def test_quarentena_expira_e_a_versao_e_tentada_de_novo() -> None:
    """Falha transitória (internet caiu no meio do pull) não pode queimar a versão para
    sempre — mas também não pode virar retentativa a cada 60 segundos."""
    alvo, motivo = agente.escolher_alvo("v0.1.3", TAGS, {"v0.2.0": falha("v0.2.0", 1, 7)}, AGORA)
    assert alvo == "v0.2.0"
    assert "nova tentativa" in motivo


def test_versao_abandonada_apos_o_limite_de_tentativas() -> None:
    """Duas tentativas ruins: o automático desiste DAQUELA versão, mesmo com a quarentena
    já vencida. É o que impede o agente de derrubar o cliente de hora em hora."""
    falhas = {"v0.2.0": falha("v0.2.0", 2, 48)}
    alvo, _ = agente.escolher_alvo("v0.1.3", TAGS, falhas, AGORA)
    assert alvo == "v0.1.4"

    falhas["v0.1.4"] = falha("v0.1.4", 2, 48)
    alvo, motivo = agente.escolher_alvo("v0.1.3", TAGS, falhas, AGORA)
    assert alvo is None
    assert "abandonada" in motivo


def test_cancelamento_conta_como_tentativa() -> None:
    """`cancelado` entra em STATUS_RUINS: cancelar é um humano dizendo "agora não". Se o
    agente reenfileirasse 60s depois, o humano perderia a briga contra o servidor."""
    assert "cancelado" in agente.STATUS_RUINS


def test_tentativa_sem_data_ainda_conta_para_o_limite() -> None:
    falhas = {"v0.2.0": agente.TentativaAuto("v0.2.0", 5, None)}
    alvo, _ = agente.escolher_alvo("v0.1.3", TAGS, falhas, AGORA)
    assert alvo == "v0.1.4"


def test_allowlist_vazia_nao_escolhe_nada() -> None:
    alvo, _ = agente.escolher_alvo("v0.1.3", [], {}, AGORA)
    assert alvo is None


# ===========================================================================
# Validadores — a fronteira entre um <input> e um argv rodando no host
# ===========================================================================


@pytest.mark.parametrize(
    "tag",
    [
        "v1.2.3",
        "v0.0.0",
        "v0.1.3-rc1",
        "v1000.1000.1000",
    ],
)
def test_validar_tag_aceita_semver(tag: str) -> None:
    assert agente.validar_tag(tag) is True


@pytest.mark.parametrize(
    "tag",
    [
        "v1.2.3\nrm -rf /",  # \Z e não $: "$" casaria antes do \n final
        "v1.2.3\n",
        "latest",  # tag MÓVEL: destrói saber o que está rodando
        "v1.2",
        "1.2.3",
        "v1.2.3 && reboot",
        "v1.2.3;id",
        "v1.2.3/../../etc",
        "v1.2.3`id`",
        "v1.2.3$(id)",
        "--entrypoint",
        "v" + "9" * 40,
        "",
        " v1.2.3",
        None,
        123,
        ["v1.2.3"],
    ],
)
def test_validar_tag_recusa_entrada_hostil(tag: object) -> None:
    assert agente.validar_tag(tag) is False


@pytest.mark.parametrize(
    "origem",
    ["ghcr.io/nextlayerdev/nl_softwareestrela", "docker.io/lib/x", "registry.local/a.b-c"],
)
def test_validar_origem_aceita_repositorio_oci(origem: str) -> None:
    assert agente.validar_origem(origem) is True


@pytest.mark.parametrize(
    "origem",
    [
        "ghcr.io/NextLayerDev/App",  # GHCR exige minúsculo
        "ghcr.io/x -v /:/host",
        "ghcr.io/x\nrm -rf /",
        "../etc/passwd",
        "",
        "a" * 201,
        None,
        42,
    ],
)
def test_validar_origem_recusa_entrada_hostil(origem: object) -> None:
    assert agente.validar_origem(origem) is False


def test_validar_digest() -> None:
    bom = "sha256:" + "a" * 64
    assert agente.validar_digest(bom) is True
    for ruim in (
        "sha256:" + "a" * 63,
        "sha256:" + "A" * 64,  # maiúscula não é hex do digest OCI
        "sha512:" + "a" * 64,
        "sha256:" + "a" * 64 + "\n",
        "",
        None,
        object(),
    ):
        assert agente.validar_digest(ruim) is False


def test_comparacao_de_versoes_ordena_semver_e_nao_texto() -> None:
    """Comparação textual diria que v0.1.10 < v0.1.9 — e o servidor "atualizaria" para
    trás no dia em que a numeração passasse de 9."""
    assert agente.comparar_versoes("v0.1.10", "v0.1.9") == 1
    assert agente.comparar_versoes("v0.2.0", "v0.10.0") == -1
    assert agente.comparar_versoes("v1.0.0-rc1", "v1.0.0") == -1
    assert agente.comparar_versoes("v1.0.0", "v1.0.0") == 0


def test_redator_tira_segredo_do_log_que_vai_para_o_navegador() -> None:
    # Isca DE MENTIRA — e é o ponto do teste: estas strings existem para serem apagadas
    # pelo redator. O gitleaks acusou (generic-api-key) e está certo em achar que PARECE
    # um segredo: é justamente por parecer que ela serve aqui. O `gitleaks:allow` abaixo
    # tem de ficar na MESMA linha do achado — comentário na linha de cima não conta.
    fake = "senha-secreta"  # gitleaks:allow
    texto = (
        f"DATABASE_URL=postgresql://estrela:{fake}@db:5432/x token=abcdef123456"  # gitleaks:allow
    )
    saida = agente.redigir(texto, (fake,))
    assert "senha-secreta" not in saida
    assert "abcdef123456" not in saida
    assert "postgresql://estrela:" in saida  # continua legível para um humano


# ===========================================================================
# Parsing dos labels remotos (usado para popular a allowlist)
# ===========================================================================


def test_labels_de_config_aceita_imagem_de_uma_plataforma() -> None:
    bruto = {"config": {"Labels": {"a": "1"}}}
    assert agente._labels_de_config(bruto) == {"a": "1"}


def test_labels_de_config_prefere_linux_amd64_em_imagem_multiplataforma() -> None:
    bruto = {
        "linux/arm64": {"config": {"Labels": {"plataforma": "arm"}}},
        "linux/amd64": {"config": {"Labels": {"plataforma": "amd"}}},
    }
    assert agente._labels_de_config(bruto) == {"plataforma": "amd"}


def test_labels_de_config_devolve_none_quando_nao_sabe() -> None:
    """`None` aqui vira NULL na allowlist, e NULL a aba /deploy já mostra como versão
    arriscada. O agente não inventa alembic_head nem rollback_seguro."""
    assert agente._labels_de_config(None) is None
    assert agente._labels_de_config({"config": {}}) == {}
    assert agente._labels_de_config({"linux/amd64": {"config": {}}}) is None


def test_rollback_do_label_nao_chuta() -> None:
    assert agente._rollback_do_label("true") is True
    assert agente._rollback_do_label("false") is False
    assert agente._rollback_do_label(None) is None
    assert agente._rollback_do_label("talvez") is None
    assert agente._rollback_do_label("") is None


# --- Folga da janela (regressão do "deploy às 07:59 termina às 09:30") -------


def _sp(ano: int, mes: int, dia: int, h: int, m: int = 0) -> datetime:
    """Um instante no fuso do cliente, que é onde a janela é decidida."""
    return datetime(ano, mes, dia, h, m, tzinfo=timezone(timedelta(hours=-3)))


def test_fim_da_janela_e_o_proximo_inicio_de_expediente() -> None:
    # Terça 20:00 -> a janela fecha às 08:00 de quarta.
    fim = agente.fim_da_janela(_sp(2026, 7, 21, 20, 0), JANELA)
    assert fim is not None
    assert (fim.hour, fim.day) == (8, 22)


def test_fim_da_janela_none_dentro_do_expediente() -> None:
    assert agente.fim_da_janela(_sp(2026, 7, 21, 14, 0), JANELA) is None


def test_janela_com_folga_recusa_a_beira_do_expediente() -> None:
    """O bug que isto trava: `dentro_da_janela` diz "pode" às 07:59, mas o pior caso do
    deploy (~100 min) terminaria depois das 9h, com a loja vendendo."""
    quase = _sp(2026, 7, 21, 7, 59)  # terça, 1 min de janela restante
    assert agente.dentro_da_janela(quase, JANELA) is True, "ainda é janela..."
    assert agente.janela_com_folga(quase, JANELA, agente.MARGEM_JANELA_SEG) is False, (
        "...mas não cabe um deploy inteiro antes do expediente"
    )


def test_janela_com_folga_aceita_a_noite() -> None:
    noite = _sp(2026, 7, 21, 20, 0)  # terça 20h: ~12h até as 08:00
    assert agente.janela_com_folga(noite, JANELA, agente.MARGEM_JANELA_SEG) is True


def test_janela_com_folga_e_falsa_no_expediente() -> None:
    assert agente.janela_com_folga(_sp(2026, 7, 21, 14, 0), JANELA, 60) is False


def test_margem_cobre_o_pior_caso_do_deploy() -> None:
    """A margem tem de ser >= a soma real dos timeouts, senão ela é decorativa."""
    pior_caso = (
        agente.TIMEOUT_PULL
        + agente.TIMEOUT_BACKUP
        + agente.TIMEOUT_PREFLIGHT
        + agente.TIMEOUT_UP
        + agente.TIMEOUT_GATE_SEG
    )
    assert agente.MARGEM_JANELA_SEG >= pior_caso
