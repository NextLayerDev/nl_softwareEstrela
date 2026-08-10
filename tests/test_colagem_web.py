"""Testes HTTP da colagem: as duas telas, o contrato OOB e a regressão do #bloco-itens.

Usam o mesmo truque do `client_vendedor` do `test_pedidos.py` — `get_db` sobrescrito
pela Session do fixture — para que o teste crie os próprios produtos e nada sobre no
banco de dev depois da rodada.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.models.cliente import Cliente
from app.models.enums import EstoqueModo
from app.models.produto import Produto, ProdutoVariacao


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


@pytest.fixture
def produto(db):
    """Produto de uma cor só e sem piso: a colagem resolve sozinha, sem virar dúvida."""
    p = Produto(
        codigo=f"WEB{uuid.uuid4().hex[:8].upper()}",
        descricao="PRODUTO DE COLAGEM WEB",
        preco_pouca_qtd=Decimal("10.00"),
        preco_muita_qtd=Decimal("8.00"),
        preco_minimo=Decimal("0.00"),
    )
    db.add(p)
    db.flush()
    db.add(
        ProdutoVariacao(
            produto_id=p.id,
            cor="azul",
            estoque_modo=EstoqueModo.EXATO,
            estoque_fisico=1000,
            estoque_reservado=0,
        )
    )
    db.flush()
    return p


def _tsv(*linhas: str) -> str:
    return "\n".join(
        [
            "07/08/2026\t\t265550\t\t",
            "CODIGO\tDESCIRCAO\tQUANT.\tV. UNIT.\tSUB. TOTAL",
            *linhas,
            "\t\t\tTOTAL\tR$ 0,00",
        ]
    )


def _colar(client, texto: str):
    return client.post("/pedidos/colar", data={"texto": texto}, headers={"HX-Request": "true"})


# --------------------------------------------------------------------- telas
def test_novo_pedido_mostra_o_painel_de_colar(client_admin) -> None:
    t = client_admin.get("/pedidos/novo").text
    assert 'id="painel-colagem"' in t
    assert 'hx-post="/pedidos/colar"' in t
    # o painel reaproveita os campos de cliente do formulário ao lado
    assert 'hx-include="#form-cliente"' in t
    assert 'id="form-cliente"' in t


def test_colagem_que_casa_tudo_manda_direto_para_o_pedido(client_admin, produto) -> None:
    r = _colar(client_admin, _tsv(f"{produto.codigo}\tqualquer\t2\tR$ 9,00\t"))

    assert r.status_code == 200  # nunca 4xx: o htmx descarta corpo de resposta de erro
    destino = r.headers.get("HX-Redirect", "")
    assert re.fullmatch(r"/pedidos/\d+", destino), r.text

    detalhe = client_admin.get(destino).text
    assert "R$ 18,00" in detalhe  # 2 × 9,00 — o preço colado é o que vale


def test_colagem_com_pendencia_para_e_mostra_o_que_faltou(client_admin, produto) -> None:
    r = _colar(
        client_admin,
        _tsv(f"{produto.codigo}\tx\t1\tR$ 9,00\t", "CODIGO-QUE-NAO-EXISTE\t\t3\tR$ 1,00\t"),
    )

    assert r.status_code == 200
    assert "HX-Redirect" not in r.headers  # tem o que conferir: não redireciona
    assert "1 linha não entrou" in r.text
    assert "código não encontrado" in r.text
    assert "Abrir pedido #" in r.text
    assert "1 linha virou item" in r.text


def test_colagem_vazia_nao_estoura_e_explica(client_admin) -> None:
    r = _colar(client_admin, "   ")
    assert r.status_code == 200
    assert "Não encontrei linhas de produto" in r.text


def test_cliente_digitado_ao_lado_vale_no_pedido_colado(client_admin, produto) -> None:
    """O `hx-include` do painel manda os campos de cliente junto com o texto."""
    r = client_admin.post(
        "/pedidos/colar",
        data={
            "texto": _tsv(f"{produto.codigo}\tx\t1\tR$ 9,00\t"),
            "cliente_nome": "Maria do Balcão",
            "cliente_telefone": "11 97777-6666",
        },
        headers={"HX-Request": "true"},
    )
    detalhe = client_admin.get(r.headers["HX-Redirect"]).text
    assert "Maria do Balcão" in detalhe


def test_colar_dentro_do_rascunho_responde_tudo_oob(client_admin, produto) -> None:
    criado = _colar(client_admin, _tsv(f"{produto.codigo}\tx\t1\tR$ 9,00\t"))
    pedido_url = criado.headers["HX-Redirect"]

    assert f'hx-post="{pedido_url}/colar"' in client_admin.get(pedido_url).text

    r = client_admin.post(
        f"{pedido_url}/colar",
        data={"texto": _tsv(f"{produto.codigo}\tx\t2\tR$ 9,00\t")},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    # o form usa hx-swap="none", então TUDO na resposta precisa vir OOB
    assert 'id="painel-colagem" hx-swap-oob="true"' in r.text
    assert 'id="bloco-itens" hx-swap-oob="true"' in r.text
    assert 'id="acoes-pedido"' in r.text
    assert "R$ 27,00" in r.text  # 1×9 + 2×9, somados na mesma linha


def test_painel_de_colagem_pode_ser_recarregado_vazio(client_admin, produto) -> None:
    criado = _colar(client_admin, _tsv(f"{produto.codigo}\tx\t1\tR$ 9,00\t"))
    pedido_id = criado.headers["HX-Redirect"].rsplit("/", 1)[1]

    r = client_admin.get(f"/pedidos/{pedido_id}/colagem", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert 'id="painel-colagem"' in r.text
    assert f'hx-post="/pedidos/{pedido_id}/colar"' in r.text


# --------------------------------------------------------------------- regressão
def test_rotas_antigas_de_item_nao_marcam_o_bloco_como_oob(client_admin, produto) -> None:
    """A flag `oob_bloco` é separada da `oob` por um motivo.

    As rotas de item/desconto trocam o #bloco-itens pelo hx-target (`outerHTML`), e o
    htmx REMOVE do conteúdo qualquer nó marcado como OOB antes do swap principal —
    marcá-lo ali apagaria a tabela da tela.
    """
    criado = _colar(client_admin, _tsv(f"{produto.codigo}\tx\t1\tR$ 9,00\t"))
    pedido_url = criado.headers["HX-Redirect"]

    r = client_admin.post(
        f"{pedido_url}/desconto", data={"desconto": "1,00"}, headers={"HX-Request": "true"}
    )
    assert r.status_code == 200
    assert 'id="bloco-itens" class=' in r.text
    assert 'id="bloco-itens" hx-swap-oob' not in r.text
    assert 'hx-swap-oob="true"' in r.text  # o _acoes.html continua vindo OOB


# --------------------------------------------------------------------- planilha do dia
@pytest.fixture
def outro_produto(db):
    p = Produto(
        codigo=f"WEB{uuid.uuid4().hex[:8].upper()}",
        descricao="SEGUNDO PRODUTO DE COLAGEM WEB",
        preco_pouca_qtd=Decimal("10.00"),
        preco_muita_qtd=Decimal("8.00"),
        preco_minimo=Decimal("0.00"),
    )
    db.add(p)
    db.flush()
    db.add(
        ProdutoVariacao(
            produto_id=p.id,
            cor="preto",
            estoque_modo=EstoqueModo.EXATO,
            estoque_fisico=1000,
            estoque_reservado=0,
        )
    )
    db.flush()
    return p


def _bloco(*linhas: str, codigo_cliente: str, data: str = "07/08/2026") -> str:
    return "\n".join(
        [
            f"{data}\t\t{codigo_cliente}\t\t",
            "CODIGO\tDESCIRCAO\tQUANT.\tV. UNIT.\tSUB. TOTAL",
            *linhas,
            "\t\t\tTOTAL\tR$ 0,00",
        ]
    )


def test_planilha_do_dia_cria_um_pedido_por_bloco(client_admin, produto, outro_produto) -> None:
    r = _colar(
        client_admin,
        _bloco(f"{produto.codigo}\tx\t2\tR$ 9,00\t", codigo_cliente="111")
        + "\n"
        + _bloco(f"{outro_produto.codigo}\tx\t3\tR$ 4,00\t", codigo_cliente="222"),
    )

    assert r.status_code == 200
    # Não dá para redirecionar para dois pedidos: a tela de chegada é o resumo do lote.
    assert "HX-Redirect" not in r.headers
    assert "2 pedidos criados" in r.text
    assert "R$ 18,00" in r.text
    assert "R$ 12,00" in r.text
    assert r.text.count("Abrir pedido") == 2


def test_bloco_com_codigo_conhecido_mostra_o_cadastro(client_admin, db, produto, outro_produto):
    db.add(Cliente(nome="LOJA DO ZE", codigo="265550"))
    db.flush()

    r = _colar(
        client_admin,
        _bloco(f"{produto.codigo}\tx\t1\tR$ 9,00\t", codigo_cliente="265550")
        + "\n"
        + _bloco(f"{outro_produto.codigo}\tx\t1\tR$ 9,00\t", codigo_cliente="000000"),
    )

    assert "LOJA DO ZE" in r.text
    assert "cadastro" in r.text
    assert "código 000000 não achado" in r.text


def test_um_bloco_so_continua_redirecionando(client_admin, produto) -> None:
    """A planilha de um pedido só não pode ganhar uma tela de resumo no caminho."""
    r = _colar(client_admin, _bloco(f"{produto.codigo}\tx\t2\tR$ 9,00\t", codigo_cliente="111"))
    assert re.fullmatch(r"/pedidos/\d+", r.headers.get("HX-Redirect", ""))
