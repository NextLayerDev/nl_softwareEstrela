"""Testes da camada de realtime.

O listener NÃO sobe durante os testes (o lifespan o pula quando o pytest está carregado), e
o `emitir` vira um SELECT inócuo que o rollback do SAVEPOINT descarta. Então aqui testamos as
duas peças que quebram calado: o RBAC do fan-out e a autenticação do socket.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.core import eventos
from app.main import app
from app.models.enums import EstoqueModo, OrigemMov, RotuloAprox
from app.models.usuario import Usuario
from app.realtime.manager import GerenciadorConexoes
from app.services.estoque_service import estoque_service


# =============================================================== fan-out (RBAC)
class _WSFake:
    """WebSocket de mentira: guarda o que foi enviado e se foi fechado."""

    def __init__(self) -> None:
        self.enviados: list[dict] = []
        self.fechado_com: int | None = None

    async def send_json(self, dados: dict) -> None:
        self.enviados.append(dados)

    async def close(self, code: int = 1000) -> None:
        self.fechado_com = code


def _envelope(tipo: str, **kw) -> dict:
    base = {
        "tipo": tipo,
        "audiencia": [],
        "vendedor_id": None,
        "target_usuario_id": None,
        "silencioso": False,
        "dados": {},
    }
    base.update(kw)
    return base


def _montar() -> tuple[GerenciadorConexoes, dict[str, _WSFake]]:
    """Um gerenciador com um terminal de cada perfil (vendedor #7 e #9)."""
    ger = GerenciadorConexoes()
    ws = {
        "admin": _WSFake(),
        "financeiro": _WSFake(),
        "funcionario": _WSFake(),
        "vendedor7": _WSFake(),
        "vendedor9": _WSFake(),
    }
    ger.registrar(ws["admin"], 1, "admin")
    ger.registrar(ws["financeiro"], 2, "financeiro")
    ger.registrar(ws["funcionario"], 3, "funcionario")
    ger.registrar(ws["vendedor7"], 7, "vendedor")
    ger.registrar(ws["vendedor9"], 9, "vendedor")
    return ger, ws


def test_fan_out_entrega_para_a_audiencia_toda():
    ger, ws = _montar()
    env = _envelope("estoque.movimentado", audiencia=list(eventos.TODOS))
    asyncio.run(ger.fan_out(env))
    for nome, fake in ws.items():
        assert len(fake.enviados) == 1, f"{nome} deveria ter recebido"


def test_fan_out_respeita_o_perfil():
    """Evento de financeiro não pode chegar no terminal do funcionário."""
    ger, ws = _montar()
    asyncio.run(ger.fan_out(_envelope("conta.baixada", audiencia=list(eventos.FIN_AUD))))
    assert ws["admin"].enviados
    assert ws["financeiro"].enviados
    assert not ws["funcionario"].enviados
    assert not ws["vendedor7"].enviados


def test_fan_out_entrega_ao_dono_mesmo_fora_da_audiencia():
    """O vendedor dono vê o próprio pedido; o outro vendedor não vê nada."""
    ger, ws = _montar()
    env = _envelope("pedido.faturado", audiencia=list(eventos.FIN_AUD), vendedor_id=7)
    asyncio.run(ger.fan_out(env))
    assert ws["vendedor7"].enviados, "o dono do pedido deveria receber"
    assert not ws["vendedor9"].enviados, "vendedor não pode ver pedido de outro vendedor"
    assert ws["financeiro"].enviados


def test_fan_out_dirigido_ignora_audiencia():
    """target_usuario_id vence tudo: só o alvo recebe, ninguém mais."""
    ger, ws = _montar()
    env = _envelope("qualquer", audiencia=list(eventos.TODOS), target_usuario_id=7)
    asyncio.run(ger.fan_out(env))
    assert ws["vendedor7"].enviados
    assert not ws["admin"].enviados
    assert not ws["vendedor9"].enviados


def test_desconectar_usuario_derruba_so_os_sockets_dele():
    ger, ws = _montar()
    asyncio.run(ger.desconectar_usuario(7))
    assert ws["vendedor7"].fechado_com == 4001
    assert ws["vendedor9"].fechado_com is None
    assert ws["admin"].fechado_com is None
    assert ger.total == 4


def test_fan_out_descarta_socket_morto():
    """Um socket que estourou no envio sai do registro, sem derrubar os outros."""
    ger, ws = _montar()

    async def explode(_):
        raise RuntimeError("socket caiu")

    ws["admin"].send_json = explode
    asyncio.run(ger.fan_out(_envelope("x", audiencia=list(eventos.TODOS))))
    assert ger.total == 4
    assert ws["financeiro"].enviados


# ==================================================================== emitir
class _DBFake:
    def __init__(self) -> None:
        self.execucoes: list[tuple] = []

    def execute(self, stmt, params=None):
        self.execucoes.append((stmt, params))


def test_emitir_monta_o_envelope_no_canal_certo():
    db = _DBFake()
    eventos.emitir(db, "teste.tipo", {"a": 1}, audiencia=eventos.FIN_AUD, vendedor_id=5)
    assert len(db.execucoes) == 1
    _, params = db.execucoes[0]
    assert params["canal"] == "estrela_eventos"
    env = json.loads(params["carga"])
    assert env["tipo"] == "teste.tipo"
    assert env["audiencia"] == list(eventos.FIN_AUD)
    assert env["vendedor_id"] == 5
    assert env["dados"] == {"a": 1}


def test_emitir_respeita_o_desligamento(monkeypatch):
    monkeypatch.setattr(eventos.settings, "REALTIME_ENABLED", False)
    db = _DBFake()
    eventos.emitir(db, "teste", {}, audiencia=eventos.TODOS)
    assert db.execucoes == []


def test_emitir_degrada_payload_gigante():
    """Acima do limite do NOTIFY, manda o envelope sem 'dados' em vez de estourar."""
    db = _DBFake()
    eventos.emitir(db, "teste", {"lixo": "x" * 9000}, audiencia=eventos.TODOS)
    env = json.loads(db.execucoes[0][1]["carga"])
    assert env["truncado"] is True
    assert env["dados"] == {}
    assert env["tipo"] == "teste"


def test_emitir_nunca_levanta():
    """Realtime é best-effort: falha de emissão não pode derrubar a regra de negócio."""

    class _DBQuebrado:
        def execute(self, *_a, **_kw):
            raise RuntimeError("banco fora")

    eventos.emitir(_DBQuebrado(), "teste", {}, audiencia=eventos.TODOS)  # não levanta


# ========================================================= emits dos services
@pytest.fixture
def espiao(monkeypatch) -> list[dict]:
    """Captura os emits sem depender de entrega real pelo Postgres."""
    capturados: list[dict] = []

    def _fake(db, tipo, dados, **kw):
        capturados.append({"tipo": tipo, "dados": dados, **kw})

    monkeypatch.setattr(eventos, "emitir", _fake)
    return capturados


def _variacao(db: Session, minimo: int = 5, fisico: int = 100):
    from app.models.produto import Produto, ProdutoVariacao

    p = Produto(codigo=f"RT{id(db) % 10000}", descricao="Produto de teste realtime")
    db.add(p)
    db.flush()
    v = ProdutoVariacao(
        produto_id=p.id,
        cor="AZUL",
        estoque_fisico=fisico,
        estoque_reservado=0,
        estoque_minimo=minimo,
        estoque_modo=EstoqueModo.EXATO,
    )
    db.add(v)
    db.flush()
    return v


def test_entrada_emite_movimentacao_com_saldo_novo(db: Session, usuario_admin, espiao):
    v = _variacao(db, fisico=10)
    estoque_service.entrada(db, v, 5, usuario_admin.id)
    movs = [e for e in espiao if e["tipo"] == "estoque.movimentado"]
    assert len(movs) == 1
    assert movs[0]["dados"]["estoque_fisico"] == 15
    assert movs[0]["dados"]["tipo"] == "entrada"


def test_importacao_nao_emite_por_linha(db: Session, usuario_admin, espiao):
    """Carga em lote emitiria milhares de eventos; a carga manda só um resumo."""
    v = _variacao(db)
    estoque_service.entrada(db, v, 5, usuario_admin.id, origem=OrigemMov.IMPORTACAO)
    assert [e for e in espiao if e["tipo"] == "estoque.movimentado"] == []


def test_alerta_de_minimo_so_na_transicao(db: Session, usuario_admin, espiao):
    v = _variacao(db, minimo=5, fisico=10)

    # Ainda acima do mínimo: sem alerta.
    estoque_service.baixar(db, v, 2, usuario_admin.id, pedido_id=1)
    assert not [e for e in espiao if e["tipo"] == "estoque.alerta_minimo"]

    # Cruza para <= mínimo: alerta.
    estoque_service.baixar(db, v, 4, usuario_admin.id, pedido_id=1)
    alertas = [e for e in espiao if e["tipo"] == "estoque.alerta_minimo"]
    assert len(alertas) == 1
    assert alertas[0]["dados"]["estoque_fisico"] == 4

    # Já estava abaixo: NÃO repete o alerta a cada baixa.
    estoque_service.baixar(db, v, 1, usuario_admin.id, pedido_id=1)
    assert len([e for e in espiao if e["tipo"] == "estoque.alerta_minimo"]) == 1


def test_rotulo_acabou_dispara_alerta(db: Session, usuario_admin, espiao):
    v = _variacao(db, minimo=0, fisico=50)
    estoque_service.definir_aproximado(db, v, RotuloAprox.ACABOU, usuario_admin.id)
    assert [e for e in espiao if e["tipo"] == "estoque.alerta_minimo"]


def test_reset_de_senha_invalida_a_sessao(db: Session, usuario_vendedor, espiao):
    from app.services.usuario_service import usuario_service

    usuario_service.resetar_senha(db, usuario_vendedor.id, "NovaSenha!2026")
    invalidacoes = [e for e in espiao if e["tipo"] == "sessao.invalidada"]
    assert len(invalidacoes) == 1
    assert invalidacoes[0]["target_usuario_id"] == usuario_vendedor.id
    assert invalidacoes[0]["dados"]["usuario_id"] == usuario_vendedor.id
    # O payload jamais pode carregar o hash da senha.
    assert "senha_hash" not in json.dumps(invalidacoes[0]["dados"])


# ================================================================= auth do WS
def test_ws_recusa_sem_cookie():
    with TestClient(app) as client, pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws"):
            pass
    assert exc.value.code == 1008


def test_ws_recusa_token_invalido():
    client = TestClient(app)
    client.cookies.set("estrela_token", "nao-e-um-jwt")
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws"):
            pass
    assert exc.value.code == 1008


def test_ws_aceita_sessao_valida(db: Session, usuario_admin: Usuario, monkeypatch):
    """Com um token bom, o socket conecta e o usuário entra no registro do worker."""
    from app.core.security import criar_token
    from app.web.routes import realtime as rota_rt

    # O TestClient roda noutra Session; devolvemos o usuário do teste direto.
    monkeypatch.setattr(
        rota_rt, "_identificar", lambda _t: (usuario_admin.id, usuario_admin.perfil)
    )
    client = TestClient(app)
    client.cookies.set(
        "estrela_token", criar_token(usuario_admin.id, usuario_admin.perfil, extra={"tv": 0})
    )
    with client.websocket_connect("/ws"):
        assert rota_rt.manager.total == 1
    assert rota_rt.manager.total == 0, "a conexão deve sair do registro ao desconectar"


class _SessionFake:
    """Faz o _identificar usar a Session do teste.

    Sem isto ele abriria uma SessionLocal própria, que não enxerga o SAVEPOINT ainda não
    commitado — e os testes abaixo passariam por "usuário não existe" em vez de exercitarem
    de verdade a checagem de token_version.
    """

    def __init__(self, session: Session) -> None:
        self._s = session

    def __enter__(self) -> Session:
        return self._s

    def __exit__(self, *_a) -> bool:
        return False  # não fecha: a fixture cuida disso


@pytest.fixture
def identificar_com_db(db: Session, monkeypatch):
    from app.web.routes import realtime as rota_rt

    monkeypatch.setattr(rota_rt, "SessionLocal", lambda: _SessionFake(db))
    return rota_rt._identificar


def test_identificar_aceita_token_bom(usuario_admin: Usuario, identificar_com_db):
    from app.core.security import criar_token

    token = criar_token(
        usuario_admin.id, usuario_admin.perfil, extra={"tv": usuario_admin.token_version}
    )
    assert identificar_com_db(token) == (usuario_admin.id, "admin")


def test_identificar_recusa_token_version_antigo(
    db: Session, usuario_admin: Usuario, identificar_com_db
):
    """Senha resetada / perfil trocado invalida o token: o socket não pode abrir."""
    token_velho = criar_token_tv(usuario_admin, tv=1)
    usuario_admin.token_version = 3
    db.flush()
    assert identificar_com_db(token_velho) is None


def test_identificar_recusa_usuario_inativo(
    db: Session, usuario_admin: Usuario, identificar_com_db
):
    token = criar_token_tv(usuario_admin, tv=usuario_admin.token_version)
    usuario_admin.ativo = False
    db.flush()
    assert identificar_com_db(token) is None


def criar_token_tv(usuario: Usuario, tv: int) -> str:
    from app.core.security import criar_token

    return criar_token(usuario.id, usuario.perfil, extra={"tv": tv})
