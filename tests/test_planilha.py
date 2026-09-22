"""Pedido em planilha: criar já confirmado, abrir na lista e editar rascunho célula a célula.

Mesmo truque do `client_admin` da colagem: `get_db` sobrescrito pela Session do fixture,
para o teste criar os próprios produtos e nada sobrar no banco de dev.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.models.enums import EstoqueModo, StatusPedido
from app.models.pedido import Pedido
from app.models.produto import Produto, ProdutoVariacao
from app.schemas.pedido import (
    ConsultaPlanilha,
    LinhaPlanilha,
    PedidoCompletoCreate,
    PedidoPlanilhaSalvar,
)
from app.services.pedido_service import pedido_service
from app.services.planilha_service import planilha_service


@pytest.fixture
def client_admin(db, usuario_admin):
    from app.deps.auth import get_current_user
    from app.deps.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: usuario_admin
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _produto(db, *, cores=("azul",), fisico=1000, modo=EstoqueModo.EXATO, pouca="10.00"):
    p = Produto(
        codigo=f"PL{uuid.uuid4().hex[:8].upper()}",
        descricao="GARRAFA DE PLANILHA",
        preco_pouca_qtd=Decimal(pouca),
        preco_muita_qtd=Decimal("8.00"),
        qtd_corte_atacado=50,
        preco_minimo=Decimal("0.00"),
    )
    db.add(p)
    db.flush()
    for cor in cores:
        db.add(
            ProdutoVariacao(
                produto_id=p.id,
                cor=cor,
                estoque_modo=modo,
                estoque_fisico=fisico,
                estoque_reservado=0,
            )
        )
    db.flush()
    db.refresh(p)
    return p


def _linha(**kw) -> LinhaPlanilha:
    base = {"codigo": "", "descricao": "", "qtd": 1, "preco_unit": Decimal("10.00")}
    base.update(kw)
    return LinhaPlanilha(**base)


# --------------------------------------------------------------------- consulta
def test_codigo_de_uma_cor_casa_com_preco_da_faixa(db, usuario_admin) -> None:
    p = _produto(db)
    [r] = planilha_service.resolver(db, [ConsultaPlanilha(codigo=p.codigo, qtd=2)], "admin")
    assert r.situacao == "ok"
    assert r.variacao_id == p.variacoes[0].id
    assert r.preco_centavos == 1000  # varejo: abaixo do corte de atacado


def test_quantidade_de_atacado_troca_o_preco(db, usuario_admin) -> None:
    p = _produto(db)
    variacao = p.variacoes[0]
    [r] = planilha_service.resolver(
        db, [ConsultaPlanilha(variacao_id=variacao.id, qtd=60)], "admin"
    )
    assert r.situacao == "ok"
    assert r.preco_centavos == 800  # 60 >= corte de 50: preço de atacado


def test_codigo_de_varias_cores_pede_para_escolher(db, usuario_admin) -> None:
    p = _produto(db, cores=("azul", "preto"))
    [r] = planilha_service.resolver(db, [ConsultaPlanilha(codigo=p.codigo)], "admin")
    assert r.situacao == "duvida"
    assert {o.cor for o in r.opcoes} == {"azul", "preto"}


def test_codigo_desconhecido_vira_nada(db, usuario_admin) -> None:
    [r] = planilha_service.resolver(db, [ConsultaPlanilha(codigo="NAOEXISTE999")], "admin")
    assert r.situacao == "nada"


# --------------------------------------------------------------------- criar
def test_criar_pela_planilha_confirma_e_reserva(db, usuario_admin) -> None:
    p = _produto(db)
    variacao = p.variacoes[0]
    dados = PedidoPlanilhaSalvar(
        cliente_nome="BRUNA",
        itens=[
            _linha(variacao_id=variacao.id, codigo=p.codigo, qtd=5, preco_unit=Decimal("21")),
            _linha(codigo="X-1", descricao="CAIXA AVULSA", qtd=2, preco_unit=Decimal("2.50")),
        ],
    )
    pedido, aviso = planilha_service.criar(db, dados, usuario_admin.id, "admin")

    assert aviso is None
    assert pedido.status == StatusPedido.CONFIRMADO
    assert pedido.numero is not None
    assert pedido.total == Decimal("110.00")  # 5 × 21 + 2 × 2,50 — o preço da célula vale
    assert pedido.cliente_nome == "BRUNA"
    db.refresh(variacao)
    assert variacao.estoque_reservado == 5  # avulso não reserva nada
    avulso = next(i for i in pedido.itens if i.produto_variacao_id is None)
    assert (avulso.codigo, avulso.descricao) == ("X-1", "CAIXA AVULSA")


def test_linha_so_com_codigo_casa_no_servidor(db, usuario_admin) -> None:
    p = _produto(db)
    dados = PedidoPlanilhaSalvar(itens=[_linha(codigo=p.codigo, qtd=3)])
    pedido, _ = planilha_service.criar(db, dados, usuario_admin.id, "admin")
    assert pedido.itens[0].produto_variacao_id == p.variacoes[0].id


def test_sem_estoque_exato_fica_rascunho_com_aviso(db, usuario_admin) -> None:
    p = _produto(db, fisico=1)
    dados = PedidoPlanilhaSalvar(itens=[_linha(variacao_id=p.variacoes[0].id, qtd=10)])
    pedido, aviso = planilha_service.criar(db, dados, usuario_admin.id, "admin")

    assert aviso and "rascunho" in aviso
    assert pedido.status == StatusPedido.RASCUNHO
    assert pedido.numero is None
    assert len(pedido.itens) == 1  # o trabalho digitado não se perde
    db.refresh(p.variacoes[0])
    assert p.variacoes[0].estoque_reservado == 0  # a reserva pela metade voltou


# --------------------------------------------------------------------- rascunho
def _rascunho(db, usuario, produto) -> Pedido:
    """Rascunho pelo caminho da tela nova (a planilha nova já nasceria confirmada)."""
    return pedido_service.criar_completo(
        db,
        PedidoCompletoCreate(
            itens=[
                {"tipo": "catalogo", "variacao_id": produto.variacoes[0].id, "qtd": 2},
                {"tipo": "avulso", "nome": "BRINDE", "qtd": 1, "preco_unit": "0"},
            ]
        ),
        usuario.id,
        "admin",
    )


def test_salvar_rascunho_atualiza_troca_e_apaga(db, usuario_admin) -> None:
    p = _produto(db)
    outro = _produto(db)
    pedido = _rascunho(db, usuario_admin, p)
    catalogo = next(i for i in pedido.itens if i.produto_variacao_id)
    brinde_id = next(i.id for i in pedido.itens if i.produto_variacao_id is None)

    dados = PedidoPlanilhaSalvar(
        cliente_nome="NOVO NOME",
        itens=[
            # mesma linha, só quantidade e valor
            _linha(
                item_id=catalogo.id,
                identidade_mudou=False,
                variacao_id=catalogo.produto_variacao_id,
                qtd=7,
                preco_unit=Decimal("9.50"),
            ),
            # linha nova
            _linha(variacao_id=outro.variacoes[0].id, qtd=1, preco_unit=Decimal("3")),
        ],
        # o brinde sumiu da planilha: é apagado
    )
    salvo, aviso = planilha_service.salvar_rascunho(db, pedido.id, dados, usuario_admin.id, "admin")

    assert aviso is None
    assert salvo.status == StatusPedido.RASCUNHO
    ids = {i.id for i in salvo.itens}
    assert catalogo.id in ids and brinde_id not in ids
    mantido = next(i for i in salvo.itens if i.id == catalogo.id)
    assert (mantido.qtd, mantido.preco_unit, mantido.subtotal) == (
        7,
        Decimal("9.50"),
        Decimal("66.50"),
    )
    assert salvo.total == Decimal("69.50")
    assert salvo.cliente_nome == "NOVO NOME"


def test_salvar_e_confirmar_rascunho(db, usuario_admin) -> None:
    p = _produto(db)
    pedido = _rascunho(db, usuario_admin, p)
    dados = PedidoPlanilhaSalvar(
        confirmar=True,
        itens=[_linha(variacao_id=p.variacoes[0].id, qtd=4, preco_unit=Decimal("10"))],
    )
    salvo, aviso = planilha_service.salvar_rascunho(db, pedido.id, dados, usuario_admin.id, "admin")
    assert aviso is None
    assert salvo.status == StatusPedido.CONFIRMADO
    assert salvo.numero is not None


def test_montar_pedido_para_a_planilha(db, usuario_admin) -> None:
    p = _produto(db)
    pedido = _rascunho(db, usuario_admin, p)
    out = planilha_service.montar(pedido)
    assert out.editavel is True
    assert out.numero == "RASCUNHO"
    assert out.cliente == ""  # sem nome: célula vazia para digitar, não "CONSUMIDOR"
    dados = out.model_dump(by_alias=True)
    assert {"pedidoId", "itens", "descontoCentavos"} <= dados.keys()
    assert dados["itens"][0]["precoCentavos"] == 1000


# --------------------------------------------------------------------- HTTP
def test_tela_da_planilha_e_botao_na_lista(client_admin) -> None:
    assert 'href="/pedidos/planilha"' in client_admin.get("/pedidos").text
    tela = client_admin.get("/pedidos/planilha").text
    assert "PedidoPlanilha.editor" in tela
    assert "pedido_planilha.js" in tela


def test_lista_em_modo_planilha(client_admin, db, usuario_admin) -> None:
    p = _produto(db)
    pedido = _rascunho(db, usuario_admin, p)
    tela = client_admin.get("/pedidos?visao=planilha").text
    assert 'id="tabela-planilha"' in tela
    assert f'id="pp-{pedido.id}"' in tela
    assert "TOTAL GERAL" in tela

    fragmento = client_admin.get("/pedidos/lista?visao=planilha").text
    assert 'id="tabela-planilha"' in fragmento

    planilha = client_admin.get(f"/pedidos/{pedido.id}/planilha").text
    assert "PedidoPlanilha.editor" in planilha

    linha = client_admin.get(f"/pedidos/{pedido.id}/linha-planilha?aberto=1").text
    assert f'id="pp-{pedido.id}"' in linha and "aberto: true" in linha


def test_http_resolver_e_criar(client_admin, db) -> None:
    p = _produto(db)
    r = client_admin.post(
        "/pedidos/planilha/resolver", json={"linhas": [{"codigo": p.codigo, "qtd": 1}]}
    ).json()
    assert r["ok"] and r["linhas"][0]["situacao"] == "ok"

    criado = client_admin.post(
        "/pedidos/planilha",
        json={
            "cliente_nome": "MARIA",
            "itens": [
                {
                    "variacao_id": r["linhas"][0]["variacao_id"],
                    "codigo": p.codigo,
                    "qtd": 2,
                    "preco_unit": "12.00",
                }
            ],
        },
    ).json()
    assert criado["ok"] and criado["numero"] is not None
    assert db.get(Pedido, criado["pedido_id"]).total == Decimal("24.00")


def test_http_planilha_invalida_responde_200_com_erro(client_admin) -> None:
    r = client_admin.post("/pedidos/planilha", json={"itens": []})
    assert r.status_code == 200
    assert r.json()["ok"] is False and r.json()["erro"]


def test_http_nao_edita_pedido_confirmado(client_admin, db, usuario_admin) -> None:
    p = _produto(db)
    pedido, _ = planilha_service.criar(
        db,
        PedidoPlanilhaSalvar(itens=[_linha(variacao_id=p.variacoes[0].id)]),
        usuario_admin.id,
        "admin",
    )
    r = client_admin.post(
        f"/pedidos/{pedido.id}/planilha",
        json={"itens": [{"descricao": "X", "qtd": 1, "preco_unit": "1"}]},
    ).json()
    assert r["ok"] is False and "rascunho" in r["erro"]


def test_cor_na_descricao_escolhe_a_variacao(db, usuario_admin) -> None:
    p = _produto(db, cores=("azul", "preto"))
    preto = next(v for v in p.variacoes if v.cor == "preto")
    [r] = planilha_service.resolver(
        db, [ConsultaPlanilha(codigo=p.codigo, descricao="GARRAFA PRETO")], "admin"
    )
    assert r.situacao == "ok" and r.variacao_id == preto.id


def test_codigo_inexistente_nao_casa_pela_descricao(db, usuario_admin) -> None:
    p = _produto(db)
    [r] = planilha_service.resolver(
        db, [ConsultaPlanilha(codigo="NAOEXISTE999", descricao=p.descricao)], "admin"
    )
    assert r.situacao == "nada"
