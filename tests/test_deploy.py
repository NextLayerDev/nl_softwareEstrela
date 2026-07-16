"""Aba /deploy: RBAC, sondas de saúde, histórico e o cliente do GitHub.

Nenhum teste aqui toca a rede: o respx intercepta o transporte do httpx. Um teste que
fizesse rede de verdade quebraria no CI (sem egress) e mentiria sobre o comportamento
offline, que é justamente o caso normal do servidor da cliente.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.integracoes import github
from app.main import app
from app.models.deploy import CiStatusCache, Deploy
from app.services.ci_service import ci_service
from app.services.saude_service import (
    SCHEMA_EM_DIA,
    saude_service,
)

PERFIS = ["admin", "vendedor", "financeiro", "funcionario", "dev"]
ROTAS = ["/deploy", "/deploy/status", "/deploy/saude", "/deploy/historico", "/deploy/ci"]


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


def test_deploy_nao_aparece_no_menu_de_ninguem() -> None:
    """Ferramenta de manutenção: só por URL direta, nem para o dev."""
    for perfil in PERFIS:
        assert "Status do Deploy" not in _login(perfil).get("/").text


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
